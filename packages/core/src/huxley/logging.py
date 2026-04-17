"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path
    from typing import IO


# Per-run rotation: at startup, the previous run's log is renamed to
# logs/huxley_<that-run's-mtime>.log so the live file is always the
# current run only. Bounded to KEEP_RUNS archives.
KEEP_RUNS = 10


def _rotate_per_run(log_file: Path) -> None:
    """Archive the previous run's log + prune old archives.

    Convention: `logs/huxley.log` is always the current run. On startup,
    if it exists, rename it to `logs/huxley_<iso-timestamp>.log` (the
    timestamp is the file's last-modified time — i.e. when the previous
    run last wrote to it). Then delete oldest archives beyond KEEP_RUNS.

    Failures here are logged but do not crash startup — losing one log
    rotation is far less bad than failing to boot the server.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if log_file.exists():
        try:
            ts = datetime.fromtimestamp(log_file.stat().st_mtime).strftime("%Y%m%dT%H%M%S")
            archive = log_file.parent / f"huxley_{ts}.log"
            # Avoid clobber if multiple restarts happen within one second.
            n = 1
            while archive.exists():
                archive = log_file.parent / f"huxley_{ts}_{n}.log"
                n += 1
            log_file.rename(archive)
        except OSError as exc:
            print(f"[huxley] log rotation failed (will append): {exc}", file=sys.stderr)
            return

    # Prune old archives (sort by name — embedded ISO timestamp gives
    # chronological order naturally).
    try:
        archives = sorted(log_file.parent.glob("huxley_*.log"))
        for old in archives[:-KEEP_RUNS]:
            old.unlink()
    except OSError as exc:
        print(f"[huxley] log archive prune failed: {exc}", file=sys.stderr)


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
        log_file: If set, also write JSON log lines to this file (always JSON for easy
            parsing). The file is per-run: each call to `setup_logging` archives any
            existing file at this path and starts fresh. See `_rotate_per_run`.
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
        # Per-run rotation: previous run's file → logs/huxley_<ts>.log.
        _rotate_per_run(log_file)
        _file_handle = log_file.open("w", encoding="utf-8")
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
