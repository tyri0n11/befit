from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from app.api.v1.api import api_router
from app.core.database import db_lifespan
from app.core.logging import configure_logging
from app.core.redis import redis_lifespan
from app.core.settings import settings
from app.mcp.login import router as mcp_login_router
from app.mcp.server import mcp as mcp_server

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(db_lifespan())
        await stack.enter_async_context(redis_lifespan())
        if settings.MCP_ENABLED:
            await stack.enter_async_context(mcp_server.session_manager.run())
        yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="befit101 API",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(api_router, prefix=settings.API_V1_STR)

if settings.MCP_ENABLED:
    app.include_router(mcp_login_router)

    # With auth_server_provider set, streamable_http_app() no longer returns
    # just the transport route — it also adds /authorize, /token, /register
    # and the two .well-known metadata documents, all as *absolute* paths on
    # this one Starlette instance. Mounting the whole app under "/mcp" would
    # nest every one of those under the prefix too (/mcp/token, and worse, a
    # well-known URI nested under a path — breaks RFC 8615 discovery). So
    # streamable_http_path is "/mcp" (correct already, no prefix needed) and
    # the routes are spliced onto this app directly instead of mounted.
    #
    # DNS-rebinding protection is disabled: the app already runs with CORS
    # wide open and sits behind a Cloudflare Tunnel with no single fixed Host
    # header, so the default localhost-only allowlist would just break every
    # non-local client.
    mcp_asgi_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    app.router.routes.extend(mcp_asgi_app.routes)
    # Routes alone aren't enough: RequireAuthMiddleware (wrapping just the
    # /mcp route) only rejects when scope["user"] is unset, and it's
    # AuthenticationMiddleware — a middleware on mcp_asgi_app itself, not
    # carried over by splicing .routes — that actually resolves the bearer
    # token and sets it. Without this, /mcp always 401s regardless of the
    # token presented.
    for mw in mcp_asgi_app.user_middleware:
        app.add_middleware(mw.cls, **mw.kwargs)


@app.get("/", include_in_schema=False)
def read_root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}!"}
