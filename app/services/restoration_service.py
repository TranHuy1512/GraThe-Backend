"""Single-image restoration service with R2 cache deduplication.

Flow: upload image → compute content hash → check R2 cache →
cache hit: return immediately | cache miss: call AI → upload to R2 → return.
"""

import logging
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from gradio_client import Client, handle_file
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.schemas.restoration import (
    RestorationResponse,
    RestoredFile,
    SoftRestorationResponse,
)
from app.services.r2_storage import r2_storage_service
from app.utils.files import safe_filename
from app.utils.image_hash import (
    RestorationParams,
    SoftRestorationParams,
    compute_content_hash,
    compute_soft_content_hash,
    read_image_as_png_bytes,
)

logger = logging.getLogger(__name__)


class RestorationService:

    async def restore_soft_upload(
        self,
        file: UploadFile,
        patch_size: int,
        batch_size: int,
        overlap: bool,
    ) -> SoftRestorationResponse:
        """Restore a single image without binarization.

        The returned output is independent of threshold, so callers can apply
        threshold previews later without running the AI model again.
        """

        self._validate_options(
            patch_size=patch_size,
            batch_size=batch_size,
            threshold=settings.DEFAULT_THRESHOLD,
        )
        filename, content, image = await self._read_valid_image_upload(file)

        params = SoftRestorationParams(
            patch_size=patch_size,
            overlap=overlap,
        )
        content_hash = await run_in_threadpool(compute_soft_content_hash, image, params)
        cache_key = r2_storage_service.build_soft_cache_key(content_hash)

        if await r2_storage_service.object_exists(cache_key):
            public_url = r2_storage_service.build_public_url(cache_key) or ""
            logger.info("SOFT CACHE HIT for %s (%s)", filename, content_hash[:12])
            return self._build_soft_response(
                content_hash=content_hash,
                input_filename=filename,
                url=public_url,
                cached=True,
            )

        logger.info(
            "SOFT CACHE MISS for %s (%s), calling AI...",
            filename,
            content_hash[:12],
        )
        temp_path = settings.UPLOAD_DIR / f"{content_hash}-{safe_filename(filename)}"
        temp_path.write_bytes(content)

        try:
            restored_path = await run_in_threadpool(
                self._restore_with_remote_model,
                temp_path,
                patch_size,
                batch_size,
                settings.DEFAULT_THRESHOLD,
                False,
                overlap,
            )
            restored_bytes = await run_in_threadpool(
                read_image_as_png_bytes, restored_path,
            )
            public_url = await r2_storage_service.upload_file_bytes(
                content=restored_bytes,
                object_key=cache_key,
                content_type="image/png",
            )

            return self._build_soft_response(
                content_hash=content_hash,
                input_filename=filename,
                url=public_url or "",
                cached=False,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    async def restore_upload(
        self,
        file: UploadFile,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> RestorationResponse:
        """Restore a single image, using R2 cache for deduplication."""

        self._validate_options(patch_size, batch_size, threshold)

        # ---- 1. Read & validate upload ----
        filename, content, image = await self._read_valid_image_upload(file)

        # ---- 2. Compute content hash ----
        params = RestorationParams(
            patch_size=patch_size,
            threshold=threshold,
            binarize_output=binarize_output,
            overlap=overlap,
        )
        content_hash = await run_in_threadpool(compute_content_hash, image, params)
        cache_key = r2_storage_service.build_cache_key(content_hash)

        # ---- 3. Check R2 cache ----
        if await r2_storage_service.object_exists(cache_key):
            public_url = r2_storage_service.build_public_url(cache_key) or ""
            logger.info("CACHE HIT for %s (%s)", filename, content_hash[:12])
            return self._build_response(
                content_hash=content_hash,
                input_filename=filename,
                url=public_url,
                cached=True,
            )

        # ---- 4. Cache miss → save temp file, call AI ----
        logger.info("CACHE MISS for %s (%s), calling AI...", filename, content_hash[:12])
        temp_path = settings.UPLOAD_DIR / f"{content_hash}-{safe_filename(filename)}"
        temp_path.write_bytes(content)

        try:
            restored_path = await run_in_threadpool(
                self._restore_with_remote_model,
                temp_path, patch_size, batch_size, threshold, binarize_output, overlap,
            )

            # ---- 5. Read restored image as PNG & upload to R2 cache ----
            restored_bytes = await run_in_threadpool(
                read_image_as_png_bytes, restored_path,
            )
            public_url = await r2_storage_service.upload_file_bytes(
                content=restored_bytes,
                object_key=cache_key,
                content_type="image/png",
            )

            return self._build_response(
                content_hash=content_hash,
                input_filename=filename,
                url=public_url or "",
                cached=False,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    async def _read_valid_image_upload(
        self, file: UploadFile,
    ) -> tuple[str, bytes, Image.Image]:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing filename.",
            )

        content = await file.read()
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File is larger than {settings.MAX_UPLOAD_MB} MB.",
            )

        try:
            image = Image.open(BytesIO(content))
            image.load()  # force full decode to validate
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported file type. Please upload a valid image.",
            ) from exc

        return file.filename, content, image

    def _build_response(
        self,
        content_hash: str,
        input_filename: str,
        url: str,
        cached: bool,
    ) -> RestorationResponse:
        return RestorationResponse(
            request_id=content_hash,
            input_filename=input_filename,
            input_type="image",
            total_pages=1,
            outputs=[
                RestoredFile(
                    page=1,
                    filename=f"{content_hash}.png",
                    url=url,
                    content_hash=content_hash,
                    cached=cached,
                )
            ],
        )

    def _build_soft_response(
        self,
        content_hash: str,
        input_filename: str,
        url: str,
        cached: bool,
    ) -> SoftRestorationResponse:
        return SoftRestorationResponse(
            request_id=content_hash,
            input_filename=input_filename,
            input_type="image",
            total_pages=1,
            soft_output=RestoredFile(
                page=1,
                filename=f"{content_hash}.png",
                url=url,
                content_hash=content_hash,
                cached=cached,
            ),
            recommended_threshold=settings.DEFAULT_THRESHOLD,
        )

    def _restore_with_remote_model(
        self,
        image_path: Path,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> Path:
        try:
            client = Client(settings.AI_RESTORATION_SPACE)
            result = client.predict(
                image=handle_file(str(image_path)),
                patch_size=patch_size,
                batch_size=batch_size,
                threshold=threshold,
                binarize_output=binarize_output,
                overlap=overlap,
                api_name=settings.AI_RESTORATION_API_NAME,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI restoration service failed to process the image.",
            ) from exc

        restored_path = self._extract_restored_path(result)
        if restored_path is None or not restored_path.exists():
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI restoration service did not return a restored image file.",
            )
        return restored_path

    def _extract_restored_path(self, result: object) -> Path | None:
        if isinstance(result, (str, Path)):
            return Path(result)
        if isinstance(result, dict):
            path = result.get("path") or result.get("name")
            return Path(path) if isinstance(path, str) else None
        if isinstance(result, (list, tuple)):
            for item in result:
                path = self._extract_restored_path(item)
                if path is not None:
                    return path
        return None

    def _validate_options(
        self, patch_size: int, batch_size: int, threshold: float,
    ) -> None:
        if patch_size not in {256, 384, 512, 768}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="patch_size must be one of: 256, 384, 512, 768.",
            )
        if batch_size < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="batch_size must be greater than or equal to 1.",
            )
        if not 0 <= threshold <= 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold must be between 0 and 1.",
            )


restoration_service = RestorationService()
