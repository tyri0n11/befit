from fastapi import APIRouter

from app.api.v1.endpoints import auth, catalog, training

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(training.router)
