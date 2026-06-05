"""Cloudflare R2 storage service.

Provides upload, download, existence checks, and key-building helpers
for the content-addressed restoration cache and per-job PDF outputs.

R2 key layout::

    {R2_FASTAPI_PREFIX}/
    ├── cache/{CACHE_MODEL_PREFIX}/     ← content-addressed restored images
    │   ├── {content_hash_1}.png
    │   └── {content_hash_2}.png
    ├── pdfs/{job_id}/                  ← merged PDF per job
    │   └── restored.pdf
    └── images/{date}/{uuid}-{name}     ← general-purpose uploads
"""

from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote
from uuid import uuid4
from warnings import catch_warnings, simplefilter

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.schemas.upload import ImageUploadResponse
from app.utils.files import safe_filename


class R2StorageService:

    # ------------------------------------------------------------------ #
    #  General-purpose image upload (routes_upload)                       #
    # ------------------------------------------------------------------ #

    async def upload_image(self, file: UploadFile) -> ImageUploadResponse:
        self._validate_configuration()

        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename.")

        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File is larger than {settings.MAX_UPLOAD_MB} MB.",
            )
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty.")

        content_type = await run_in_threadpool(self._detect_image_content_type, content)
        filename = safe_filename(file.filename)
        object_key = self._build_upload_key(filename)

        try:
            response = await run_in_threadpool(
                self._put_object, object_key, content, content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cloudflare R2 failed to store the image.",
            ) from exc

        bucket = settings.R2_BUCKET_NAME
        assert bucket is not None
        return ImageUploadResponse(
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            bucket=bucket,
            object_key=object_key,
            etag=response.get("ETag", "").strip('"') or None,
            public_url=self.build_public_url(object_key),
        )

    # ------------------------------------------------------------------ #
    #  Generic byte-level operations                                      #
    # ------------------------------------------------------------------ #

    async def upload_file_bytes(
        self,
        content: bytes,
        object_key: str,
        content_type: str,
    ) -> str | None:
        """Upload raw bytes to R2 with a pre-built object key.

        Returns the public URL if ``R2_PUBLIC_BASE_URL`` is configured.
        """

        self._validate_configuration()
        try:
            await run_in_threadpool(
                self._put_object, object_key, content, content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cloudflare R2 failed to upload the file.",
            ) from exc
        return self.build_public_url(object_key)

    async def object_exists(self, object_key: str) -> bool:
        """Return *True* if the object exists on R2 (HEAD request)."""

        self._validate_configuration()
        try:
            await run_in_threadpool(self._head_object, object_key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
            raise

    async def download_object(self, object_key: str) -> bytes:
        """Download an object from R2 and return its raw bytes."""

        self._validate_configuration()
        return await run_in_threadpool(self._get_object_bytes, object_key)

    # ------------------------------------------------------------------ #
    #  Key builders                                                       #
    # ------------------------------------------------------------------ #

    def build_cache_key(self, content_hash: str) -> str:
        """Content-addressed key: ``{prefix}/cache/{model}/{hash}.png``."""

        prefix = settings.R2_FASTAPI_PREFIX.strip("/")
        model = settings.CACHE_MODEL_PREFIX.strip("/")
        return "/".join(
            part for part in (prefix, "cache", model, f"{content_hash}.png") if part
        )

    def build_soft_cache_key(self, content_hash: str) -> str:
        """Content-addressed key for non-binarized restored images."""

        prefix = settings.R2_FASTAPI_PREFIX.strip("/")
        model = settings.CACHE_MODEL_PREFIX.strip("/")
        return "/".join(
            part
            for part in (prefix, "cache", model, "soft", f"{content_hash}.png")
            if part
        )

    def build_pdf_object_key(self, job_id: str, filename: str) -> str:
        """Per-job key for merged PDFs: ``{prefix}/pdfs/{job_id}/{filename}``."""

        prefix = settings.R2_FASTAPI_PREFIX.strip("/")
        return "/".join(
            part for part in (prefix, "pdfs", job_id, filename) if part
        )

    def build_public_url(self, object_key: str) -> str | None:
        """Build the full public URL for an R2 object key."""

        if not settings.R2_PUBLIC_BASE_URL:
            return None
        encoded_key = quote(object_key, safe="/")
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{encoded_key}"

    # ------------------------------------------------------------------ #
    #  Low-level S3-compatible helpers                                    #
    # ------------------------------------------------------------------ #

    def _get_client(self):
        return boto3.client(
            "s3",
            endpoint_url=self._endpoint_url(),
            region_name=settings.R2_REGION,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
        )

    def _put_object(
        self, object_key: str, content: bytes, content_type: str,
    ) -> dict:
        return self._get_client().put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
            Body=BytesIO(content),
            ContentLength=len(content),
            ContentType=content_type,
        )

    def _head_object(self, object_key: str) -> dict:
        return self._get_client().head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )

    def _get_object_bytes(self, object_key: str) -> bytes:
        response = self._get_client().get_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )
        return response["Body"].read()

    def _detect_image_content_type(self, content: bytes) -> str:
        try:
            with catch_warnings():
                simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    image.verify()
                    image_format = image.format
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported or invalid image file.",
            ) from exc

        content_type = Image.MIME.get(image_format or "")
        if content_type is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The image format does not have a supported MIME type.",
            )
        return content_type

    def _build_upload_key(self, filename: str) -> str:
        """Key for general-purpose uploads (not cache)."""

        date_path = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        prefix = settings.R2_FASTAPI_PREFIX.strip("/")
        unique_filename = f"{uuid4().hex}-{filename}"
        return "/".join(
            part for part in (prefix, "images", date_path, unique_filename) if part
        )

    def _endpoint_url(self) -> str:
        if settings.R2_ENDPOINT:
            return settings.R2_ENDPOINT.rstrip("/")
        assert settings.R2_ACCOUNT_ID is not None
        return f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    def _validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("R2_ACCESS_KEY_ID", settings.R2_ACCESS_KEY_ID),
                ("R2_SECRET_ACCESS_KEY", settings.R2_SECRET_ACCESS_KEY),
                ("R2_BUCKET_NAME", settings.R2_BUCKET_NAME),
            )
            if not value
        ]
        if not settings.R2_ENDPOINT and not settings.R2_ACCOUNT_ID:
            missing.append("R2_ENDPOINT or R2_ACCOUNT_ID")

        if missing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"R2 storage is not configured. Missing: {', '.join(missing)}.",
            )


r2_storage_service = R2StorageService()
