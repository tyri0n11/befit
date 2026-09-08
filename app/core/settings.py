from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    PROJECT_NAME: str = "befit101"
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
    # Catalog responses are master data written only by scripts/seed.py, so they
    # can be cached hard. Set to 0 to bypass the cache entirely.
    CATALOG_CACHE_TTL_SECONDS: int = 3600

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
    # Deep link the mobile app's Google flow is redirected back to (see
    # google_callback's `mobile` branch). Query string carries the token pair.
    MOBILE_APP_SCHEME: str = "befit://auth/google-callback"

    # Google Calendar sync (separate, explicit opt-in — see
    # app/services/google_calendar.py). Distinct redirect URI from
    # GOOGLE_REDIRECT_URI: /calendar/callback is a different path and must be
    # registered separately in the Google Cloud console.
    GOOGLE_CALENDAR_REDIRECT_URI: str = "http://localhost:8000/api/v1/calendar/callback"
    # Public HTTPS address Google POSTs push notifications to. Must be
    # reachable from the internet (the Cloudflare Tunnel that already serves
    # the API works for this — no separate inbound path needed).
    GOOGLE_CALENDAR_WEBHOOK_URL: str = ""
    # Shared secret sent as the watch channel's `token`; the webhook endpoint
    # checks it against X-Goog-Channel-Token to reject spoofed calls.
    GOOGLE_CALENDAR_WEBHOOK_TOKEN: str = ""

    # MCP (Model Context Protocol) — exposes the app as tools for Claude chat/cowork.
    MCP_ENABLED: bool = True
    # This API's own public origin, used as the OAuth issuer for MCP clients.
    # Must match exactly what those clients see — RFC 8414 issuer comparison
    # is exact-string, so a trailing slash or wrong scheme breaks discovery.
    MCP_ISSUER_URL: str = "http://localhost:8000"

    @computed_field
    @property
    def GOOGLE_OAUTH_CONFIGURED(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @computed_field
    @property
    def GOOGLE_CALENDAR_CONFIGURED(self) -> bool:
        return bool(
            self.GOOGLE_CLIENT_ID
            and self.GOOGLE_CLIENT_SECRET
            and self.GOOGLE_CALENDAR_WEBHOOK_URL
            and self.GOOGLE_CALENDAR_WEBHOOK_TOKEN
        )

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        # DB_USER/DB_PASSWORD are interpolated into a URL, so any of
        # : / ? # [ ] @ in them must be percent-encoded or the parser
        # misreads the authority — e.g. a password containing "@" makes it
        # look like the host separator, silently pointing DNS lookups at a
        # mangled "<rest-of-password>@<real-host>" that doesn't resolve.
        user = quote_plus(self.DB_USER)
        password = quote_plus(self.DB_PASSWORD)
        return f"postgresql+asyncpg://{user}:{password}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
