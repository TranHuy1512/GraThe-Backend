"""Async PDF restoration service with per-page R2 cache deduplication.

Pipeline: upload PDF → extract pages → hash each page → check R2 cache →
restore uncached pages via AI (concurrent) → upload to R2 cache →
merge all pages to PDF → upload final PDF → mark complete.
"""

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi import HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from gradio_client import Client, handle_file
from PIL import Image

from app.core.config import settings
from app.schemas.pdf_restoration import JobStatus, PageResult, PdfJobResponse
from app.services.document_loader import is_pdf
from app.services.pdf_job_manager import pdf_job_manager
from app.services.r2_storage import r2_storage_service
from app.utils.files import safe_filename
from app.utils.image_hash import (
    RestorationParams,
    compute_content_hash_from_path,
    read_image_as_png_bytes,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_AI_CALLS = 3
PAGE_RETRY_ATTEMPTS = 2
PAGE_RETRY_BACKOFF_BASE = 2  # seconds


class PdfRestorationService:
    """Orchestrates the full async PDF restoration pipeline."""

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    async def submit_job(
        self,
        file: UploadFile,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> PdfJobResponse:
        """Validate the upload, create a job, process it, and return the final result.

        The call blocks until the background pipeline finishes (completed or
        failed), so the caller receives the definitive outcome in a single
        request — no polling required.
        """

        self._validate_options(patch_size, batch_size, threshold)
        upload_path = await self._save_upload(file)

        if not is_pdf(upload_path):
            upload_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported file type. Please upload a PDF file.",
            )

        job_id = uuid4().hex
        await pdf_job_manager.create_job(
            job_id, file.filename or upload_path.name,
        )

        # Launch the heavy pipeline in a background task …
        asyncio.create_task(
            self._process_pdf_job(
                job_id=job_id,
                pdf_path=upload_path,
                patch_size=patch_size,
                batch_size=batch_size,
                threshold=threshold,
                binarize_output=binarize_output,
                overlap=overlap,
            ),
            name=f"pdf-restore-{job_id}",
        )

        # … then wait for it to reach a terminal state before responding.
        job = await pdf_job_manager.wait_for_completion(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Job vanished unexpectedly.",
            )
        return job.to_response()

    async def get_job_status(self, job_id: str) -> PdfJobResponse:
        """Return the current state of a job, or raise 404."""

        job = await pdf_job_manager.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found or has expired.",
            )
        return job.to_response()

    async def list_jobs(self) -> list[PdfJobResponse]:
        """Return all tracked jobs (most recent first)."""

        jobs = await pdf_job_manager.list_jobs()
        return [
            j.to_response()
            for j in sorted(jobs, key=lambda j: j.created_at, reverse=True)
        ]

    # ------------------------------------------------------------------ #
    #  Background pipeline                                                #
    # ------------------------------------------------------------------ #

    async def _process_pdf_job(
        self,
        job_id: str,
        pdf_path: Path,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> None:
        """Full pipeline executed as a background ``asyncio.Task``."""

        params = RestorationParams(
            patch_size=patch_size,
            threshold=threshold,
            binarize_output=binarize_output,
            overlap=overlap,
        )
        temp_page_paths: list[Path] = []

        try:
            # ---- 1. Extract PDF → temp page images ----
            await pdf_job_manager.update_status(job_id, JobStatus.EXTRACTING)
            temp_page_paths = await run_in_threadpool(
                self._extract_pdf_to_temp_files, pdf_path, job_id,
            )
            total_pages = len(temp_page_paths)
            await pdf_job_manager.update_status(
                job_id, JobStatus.PROCESSING, total_pages=total_pages,
            )
            logger.info("Job %s: extracted %d pages from PDF", job_id, total_pages)

            # ---- 2. Restore pages concurrently (semaphore-guarded) ----
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_AI_CALLS)

            async def _guarded_restore(
                page_idx: int, page_path: Path,
            ) -> tuple[PageResult, bytes]:
                async with semaphore:
                    return await self._restore_and_upload_page(
                        job_id=job_id,
                        page_idx=page_idx,
                        page_path=page_path,
                        params=params,
                        batch_size=batch_size,
                    )

            results: list[tuple[PageResult, bytes]] = await asyncio.gather(
                *(
                    _guarded_restore(idx, path)
                    for idx, path in enumerate(temp_page_paths)
                )
            )

            # ---- 3. Merge restored images → output PDF ----
            await pdf_job_manager.update_status(job_id, JobStatus.MERGING)
            sorted_results = sorted(results, key=lambda r: r[0].page)
            pdf_bytes = await run_in_threadpool(
                self._merge_images_to_pdf,
                [img_bytes for _, img_bytes in sorted_results],
            )
            logger.info(
                "Job %s: merged %d pages into PDF (%d bytes)",
                job_id, total_pages, len(pdf_bytes),
            )

            # ---- 4. Upload final PDF to R2 ----
            await pdf_job_manager.update_status(job_id, JobStatus.UPLOADING)
            pdf_object_key = r2_storage_service.build_pdf_object_key(
                job_id, "restored.pdf",
            )
            pdf_url = await r2_storage_service.upload_file_bytes(
                content=pdf_bytes,
                object_key=pdf_object_key,
                content_type="application/pdf",
            )

            # ---- 5. Done ----
            await pdf_job_manager.mark_completed(job_id, output_pdf_url=pdf_url)
            logger.info("Job %s: completed successfully", job_id)

        except Exception as exc:
            logger.exception("Job %s failed: %s", job_id, exc)
            await pdf_job_manager.mark_failed(job_id, str(exc))

        finally:
            for p in temp_page_paths:
                p.unlink(missing_ok=True)
            pdf_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  Page-level processing with cache                                   #
    # ------------------------------------------------------------------ #

    async def _restore_and_upload_page(
        self,
        job_id: str,
        page_idx: int,
        page_path: Path,
        params: RestorationParams,
        batch_size: int,
    ) -> tuple[PageResult, bytes]:
        """Restore one page, checking the R2 cache first."""

        page_num = page_idx + 1

        # 1. Compute content hash
        content_hash = await run_in_threadpool(
            compute_content_hash_from_path, page_path, params,
        )
        cache_key = r2_storage_service.build_cache_key(content_hash)

        # 2. Check R2 cache
        if await r2_storage_service.object_exists(cache_key):
            # CACHE HIT → download restored bytes for PDF merge
            restored_bytes = await r2_storage_service.download_object(cache_key)
            public_url = r2_storage_service.build_public_url(cache_key)

            page_result = PageResult(
                page=page_num,
                filename=f"page-{page_num:03d}.png",
                r2_object_key=cache_key,
                public_url=public_url,
                content_hash=content_hash,
                cached=True,
            )
            await pdf_job_manager.add_page_result(job_id, page_result)
            logger.info(
                "Job %s: page %d CACHE HIT (%s…)",
                job_id, page_num, content_hash[:12],
            )
            return page_result, restored_bytes

        # 3. CACHE MISS → call AI with retry
        restored_path = await self._call_ai_with_retry(
            page_path=page_path,
            page_num=page_num,
            params=params,
            batch_size=batch_size,
        )

        # 4. Read restored image as PNG bytes
        restored_bytes = await run_in_threadpool(
            read_image_as_png_bytes, restored_path,
        )

        # 5. Upload to R2 cache
        public_url = await r2_storage_service.upload_file_bytes(
            content=restored_bytes,
            object_key=cache_key,
            content_type="image/png",
        )

        # 6. Track progress
        page_result = PageResult(
            page=page_num,
            filename=f"page-{page_num:03d}.png",
            r2_object_key=cache_key,
            public_url=public_url,
            content_hash=content_hash,
            cached=False,
        )
        await pdf_job_manager.add_page_result(job_id, page_result)
        logger.info(
            "Job %s: page %d restored & uploaded (%s…)",
            job_id, page_num, content_hash[:12],
        )
        return page_result, restored_bytes

    async def _call_ai_with_retry(
        self,
        page_path: Path,
        page_num: int,
        params: RestorationParams,
        batch_size: int,
    ) -> Path:
        """Call the Gradio AI endpoint with automatic retry on transient errors."""

        last_exc: Exception | None = None
        for attempt in range(PAGE_RETRY_ATTEMPTS + 1):
            try:
                return await run_in_threadpool(
                    self._call_gradio_restore,
                    page_path,
                    params.patch_size,
                    batch_size,
                    params.threshold,
                    params.binarize_output,
                    params.overlap,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < PAGE_RETRY_ATTEMPTS:
                    wait = PAGE_RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "Page %d: AI call attempt %d failed (%s), retrying in %ds…",
                        page_num, attempt + 1, exc, wait,
                    )
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"AI restoration failed for page {page_num} "
            f"after {PAGE_RETRY_ATTEMPTS + 1} attempts",
        ) from last_exc

    # ------------------------------------------------------------------ #
    #  Gradio client helpers                                              #
    # ------------------------------------------------------------------ #

    def _call_gradio_restore(
        self,
        image_path: Path,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> Path:
        """Synchronous call to the remote Gradio AI server."""

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

        restored_path = self._extract_restored_path(result)
        if restored_path is None or not restored_path.exists():
            raise RuntimeError(
                f"AI server did not return a valid file for {image_path.name}",
            )
        return restored_path

    def _extract_restored_path(self, result: object) -> Path | None:
        """Navigate the Gradio result structure to find the output file path."""

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

    # ------------------------------------------------------------------ #
    #  PDF / image helpers                                                #
    # ------------------------------------------------------------------ #

    def _extract_pdf_to_temp_files(
        self, pdf_path: Path, job_id: str,
    ) -> list[Path]:
        """Render each PDF page as a PNG and save to the upload directory."""

        zoom = settings.PDF_RENDER_DPI / 72
        matrix = fitz.Matrix(zoom, zoom)
        temp_paths: list[Path] = []

        with fitz.open(pdf_path) as document:
            for page_idx, page in enumerate(document):
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                temp_path = (
                    settings.UPLOAD_DIR / f"{job_id}_page_{page_idx + 1:03d}.png"
                )
                pixmap.save(str(temp_path))
                temp_paths.append(temp_path)

        if not temp_paths:
            raise ValueError("PDF has no pages.")
        return temp_paths

    def _merge_images_to_pdf(self, image_bytes_list: list[bytes]) -> bytes:
        """Combine a list of PNG byte buffers into a single PDF."""

        doc = fitz.open()
        try:
            for img_bytes in image_bytes_list:
                with Image.open(BytesIO(img_bytes)) as img:
                    width_px, height_px = img.size

                width_pt = width_px * 72.0 / settings.PDF_RENDER_DPI
                height_pt = height_px * 72.0 / settings.PDF_RENDER_DPI

                page = doc.new_page(width=width_pt, height=height_pt)
                page.insert_image(
                    fitz.Rect(0, 0, width_pt, height_pt),
                    stream=img_bytes,
                )
            return doc.tobytes(deflate=True)
        finally:
            doc.close()

    # ------------------------------------------------------------------ #
    #  Upload & validation                                                #
    # ------------------------------------------------------------------ #

    async def _save_upload(self, file: UploadFile) -> Path:
        """Persist the uploaded file to disk and enforce size limits."""

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

        filename = safe_filename(file.filename)
        upload_path = settings.UPLOAD_DIR / f"{uuid4().hex}-{filename}"
        upload_path.write_bytes(content)
        return upload_path

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


pdf_restoration_service = PdfRestorationService()
