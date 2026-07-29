from fastapi import APIRouter

from app.api.v1.endpoints import auth, catalog, stats, template, training

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(training.router)
api_router.include_router(stats.router)
api_router.include_router(template.router)
