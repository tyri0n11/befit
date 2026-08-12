from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from app.api.v1.api import api_router
from app.core.database import db_lifespan
from app.core.logging import configure_logging
from app.core.redis import redis_lifespan
from app.core.settings import settings
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
    # streamable_http_path="/" — the streamable-HTTP route lives at the root of
    # its own sub-app, so mounting it at "/mcp" here doesn't double up to
    # "/mcp/mcp". DNS-rebinding protection is disabled: the app already runs
    # with CORS wide open and sits behind a Cloudflare Tunnel with no single
    # fixed Host header, so the default localhost-only allowlist would just
    # break every non-local client.
    app.mount(
        "/mcp",
        mcp_server.streamable_http_app(
            streamable_http_path="/",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            ),
        ),
    )


@app.get("/", include_in_schema=False)
def read_root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}!"}
