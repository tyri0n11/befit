from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis
from app.models.user import User
from app.services.auth import AuthError, AuthService
from app.services.body_metrics import BodyMetricsService
from app.services.catalog import CatalogService, catalog_cache
from app.services.email import EmailSender, get_email_sender
from app.services.stats import StatsService
from app.services.template import TemplateService
from app.services.training import TrainingService
from app.services.user_profile import UserProfileService

bearer_scheme = HTTPBearer(auto_error=False)


def get_catalog_service(
    session: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> CatalogService:
    return CatalogService(session, catalog_cache(redis))


def get_training_service(
    session: AsyncSession = Depends(get_db),
) -> TrainingService:
    return TrainingService(session)


def get_stats_service(
    session: AsyncSession = Depends(get_db),
) -> StatsService:
    return StatsService(session)


def get_template_service(
    session: AsyncSession = Depends(get_db),
) -> TemplateService:
    return TemplateService(session)


def get_user_profile_service(
    session: AsyncSession = Depends(get_db),
) -> UserProfileService:
    return UserProfileService(session)


def get_body_metrics_service(
    session: AsyncSession = Depends(get_db),
) -> BodyMetricsService:
    return BodyMetricsService(session)


def get_auth_service(
    session: AsyncSession = Depends(get_db),
    email: EmailSender = Depends(get_email_sender),
) -> AuthService:
    return AuthService(session, email)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
    service: AuthService = Depends(get_auth_service),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    try:
        return await service.user_from_access_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[User, Depends(get_current_user)]
