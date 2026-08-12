"""The one piece of the OAuth flow the SDK doesn't generate: a minimal,
dependency-free HTML login form (no Jinja2 in this repo) that
BefitOAuthProvider.authorize() redirects the browser to, and which hands
control back via app.mcp.oauth.store_authorization_code on success.
"""

import html

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.database import db
from app.core.redis import cache
from app.core.settings import settings
from app.mcp.oauth import load_pending_request, store_authorization_code
from app.services.auth import AuthError, AuthService
from app.services.email import get_email_sender
from app.services.google_oauth import GoogleOAuthError, start_login
from app.utils.security import decode_token

router = APIRouter(tags=["mcp-oauth"])

# Same Redis-key prefix app/api/v1/endpoints/auth.py's google_callback()
# checks — see that module's comment for why the two flows share one
# callback (Google allows exactly one registered redirect URI).
_MCP_GOOGLE_PREFIX = "mcp:oauth:google:"


def _page(body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Sign in to befit</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 24rem; margin: 4rem auto; }}
label {{ display: block; margin-top: 1rem; font-size: 0.9rem; }}
input {{ width: 100%; padding: 0.5rem; box-sizing: border-box; }}
button {{ margin-top: 1.5rem; width: 100%; padding: 0.6rem; }}
.error {{ color: #b00020; margin-top: 1rem; }}
</style>
</head>
<body>{body}</body>
</html>"""


def _form(request_id: str, client_name: str | None, error: str | None = None) -> str:
    heading = (
        f"Sign in to befit for {html.escape(client_name)}"
        if client_name
        else "Sign in to befit"
    )
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    google_html = ""
    if settings.GOOGLE_OAUTH_CONFIGURED:
        google_url = f"/oauth/login/google?request_id={html.escape(request_id)}"
        google_html = f"""
<a href="{google_url}"><button type="button">Continue with Google</button></a>
<p style="text-align:center;color:#888;margin:1rem 0;">or</p>"""
    return _page(f"""
<h1>{heading}</h1>
{error_html}
{google_html}
<form method="post" action="/oauth/login">
<input type="hidden" name="request_id" value="{html.escape(request_id)}">
<label>Email<input type="email" name="email" required autofocus></label>
<label>Password<input type="password" name="password" required></label>
<button type="submit">Sign in</button>
</form>""")


def _expired_page() -> HTMLResponse:
    return HTMLResponse(
        _page(
            "<h1>This sign-in link has expired</h1>"
            "<p>Go back to the app and try connecting again.</p>"
        ),
        status_code=400,
    )


@router.get("/oauth/login", response_class=HTMLResponse)
async def login_form(request_id: str = Query(...)) -> HTMLResponse:
    pending = await load_pending_request(request_id)
    if pending is None:
        return _expired_page()
    return HTMLResponse(_form(request_id, pending.get("client_name")))


@router.get("/oauth/login/google", response_model=None)
async def login_google(request_id: str = Query(...)) -> RedirectResponse | HTMLResponse:
    pending = await load_pending_request(request_id)
    if pending is None:
        return _expired_page()

    try:
        url, google_state = await start_login(cache.client)
    except GoogleOAuthError as exc:
        return HTMLResponse(
            _page(f"<h1>Google sign-in unavailable</h1><p>{html.escape(str(exc))}</p>"),
            status_code=503,
        )

    # google_callback() in app/api/v1/endpoints/auth.py reads this mapping
    # to know it should complete this MCP authorization request, not return
    # tokens directly. Same TTL as the pending request it points back to.
    await cache.client.set(
        _MCP_GOOGLE_PREFIX + google_state,
        request_id,
        ex=settings.OAUTH_STATE_TTL_SECONDS,
    )
    return RedirectResponse(url, status_code=302)


@router.post("/oauth/login", response_model=None)
async def login_submit(
    request_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    pending = await load_pending_request(request_id)
    if pending is None:
        return _expired_page()

    async with db.session() as session:
        service = AuthService(session, get_email_sender())
        try:
            pair = await service.login(email, password)
        except AuthError as exc:
            return HTMLResponse(
                _form(request_id, pending.get("client_name"), error=exc.message)
            )
        claims = decode_token(pair.access_token, "access")

    redirect_url = await store_authorization_code(request_id, claims.user_id)
    if redirect_url is None:
        return _expired_page()
    return RedirectResponse(redirect_url, status_code=302)
