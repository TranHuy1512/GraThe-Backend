"""Single-image restoration service with R2 cache deduplication.

Flow: upload image → compute content hash → check R2 cache →
cache hit: return immediately | cache miss: call AI → upload to R2 → return.

After a successful restore the original image is uploaded to R2 under a
param-independent key (``originals/{pixel_hash}.png``) and a document
record is persisted to SQLite.
"""

import logging
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from gradio_client import Client, handle_file
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.schemas.restoration import (
    ConfirmThresholdRequest,
    ConfirmThresholdResponse,
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
    compute_pixel_hash,
    compute_soft_content_hash,
    image_to_png_bytes,
    read_image_as_png_bytes,
)

logger = logging.getLogger(__name__)

_ai_client: "Client | None" = None
_ai_client_lock = __import__("threading").Lock()


def _get_ai_client() -> "Client":
    global _ai_client
    if _ai_client is not None:
        return _ai_client
    with _ai_client_lock:
        if _ai_client is None:
            logger.info("Connecting to AI space %s ...", settings.AI_RESTORATION_SPACE)
            _ai_client = Client(settings.AI_RESTORATION_SPACE, hf_token=settings.HF_TOKEN)
            logger.info("AI client ready.")
    return _ai_client


class RestorationService:

    async def restore_soft_upload(
        self,
        file: UploadFile,
        patch_size: int,
        batch_size: int,
        overlap: bool,
        user_id: str = "",
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
        cache_key = r2_storage_service.build_soft_cache_key(content_hash, user_id)

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
        user_id: str = "",
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
        cache_key = r2_storage_service.build_cache_key(content_hash, user_id)

        # ---- 3. Check R2 cache ----
        if await r2_storage_service.object_exists(cache_key):
            public_url = r2_storage_service.build_public_url(cache_key) or ""
            logger.info("CACHE HIT for %s (%s)", filename, content_hash[:12])
            document_id = await self._persist_document_record(
                image=image,
                filename=filename,
                file_size=len(content),
                content_hash=content_hash,
                restored_url=public_url,
                params=params,
                user_id=user_id,
            )
            return self._build_response(
                content_hash=content_hash,
                input_filename=filename,
                url=public_url,
                cached=True,
                document_id=document_id,
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

            document_id = await self._persist_document_record(
                image=image,
                filename=filename,
                file_size=len(content),
                content_hash=content_hash,
                restored_url=public_url or "",
                params=params,
                user_id=user_id,
            )

            return self._build_response(
                content_hash=content_hash,
                input_filename=filename,
                url=public_url or "",
                cached=False,
                document_id=document_id,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    async def _persist_document_record(
        self,
        image: Image.Image,
        filename: str,
        file_size: int,
        content_hash: str,
        restored_url: str,
        params: RestorationParams,
        user_id: str = "",
    ) -> str | None:
        """Upload the original image to R2 and upsert a document record.

        Returns the document_id, or None if the operation fails (non-fatal).
        """
        try:
            from app.schemas.document import DocumentCreate
            from app.services.document_repository import document_repository

            # Check for existing record to avoid duplicates for this user
            existing = await document_repository.find_by_content_hash(content_hash, user_id)
            if existing is not None:
                return existing.id

            # Upload original to R2 under a user-scoped, param-independent key
            pixel_hash = await run_in_threadpool(compute_pixel_hash, image)
            original_key = r2_storage_service.build_original_key(pixel_hash, user_id)

            if await r2_storage_service.object_exists(original_key):
                original_url = r2_storage_service.build_public_url(original_key) or ""
            else:
                original_png = await run_in_threadpool(image_to_png_bytes, image)
                original_url = await r2_storage_service.upload_file_bytes(
                    content=original_png,
                    object_key=original_key,
                    content_type="image/png",
                ) or ""

            width, height = image.size
            document_id = uuid4().hex

            await document_repository.create(
                DocumentCreate(
                    id=document_id,
                    user_id=user_id,
                    mode="image",
                    file_name=filename,
                    file_size=file_size,
                    page_count=1,
                    original_url=original_url,
                    restored_url=restored_url,
                    content_hash=content_hash,
                    width=width,
                    height=height,
                    patch_size=params.patch_size,
                    threshold=params.threshold,
                    binarize_output=params.binarize_output,
                    overlap=params.overlap,
                )
            )
            return document_id
        except Exception:
            logger.exception("Failed to persist document record for %s", filename)
            return None

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
        document_id: str | None = None,
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
            document_id=document_id,
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

    async def confirm_threshold(
        self,
        request: ConfirmThresholdRequest,
        user_id: str = "",
    ) -> ConfirmThresholdResponse:
        """Apply a user-chosen threshold to a cached soft image and save the result.

        This does NOT call the AI model again.  It downloads the soft
        (non-binarized) output that was already cached in R2, applies a
        simple pixel threshold using Pillow, uploads the binarized result
        and returns the public URL.

        If ``request.document_id`` is provided the document record is updated
        with the new restored URL and content hash.
        """

        threshold = request.threshold
        soft_content_hash = request.soft_content_hash

        if not 0 <= threshold <= 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold must be between 0 and 1.",
            )

        # ---- 1. Build a deterministic hash for this (soft_hash, threshold) pair ----
        import hashlib
        final_hash = hashlib.sha256(
            f"{soft_content_hash}_threshold_{threshold}".encode()
        ).hexdigest()
        cache_key = r2_storage_service.build_cache_key(final_hash, user_id)

        # ---- 2. Check if the binarized result is already cached ----
        if await r2_storage_service.object_exists(cache_key):
            public_url = r2_storage_service.build_public_url(cache_key) or ""
            logger.info(
                "THRESHOLD CACHE HIT for soft=%s threshold=%.2f",
                soft_content_hash[:12], threshold,
            )
            await self._update_document_after_threshold(
                document_id=request.document_id,
                restored_url=public_url,
                content_hash=final_hash,
                threshold=threshold,
            )
            return ConfirmThresholdResponse(
                request_id=final_hash,
                filename=f"{final_hash}.png",
                url=public_url,
                content_hash=final_hash,
                threshold=threshold,
                cached=True,
                document_id=request.document_id,
            )

        # ---- 3. Download the soft image from R2 ----
        soft_cache_key = r2_storage_service.build_soft_cache_key(soft_content_hash, user_id)
        if not await r2_storage_service.object_exists(soft_cache_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Soft restored image not found. Please run soft restoration first.",
            )

        soft_bytes = await r2_storage_service.download_object(soft_cache_key)

        # ---- 4. Apply threshold using Pillow (no AI model call) ----
        binarized_bytes = await run_in_threadpool(
            self._apply_threshold_to_image, soft_bytes, threshold,
        )

        # ---- 5. Upload the binarized result to R2 ----
        public_url = await r2_storage_service.upload_file_bytes(
            content=binarized_bytes,
            object_key=cache_key,
            content_type="image/png",
        )

        logger.info(
            "THRESHOLD APPLIED for soft=%s threshold=%.2f -> %s",
            soft_content_hash[:12], threshold, final_hash[:12],
        )

        await self._update_document_after_threshold(
            document_id=request.document_id,
            restored_url=public_url or "",
            content_hash=final_hash,
            threshold=threshold,
        )

        return ConfirmThresholdResponse(
            request_id=final_hash,
            filename=f"{final_hash}.png",
            url=public_url or "",
            content_hash=final_hash,
            threshold=threshold,
            cached=False,
            document_id=request.document_id,
        )

    async def _update_document_after_threshold(
        self,
        document_id: str | None,
        restored_url: str,
        content_hash: str,
        threshold: float,
    ) -> None:
        if not document_id:
            return
        try:
            from app.schemas.document import DocumentUpdate
            from app.services.document_repository import document_repository

            await document_repository.update(
                document_id,
                DocumentUpdate(
                    restored_url=restored_url,
                    content_hash=content_hash,
                    threshold=threshold,
                ),
            )
        except Exception:
            logger.exception("Failed to update document %s after threshold confirm", document_id)

    @staticmethod
    def _apply_threshold_to_image(image_bytes: bytes, threshold: float) -> bytes:
        """Apply binary threshold to a grayscale soft image.

        Replicates the AI model logic:
        ``torch.where(prediction > threshold, 1.0, 0.0)``
        """
        import numpy as np

        img = Image.open(BytesIO(image_bytes)).convert("L")  # grayscale
        arr = np.array(img, dtype=np.float32) / 255.0
        binary = np.where(arr > threshold, 255, 0).astype(np.uint8)
        result = Image.fromarray(binary, mode="L")

        buf = BytesIO()
        result.save(buf, format="PNG")
        return buf.getvalue()

    def _restore_with_remote_model(
        self,
        image_path: Path,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> Path:
        global _ai_client
        try:
            client = _get_ai_client()
            result = client.predict(
                image=handle_file(str(image_path)),
                patch_size=patch_size,
                batch_size=batch_size,
                threshold=threshold,
                binarize_output=binarize_output,
                overlap=overlap,
                api_name=settings.AI_RESTORATION_API_NAME,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("AI predict failed: %s", exc)
            # Reset so the next request gets a fresh client (space may have restarted)
            with _ai_client_lock:
                _ai_client = None
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI restoration service error ({type(exc).__name__}): {exc}",
            ) from exc

        restored_path = self._extract_restored_path(result)
        if restored_path is None or not restored_path.exists():
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI service returned no file. Raw result: {result!r}",
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
