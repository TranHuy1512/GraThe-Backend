from fastapi import APIRouter, Depends, File, UploadFile, status

from app.core.auth import get_current_user
from app.schemas.upload import ImageUploadResponse
from app.services.r2_storage import r2_storage_service

router = APIRouter()


@router.post("/images", response_model=ImageUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
) -> ImageUploadResponse:
    return await r2_storage_service.upload_image(file, user_id=user_id)
