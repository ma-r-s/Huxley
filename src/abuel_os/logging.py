"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path
    from typing import IO


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    log_file: Path | None = None,
) -> None:
    """Configure structlog for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, output JSON lines. If False, human-readable console output.
        log_file: If set, also write JSON log lines to this file (always JSON for easy parsing).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    if log_file is not None:
        # Tee: console (human-readable) + file (JSON)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        _file_handle = log_file.open("a", encoding="utf-8")
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.EventRenamer("event"),
                _TeeProcessor(
                    console_renderer=renderer,
                    file_renderer=structlog.processors.JSONRenderer(),
                    file_handle=_file_handle,
                ),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )
    else:
        structlog.configure(
            processors=[
                *shared_processors,
                renderer,
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )


class _TeeProcessor:
    """Renders to both the console and a log file simultaneously."""

    def __init__(
        self,
        console_renderer: structlog.types.Processor,
        file_renderer: structlog.types.Processor,
        file_handle: IO[str],
    ) -> None:
        self._console = console_renderer
        self._file = file_renderer
        self._fh = file_handle

    def __call__(
        self,
        logger: object,
        method: str,
        event_dict: structlog.types.EventDict,
    ) -> str:
        import copy

        file_dict = copy.copy(event_dict)
        file_line = str(self._file(logger, method, file_dict))
        self._fh.write(file_line + "\n")
        self._fh.flush()

        return str(self._console(logger, method, event_dict))
