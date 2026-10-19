"""Structured logging for the Groww Review Pulse Agent using structlog.

Outputs structured JSON in production / non-interactive runs, with context
binding for run_id, week, step, and source.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import FilteringBoundLogger


def configure_logging(
    level: str = "INFO",
    json_format: bool = True,
) -> None:
    """Configures structlog and standard library logging.

    Args:
        level: Log level (e.g. "DEBUG", "INFO", "WARNING", "ERROR").
        json_format: If True, renders logs as JSON lines. If False, renders colored console output.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Any
    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Returns a structlog bound logger."""
    logger_instance: FilteringBoundLogger = structlog.get_logger(name or "pulse")
    return logger_instance


def bind_run_context(
    run_id: str | None = None,
    week: str | None = None,
    step: str | None = None,
    source: str | None = None,
) -> None:
    """Binds run context variables into structlog contextvars for subsequent logs."""
    context: dict[str, Any] = {}
    if run_id:
        context["run_id"] = run_id
    if week:
        context["week"] = week
    if step:
        context["step"] = step
    if source:
        context["source"] = source
    if context:
        structlog.contextvars.bind_contextvars(**context)


def clear_run_context() -> None:
    """Clears structlog contextvars."""
    structlog.contextvars.clear_contextvars()
