from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.core.config import settings
from app.schemas.restoration import (
    ConfirmThresholdRequest,
    ConfirmThresholdResponse,
    RestorationResponse,
    SoftRestorationResponse,
)
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


@router.post(
    "/confirm-threshold",
    response_model=ConfirmThresholdResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Apply a threshold to a cached soft output and save the result",
)
async def confirm_threshold(
    body: ConfirmThresholdRequest,
) -> ConfirmThresholdResponse:
    """Finalize a soft-restored image with a user-chosen threshold.

    This endpoint does NOT re-run the AI model.  It downloads the
    already-cached soft output, applies a simple pixel threshold,
    uploads the binarized result to R2 and returns the public URL.
    """
    return await restoration_service.confirm_threshold(
        soft_content_hash=body.soft_content_hash,
        threshold=body.threshold,
    )


@router.get(
    "/soft-image/{content_hash}",
    summary="Serve a cached soft-restored image",
    responses={200: {"content": {"image/png": {}}}},
)
async def get_soft_image(content_hash: str) -> Response:
    """Proxy the soft-restored image bytes from R2.

    This avoids CORS issues when the frontend needs to draw
    the image onto a Canvas for real-time threshold preview.
    """
    from app.services.r2_storage import r2_storage_service

    cache_key = r2_storage_service.build_soft_cache_key(content_hash)
    if not await r2_storage_service.object_exists(cache_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Soft image not found.",
        )

    image_bytes = await r2_storage_service.download_object(cache_key)
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
