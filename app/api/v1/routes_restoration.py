from fastapi import APIRouter, File, Form, UploadFile, status

from app.core.config import settings
from app.schemas.restoration import RestorationResponse, SoftRestorationResponse
from app.services.restoration_service import restoration_service

router = APIRouter()


@router.post(
    "/soft",
    response_model=SoftRestorationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def restore_document_soft(
    file: UploadFile = File(...),
    patch_size: int = Form(settings.DEFAULT_PATCH_SIZE),
    batch_size: int = Form(settings.DEFAULT_BATCH_SIZE),
    overlap: bool = Form(settings.DEFAULT_OVERLAP),
) -> SoftRestorationResponse:
    return await restoration_service.restore_soft_upload(
        file=file,
        patch_size=patch_size,
        batch_size=batch_size,
        overlap=overlap,
    )


@router.post("", response_model=RestorationResponse, status_code=status.HTTP_201_CREATED)
async def restore_document(
    file: UploadFile = File(...),
    patch_size: int = Form(settings.DEFAULT_PATCH_SIZE),
    batch_size: int = Form(settings.DEFAULT_BATCH_SIZE),
    threshold: float = Form(settings.DEFAULT_THRESHOLD),
    binarize_output: bool = Form(settings.DEFAULT_BINARIZE_OUTPUT),
    overlap: bool = Form(settings.DEFAULT_OVERLAP),
) -> RestorationResponse:
    return await restoration_service.restore_upload(
        file=file,
        patch_size=patch_size,
        batch_size=batch_size,
        threshold=threshold,
        binarize_output=binarize_output,
        overlap=overlap,
    )
