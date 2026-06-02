from fastapi import APIRouter

from app.api.v1 import routes_classification, routes_health, routes_restoration

api_router = APIRouter()
api_router.include_router(routes_health.router, tags=["health"])
api_router.include_router(routes_classification.router, prefix="/classifications", tags=["classifications"])
api_router.include_router(routes_restoration.router, prefix="/restorations", tags=["restorations"])
