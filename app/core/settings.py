from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    PROJECT_NAME: str = "FastAPI Project"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    # Level for the `app` logger; see app/core/logging.py.
    LOG_LEVEL: str = "INFO"

    # Database settings
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "database"
    DB_USER: str = "user"
    DB_PASSWORD: str = "password"

    # Redis settings
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # Security settings
    # 32 bytes is the floor RFC 7518 §3.2 sets for an HMAC-SHA256 key; PyJWT
    # warns below it. Generate one with `openssl rand -hex 32`.
    SECRET_KEY: str = Field(min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 1440
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60

    # Email (Resend). With RESEND_API_KEY empty, outgoing mail is logged instead
    # of sent — see app/services/email.py.
    RESEND_API_KEY: str = ""
    RESEND_API_URL: str = "https://api.resend.com/emails"
    EMAIL_FROM: str = "befit <onboarding@resend.dev>"
    PASSWORD_RESET_URL: str = "http://localhost:8000/reset-password"

    # Google OAuth (authorization code flow). Redirect URI must match the one
    # registered in the Google Cloud console exactly, scheme and path included.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    # How long the browser has to complete consent before the state expires.
    OAUTH_STATE_TTL_SECONDS: int = 600

    @computed_field
    @property
    def GOOGLE_OAUTH_CONFIGURED(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
