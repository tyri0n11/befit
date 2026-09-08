from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    body_metrics,
    calendar,
    catalog,
    stats,
    template,
    training,
    user,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(training.router)
api_router.include_router(stats.router)
api_router.include_router(template.router)
api_router.include_router(user.router)
api_router.include_router(body_metrics.router)
api_router.include_router(calendar.router)
