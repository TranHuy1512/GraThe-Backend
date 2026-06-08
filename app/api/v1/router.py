from fastapi import APIRouter

from app.api.v1 import (
    routes_classification,
    routes_documents,
    routes_health,
    routes_pdf_restoration,
    routes_restoration,
    routes_upload,
)

api_router = APIRouter()
api_router.include_router(routes_health.router, tags=["health"])
api_router.include_router(routes_classification.router, prefix="/classifications", tags=["classifications"])
api_router.include_router(routes_restoration.router, prefix="/restorations", tags=["restorations"])
api_router.include_router(routes_pdf_restoration.router, prefix="/pdf-restorations", tags=["pdf-restorations"])
api_router.include_router(routes_upload.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(routes_documents.router, prefix="/documents", tags=["documents"])
