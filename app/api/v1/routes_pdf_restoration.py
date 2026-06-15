"""API routes for async PDF restoration jobs."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from app.core.auth import get_current_user
from app.core.config import settings
from app.schemas.pdf_restoration import PdfJobResponse
from app.services.document_repository import document_repository
from app.services.pdf_job_manager import pdf_job_manager
from app.services.pdf_restoration_service import pdf_restoration_service
from app.services.r2_storage import r2_storage_service

router = APIRouter()


@router.post(
    "",
    response_model=PdfJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a PDF restoration job",
)
async def submit_pdf_restoration(
    file: UploadFile = File(..., description="PDF file to restore"),
    patch_size: int = Form(settings.DEFAULT_PATCH_SIZE),
    batch_size: int = Form(settings.DEFAULT_BATCH_SIZE),
    threshold: float = Form(settings.DEFAULT_THRESHOLD),
    binarize_output: bool = Form(settings.DEFAULT_BINARIZE_OUTPUT),
    overlap: bool = Form(settings.DEFAULT_OVERLAP),
    user_id: str = Depends(get_current_user),
) -> PdfJobResponse:
    """Upload a PDF and start a background restoration job.

    Returns immediately with ``status: pending`` and a ``job_id``.
    Stream real-time progress from ``GET /{job_id}/progress``.
    """
    return await pdf_restoration_service.submit_job(
        file=file,
        patch_size=patch_size,
        batch_size=batch_size,
        threshold=threshold,
        binarize_output=binarize_output,
        overlap=overlap,
        user_id=user_id,
    )


@router.get(
    "/{job_id}/progress",
    summary="Stream real-time progress for a PDF restoration job (SSE)",
    response_class=StreamingResponse,
)
async def stream_pdf_progress(
    job_id: str,
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    """Server-Sent Events stream for live page-by-page progress.

    Each event is a JSON object:
    ``{"status", "total_pages", "processed_pages", "progress_percent",
       "output_pdf_url", "document_id", "error"}``

    The stream closes automatically when the job reaches ``completed``
    or ``failed``.
    """
    job_owner = await document_repository.get_pdf_job_user(job_id)
    if job_owner is not None and job_owner and job_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this job.",
        )

    async def generate():
        async for chunk in pdf_job_manager.subscribe(job_id):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/{job_id}/pages",
    summary="List all restored pages for a PDF job",
)
async def get_pdf_job_pages(
    job_id: str,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    """Return per-page records stored in the database for a completed PDF job."""
    job_owner = await document_repository.get_pdf_job_user(job_id)
    if job_owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No job found with id '{job_id}'.",
        )
    if job_owner and job_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this job.",
        )

    pages = await document_repository.get_pdf_pages(job_id)
    if not pages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pages found for job '{job_id}'.",
        )
    return pages


@router.get(
    "/{job_id}/page-image/{page_num}",
    summary="Proxy a restored PDF page image from R2",
    responses={200: {"content": {"image/png": {}}}},
)
async def get_pdf_page_image(
    job_id: str,
    page_num: int,
    user_id: str = Depends(get_current_user),
) -> Response:
    """Proxy the restored page image bytes from R2."""
    job_owner = await document_repository.get_pdf_job_user(job_id)
    if job_owner is not None and job_owner and job_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this job.",
        )

    pages = await document_repository.get_pdf_pages(job_id)
    page = next((p for p in pages if p["page"] == page_num), None)
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Page {page_num} not found for job '{job_id}'.",
        )

    r2_key = page["r2_object_key"]
    if not await r2_storage_service.object_exists(r2_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page image not found in storage.",
        )

    image_bytes = await r2_storage_service.download_object(r2_key)
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )
