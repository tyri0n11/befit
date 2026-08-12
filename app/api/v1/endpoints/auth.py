from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis

from app.api.v1.dependencies import CurrentUser, get_auth_service
from app.core.redis import get_redis
from app.mcp.oauth import store_authorization_code
from app.schemas.auth import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from app.services.auth import AuthError, AuthErrorCode, AuthService
from app.services.google_oauth import GoogleOAuthError, complete_login, start_login
from app.utils.security import decode_token

# Redis key prefix used only to correlate a Google login started from
# app/mcp/login.py's "Continue with Google" button back to the MCP
# authorization request that initiated it. Google allows exactly one
# registered redirect URI, so both flows land on google_callback() below and
# branch there instead of having separate callback routes.
_MCP_GOOGLE_PREFIX = "mcp:oauth:google:"

router = APIRouter(prefix="/auth", tags=["auth"])

_STATUS_FOR_CODE = {
    AuthErrorCode.EMAIL_TAKEN: status.HTTP_409_CONFLICT,
    AuthErrorCode.INVALID_CREDENTIALS: status.HTTP_401_UNAUTHORIZED,
    AuthErrorCode.ACCOUNT_INACTIVE: status.HTTP_403_FORBIDDEN,
    AuthErrorCode.INVALID_TOKEN: status.HTTP_401_UNAUTHORIZED,
    AuthErrorCode.INVALID_RESET_TOKEN: status.HTTP_400_BAD_REQUEST,
    AuthErrorCode.OAUTH_FAILED: status.HTTP_400_BAD_REQUEST,
    AuthErrorCode.EMAIL_NOT_VERIFIED: status.HTTP_403_FORBIDDEN,
}


def _http_error(exc: AuthError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_FOR_CODE[exc.code],
        detail=exc.message,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register with an email and password",
)
async def register(
    payload: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    try:
        user = await service.register(
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
        )
    except AuthError as exc:
        raise _http_error(exc) from exc

    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenPair, summary="Exchange credentials")
async def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenPair:
    try:
        return await service.login(email=payload.email, password=payload.password)
    except AuthError as exc:
        raise _http_error(exc) from exc


@router.post("/refresh", response_model=TokenPair, summary="Rotate tokens")
async def refresh(
    payload: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenPair:
    try:
        return await service.refresh(payload.refresh_token)
    except AuthError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password reset link",
)
async def request_password_reset(
    payload: PasswordResetRequest,
    service: AuthService = Depends(get_auth_service),
) -> Response:
    await service.request_password_reset(payload.email)
    # 202 regardless of whether the address is registered — a different response
    # for unknown emails would enumerate accounts.
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password using a reset token",
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    service: AuthService = Depends(get_auth_service),
) -> Response:
    try:
        await service.confirm_password_reset(payload.token, payload.new_password)
    except AuthError as exc:
        raise _http_error(exc) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/google/login",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Redirect to Google consent",
)
async def google_login(redis: Redis = Depends(get_redis)) -> RedirectResponse:
    try:
        url, _state = await start_login(redis)
    except GoogleOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return RedirectResponse(url)


@router.get(
    "/google/callback",
    response_model=None,
    summary="Complete the Google flow and issue tokens",
)
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    redis: Redis = Depends(get_redis),
    service: AuthService = Depends(get_auth_service),
) -> TokenPair | RedirectResponse:
    if error:
        # Google sends ?error=access_denied when the user cancels consent.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google sign-in was not completed ({error})",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state",
        )

    try:
        identity = await complete_login(redis, code, state)
    except GoogleOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        pair = await service.login_with_google(identity)
    except AuthError as exc:
        raise _http_error(exc) from exc

    # Was this Google login started from app/mcp/login.py's "Continue with
    # Google" button rather than the REST client's own? If so, complete the
    # pending MCP authorization instead of returning tokens directly.
    mcp_key = _MCP_GOOGLE_PREFIX + state
    mcp_request_id = await redis.get(mcp_key)
    if mcp_request_id:
        await redis.delete(mcp_key)
        claims = decode_token(pair.access_token, "access")
        redirect_url = await store_authorization_code(mcp_request_id, claims.user_id)
        if redirect_url is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This sign-in link has expired",
            )
        return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)

    return pair


@router.get("/me", response_model=UserResponse, summary="The authenticated user")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
