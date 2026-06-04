"""API routes for async PDF restoration jobs."""

from fastapi import APIRouter, File, Form, UploadFile, status

from app.core.config import settings
from app.schemas.pdf_restoration import PdfJobResponse
from app.services.pdf_restoration_service import pdf_restoration_service

router = APIRouter()


@router.post(
    "",
    response_model=PdfJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Restore a PDF document",
)
async def submit_pdf_restoration(
    file: UploadFile = File(..., description="PDF file to restore"),
    patch_size: int = Form(settings.DEFAULT_PATCH_SIZE),
    batch_size: int = Form(settings.DEFAULT_BATCH_SIZE),
    threshold: float = Form(settings.DEFAULT_THRESHOLD),
    binarize_output: bool = Form(settings.DEFAULT_BINARIZE_OUTPUT),
    overlap: bool = Form(settings.DEFAULT_OVERLAP),
) -> PdfJobResponse:
    """Upload a PDF and wait for restoration to complete.

    The response is only sent once all pages have been processed (or the
    job has failed), so the returned ``status`` will be either
    ``completed`` or ``failed``.
    """
    return await pdf_restoration_service.submit_job(
        file=file,
        patch_size=patch_size,
        batch_size=batch_size,
        threshold=threshold,
        binarize_output=binarize_output,
        overlap=overlap,
    )


@router.get(
    "",
    response_model=list[PdfJobResponse],
    summary="List all PDF restoration jobs",
)
async def list_pdf_restorations() -> list[PdfJobResponse]:
    """Return all tracked PDF restoration jobs, most recent first."""
    return await pdf_restoration_service.list_jobs()


@router.get(
    "/{job_id}",
    response_model=PdfJobResponse,
    summary="Get PDF restoration job status",
)
async def get_pdf_restoration_status(job_id: str) -> PdfJobResponse:
    """Poll the current status and progress of a restoration job."""
    return await pdf_restoration_service.get_job_status(job_id)
