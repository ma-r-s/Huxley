"""The watcher's own log: JSON lines in the same shape as Huxley's
(`level`, `timestamp` ISO UTC, `event` last), so one grep habit reads both.
Event names are `watch.*` (docs/observability.md)."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


class WatchLog:
    """Appends one JSON object per line. A file that can't be opened falls
    back to stderr: losing the log must never stop the watcher."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self.records: list[dict[str, Any]] = []  # kept for tests and --once
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"[huxley-watch] log dir unavailable: {exc}", file=sys.stderr)
                self._path = None

    def _write(self, level: str, event: str, fields: dict[str, Any]) -> None:
        record = {
            **fields,
            "level": level,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event": event,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.records.append(record)
            if len(self.records) > 1000:
                del self.records[:500]
            if self._path is None:
                print(line, file=sys.stderr)
                return
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                print(line, file=sys.stderr)

    def info(self, event: str, **fields: Any) -> None:
        self._write("info", event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._write("warning", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._write("error", event, fields)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r["event"] == name]
