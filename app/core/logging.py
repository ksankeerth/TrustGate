"""Logging setup for the service.

Without this the application's own log records go nowhere under uvicorn: it
configures its own loggers and leaves the root logger without a handler, so
every logger.info/warning in this codebase — the sync-tier timing breakdown,
ThunderID retry warnings, layer failure traces — is silently discarded.
"""

import logging
import os

DEFAULT_LEVEL = "INFO"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Only this project's loggers are configured. Third-party libraries keep their
# own levels, so turning this up does not drown the output in torch/httpx noise.
APP_LOGGER = "app"


def configure_logging(level: str | None = None) -> None:
    """Attach a stream handler to the application's logger namespace.

    Idempotent: calling it twice does not double up handlers, which would
    otherwise print every record twice under a reloading server.
    """
    resolved = (level or os.getenv("TRUSTGATE_LOG_LEVEL") or DEFAULT_LEVEL).upper()

    app_logger = logging.getLogger(APP_LOGGER)
    app_logger.setLevel(resolved)

    if not any(getattr(h, "_trustgate", False) for h in app_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        handler._trustgate = True  # marker so a second call is a no-op
        app_logger.addHandler(handler)

    # uvicorn owns the root logger; not propagating avoids duplicate lines.
    app_logger.propagate = False
