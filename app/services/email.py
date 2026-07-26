"""Outgoing email.

Resend is used over its HTTP API rather than SMTP: k3s egress on port 25/587 is
commonly blocked or rate-limited by the cloud provider, and an HTTP call is far
easier to debug from inside a container.

With `RESEND_API_KEY` unset the console backend logs the message instead, so
local development needs no credentials and no network.
"""

import logging
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.core.settings import settings

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = httpx.Timeout(10.0)


def password_reset_link(token: str) -> str:
    return f"{settings.PASSWORD_RESET_URL}?{urlencode({'token': token})}"


class EmailSender(Protocol):
    async def send_password_reset(self, to: str, token: str) -> None: ...


class ConsoleEmailSender:
    """Development backend: logs the reset link rather than sending it."""

    async def send_password_reset(self, to: str, token: str) -> None:
        logger.warning(
            "[email:console] password reset for %s -> %s",
            to,
            password_reset_link(token),
        )


class ResendEmailSender:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def send_password_reset(self, to: str, token: str) -> None:
        link = password_reset_link(token)
        minutes = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
        payload = {
            # EMAIL_FROM is the complete From value, display name included.
            # Wrapping it again produces `Name <<addr>>`, which Resend rejects.
            "from": settings.EMAIL_FROM,
            "to": [to],
            "subject": "Reset your befit password",
            "html": (
                "<p>Someone asked to reset the password for this address.</p>"
                f'<p><a href="{link}">Choose a new password</a></p>'
                f"<p>The link expires in {minutes} minutes and can be used once. "
                "If this wasn't you, ignore this email.</p>"
            ),
        }

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                settings.RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )

        if not response.is_error:
            # Logged so a successful send is visible: the endpoint returns 202
            # either way, so silence on success makes "no email arrived"
            # impossible to diagnose from the logs alone.
            logger.info(
                "resend accepted the password reset for %s (id=%s)",
                to,
                response.json().get("id"),
            )
            return

        # Logged, not raised: the caller must return the same response whether or
        # not the address exists, so a delivery failure cannot become an
        # account-enumeration signal.
        logger.error(
            "resend rejected the password reset for %s: %s %s",
            to,
            response.status_code,
            response.text,
        )


def get_email_sender() -> EmailSender:
    if settings.RESEND_API_KEY:
        return ResendEmailSender(settings.RESEND_API_KEY)

    if settings.ENVIRONMENT != "development":
        logger.error(
            "RESEND_API_KEY is unset in %s — password reset emails are only "
            "being logged, not delivered",
            settings.ENVIRONMENT,
        )
    return ConsoleEmailSender()
