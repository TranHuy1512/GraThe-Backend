from fastapi import APIRouter, File, UploadFile, status

from app.schemas.classification import ClassificationResponse
from app.services.classification_service import classification_service

router = APIRouter()


@router.post("", response_model=ClassificationResponse, status_code=status.HTTP_200_OK)
async def classify_document_photo(file: UploadFile = File(...)) -> ClassificationResponse:
    return await classification_service.classify_upload(file)
