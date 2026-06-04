from fastapi import APIRouter, File, UploadFile, status

from app.schemas.upload import ImageUploadResponse
from app.services.r2_storage import r2_storage_service

router = APIRouter()


@router.post("/images", response_model=ImageUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(file: UploadFile = File(...)) -> ImageUploadResponse:
    return await r2_storage_service.upload_image(file)
