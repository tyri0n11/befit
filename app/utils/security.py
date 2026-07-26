"""Password hashing and JWT encode/decode. No database access here."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.settings import settings

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Token is malformed, expired, or of the wrong type."""


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    token_version: int


def hash_password(password: str) -> str:
    # bcrypt silently truncates at 72 bytes; reject instead of letting a long
    # password become equivalent to its own prefix.
    encoded = password.encode()
    if len(encoded) > 72:
        raise ValueError("password must be at most 72 bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # Malformed hash in the database — treat as a failed login, not a 500.
        return False


def _create_token(
    subject: int, token_version: int, token_type: TokenType, expires_in: timedelta
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        # Compared against users.token_version on every use. Bumping the column
        # is the only way to revoke an already-issued token.
        "ver": token_version,
        "type": token_type,
        "iat": now,
        "exp": now + expires_in,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user_id: int, token_version: int) -> str:
    return _create_token(
        user_id,
        token_version,
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int, token_version: int) -> str:
    return _create_token(
        user_id,
        token_version,
        "refresh",
        timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
    )


def generate_reset_token() -> str:
    """Return the raw token handed to the user. 32 bytes of urlsafe entropy."""
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    """Digest stored in password_reset_tokens.token_hash.

    A plain SHA-256 is sufficient because the input is already high-entropy
    random — unlike a password, it is not guessable, so there is nothing for a
    slow KDF to protect against.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str, expected_type: TokenType) -> TokenPayload:
    """Return the claims carried by `token`, or raise TokenError."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[ALGORITHM]
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        # Refusing a refresh token at an access-token call site (and vice versa)
        # is what stops a long-lived refresh token being used as a bearer token.
        raise TokenError(f"expected a {expected_type} token")

    try:
        return TokenPayload(
            user_id=int(payload["sub"]), token_version=int(payload["ver"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("token is missing required claims") from exc
