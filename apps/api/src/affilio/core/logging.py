"""Structured logging via structlog, with stdlib loggers (uvicorn, sqlalchemy) routed through it."""

from __future__ import annotations

import logging
import sys

import structlog

from affilio.core.config import LogFormat

_CONFIGURED_KEY = "_affilio_logging_configured"


def configure_logging(level: str = "INFO", fmt: LogFormat = "console") -> None:
    """Configure structlog + stdlib logging once per process. Safe to call repeatedly."""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_KEY, False):
        root.setLevel(level)
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: structlog.types.Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Let uvicorn's loggers propagate to the root handler instead of printing twice.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    setattr(root, _CONFIGURED_KEY, True)
