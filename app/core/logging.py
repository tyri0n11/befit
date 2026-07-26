"""Logging setup for the `app` namespace.

uvicorn configures only its own loggers and leaves the root logger at WARNING, so
without this every `logger.info()` in application code is silently dropped — which
made a successful password reset send invisible in the logs.
"""

import logging
import sys

from app.core.settings import settings

_FORMAT = "%(levelname)s:     [%(name)s] %(message)s"


def configure_logging() -> None:
    """Attach a stdout handler to the `app` logger. Idempotent."""
    logger = logging.getLogger("app")

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)

    logger.setLevel(settings.LOG_LEVEL.upper())
    # The root logger has no handler of ours; propagating would either lose the
    # record or duplicate it under uvicorn's config.
    logger.propagate = False
