"""One server per port per working directory: a restart waits for the old one.

2026-10-12: a server was started while the previous process was still
shutting down (5.6 s, most of it the session summary). The old process
had already closed its listener, so the port was free, but it still held
the persona's SQLite files and the Telegram session; the new process's
Telegram setup failed on the locked session and took the persona down.

The guard is an exclusive `flock` on `logs/huxley-<port>.lock` (relative
to the working directory, like the default log file) taken before the
server logs or listens. The kernel releases it when the holding process
exits for any reason, crash and SIGKILL included, so a stale file can
never block a start. Python opens files close-on-exec, so the
`os.execv` restart path releases it too and the new image takes it
again. Keyed by port so the documented same-directory A/B run on another
port still works.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class InstanceLockTimeoutError(RuntimeError):
    """The previous server still held the lock when the wait ran out."""

    def __init__(self, path: Path, holder_pid: int | None, timeout_s: float) -> None:
        self.path = path
        self.holder_pid = holder_pid
        self.timeout_s = timeout_s
        super().__init__(
            f"another Huxley server (pid {holder_pid}) still holds {path} "
            f"after {timeout_s:.0f} s; stop it and start again"
        )


def lock_path_for_port(port: int, base_dir: Path) -> Path:
    """Where the lock for the server on `port` lives under `base_dir`."""
    return base_dir / "logs" / f"huxley-{port}.lock"


class InstanceLock:
    """An exclusive, non-blocking `flock` on one file, held while open.

    Keep a reference for the life of the process: if the object is
    garbage-collected its file closes and the lock goes with it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock if it is free. On success, write our pid into the
        file so a waiting process can say who it is waiting for."""
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return True

    def holder_pid(self) -> int | None:
        """The pid the current holder wrote, if readable."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def release(self) -> None:
        if self._fd is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


async def acquire_instance_lock(
    path: Path,
    *,
    timeout_s: float,
    poll_s: float = 0.2,
    on_wait: Callable[[int | None], None] | None = None,
) -> tuple[InstanceLock, float, int | None]:
    """Take the lock at `path`, waiting up to `timeout_s` for a previous
    holder to exit. Returns `(lock, waited_s, previous_pid)`; `previous_pid`
    is None when the lock was free at once.

    `on_wait(holder_pid)` is called once when the lock is busy, so the
    caller can say what it is waiting for before the wait starts (the
    runtime prints to stderr because logging is not set up yet). Raises
    `InstanceLockTimeoutError` if the holder outlives the wait.
    """
    lock = InstanceLock(path)
    if lock.try_acquire():
        return lock, 0.0, None
    holder = lock.holder_pid()
    if on_wait is not None:
        on_wait(holder)
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        await asyncio.sleep(poll_s)
        if lock.try_acquire():
            return lock, time.monotonic() - started, holder
    raise InstanceLockTimeoutError(path, holder, timeout_s)
