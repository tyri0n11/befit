from fastapi import APIRouter, Depends

from app.api.v1.dependencies import CurrentUser, get_user_profile_service
from app.schemas.user import UserProfileResponse, UserProfileUpdate
from app.services.user_profile import UserProfileService

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Physical profile — created empty on first read",
)
async def get_profile(
    user: CurrentUser,
    service: UserProfileService = Depends(get_user_profile_service),
) -> UserProfileResponse:
    profile = await service.get(user.id)
    return UserProfileResponse.model_validate(profile)


@router.put(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Update the profile; bmr_kcal/tdee_kcal are recomputed, not settable",
)
async def update_profile(
    payload: UserProfileUpdate,
    user: CurrentUser,
    service: UserProfileService = Depends(get_user_profile_service),
) -> UserProfileResponse:
    profile = await service.update(user.id, payload)
    return UserProfileResponse.model_validate(profile)
