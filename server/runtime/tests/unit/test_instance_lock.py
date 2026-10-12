"""One server per port: a restart waits for the previous process.

2026-10-12: a new server started while the old one was still shutting
down and found the Telegram session locked. `huxley.instance_lock`
makes the new process wait (bounded) for the old one's `flock` to be
released. flock conflicts between two open file descriptions even in
one process, so a second `InstanceLock` here stands in for the old
server.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import TYPE_CHECKING

import pytest
import structlog

import huxley.__main__ as entry
from huxley.config import Settings
from huxley.instance_lock import (
    InstanceLock,
    InstanceLockTimeoutError,
    acquire_instance_lock,
    lock_path_for_port,
)
from huxley.persona import PersonaError
from huxley.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


def test_lock_path_is_per_port_under_logs(tmp_path: Path) -> None:
    assert lock_path_for_port(8765, tmp_path) == tmp_path / "logs" / "huxley-8765.lock"
    assert lock_path_for_port(8765, tmp_path) != lock_path_for_port(8766, tmp_path)


async def test_free_lock_is_taken_at_once_and_records_our_pid(tmp_path: Path) -> None:
    path = lock_path_for_port(8765, tmp_path)  # logs/ does not exist yet
    waits: list[int | None] = []

    lock, waited_s, previous_pid = await acquire_instance_lock(
        path, timeout_s=1.0, on_wait=waits.append
    )

    assert lock.held
    assert waited_s == 0.0
    assert previous_pid is None
    assert waits == []
    assert lock.holder_pid() == os.getpid()
    lock.release()


async def test_waits_for_the_previous_holder_then_takes_over(tmp_path: Path) -> None:
    path = lock_path_for_port(8765, tmp_path)
    old_server = InstanceLock(path)
    assert old_server.try_acquire()
    waits: list[int | None] = []

    async def _old_server_exits() -> None:
        await asyncio.sleep(0.15)
        old_server.release()

    exit_task = asyncio.create_task(_old_server_exits())
    lock, waited_s, previous_pid = await acquire_instance_lock(
        path, timeout_s=5.0, poll_s=0.02, on_wait=waits.append
    )
    await exit_task

    assert lock.held
    assert waited_s >= 0.1
    assert previous_pid == os.getpid()  # the "old server" wrote its pid
    assert waits == [os.getpid()]  # told the operator once, before waiting
    lock.release()


async def test_lock_is_released_when_the_holding_process_exits(tmp_path: Path) -> None:
    """The real shape: another process holds the lock and exits without
    releasing it (as a crash or `os._exit` would). The kernel drops the
    flock with the process, so the waiter takes over."""
    path = lock_path_for_port(8765, tmp_path)
    holder_script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from huxley.instance_lock import InstanceLock\n"
        "lock = InstanceLock(Path(sys.argv[1]))\n"
        "assert lock.try_acquire()\n"
        "print('held', flush=True)\n"
        "time.sleep(0.3)\n"
        "import os; os._exit(0)\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", holder_script, str(path), stdout=asyncio.subprocess.PIPE
    )
    assert proc.stdout is not None
    assert (await proc.stdout.readline()).strip() == b"held"

    lock, waited_s, previous_pid = await acquire_instance_lock(path, timeout_s=10.0, poll_s=0.02)
    await proc.wait()

    assert lock.held
    assert waited_s > 0
    assert previous_pid == proc.pid
    lock.release()


async def test_gives_up_after_the_timeout(tmp_path: Path) -> None:
    path = lock_path_for_port(8765, tmp_path)
    old_server = InstanceLock(path)
    assert old_server.try_acquire()

    with pytest.raises(InstanceLockTimeoutError) as info:
        await acquire_instance_lock(path, timeout_s=0.1, poll_s=0.02)

    assert info.value.holder_pid == os.getpid()
    assert str(path) in str(info.value)
    old_server.release()


async def test_other_port_is_not_blocked(tmp_path: Path) -> None:
    """The documented A/B run (a second persona on 8766 from the same
    directory) must keep working."""
    first = InstanceLock(lock_path_for_port(8765, tmp_path))
    assert first.try_acquire()

    second, waited_s, _ = await acquire_instance_lock(
        lock_path_for_port(8766, tmp_path), timeout_s=0.1
    )

    assert second.held
    assert waited_s == 0.0
    first.release()
    second.release()


def test_release_lets_the_next_holder_in(tmp_path: Path) -> None:
    path = lock_path_for_port(8765, tmp_path)
    a = InstanceLock(path)
    b = InstanceLock(path)
    assert a.try_acquire()
    assert not b.try_acquire()
    a.release()
    assert b.try_acquire()
    b.release()


# --- How the runtime and the entry point use it ---------------------------


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runtime:
    monkeypatch.chdir(tmp_path)
    return Runtime(Settings(openai_api_key="test-key"))


async def test_run_takes_the_lock_before_logging(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start that gives up must not rotate the log first: the log it
    would archive belongs to the process it was waiting for."""
    logging_calls: list[object] = []

    async def _timeout(path: Path, **_: object) -> None:
        raise InstanceLockTimeoutError(path, 4242, 30.0)

    monkeypatch.setattr("huxley.runtime.acquire_instance_lock", _timeout)
    monkeypatch.setattr("huxley.runtime.setup_logging", lambda **kw: logging_calls.append(kw))

    with pytest.raises(InstanceLockTimeoutError):
        await runtime.run()
    assert logging_calls == []


async def test_run_without_a_usable_lock_file_still_starts(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock that can't be created (read-only directory, no flock) must
    not keep the assistant down: run unguarded and say so in the log."""

    async def _no_lock(path: Path, **_: object) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr("huxley.runtime.acquire_instance_lock", _no_lock)
    monkeypatch.setattr("huxley.runtime.setup_logging", lambda **_: None)
    # Stop right after the lock step: no personas -> PersonaError.
    monkeypatch.setattr("huxley.runtime.pick_default_persona_name", lambda **_: None)

    with structlog.testing.capture_logs() as logs, pytest.raises(PersonaError):
        await runtime.run()
    events = [e["event"] for e in logs]
    assert "runtime.instance_lock_unavailable" in events
    assert "runtime.instance_lock_acquired" not in events


def test_shutdown_deadline_exits_1(runtime: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    exits: list[int] = []
    monkeypatch.setattr("huxley.runtime.os._exit", exits.append)

    with structlog.testing.capture_logs() as logs:
        runtime._shutdown_deadline_expired()

    assert exits == [1]
    assert [e["event"] for e in logs] == ["runtime.shutdown_timeout"]


def test_shutdown_deadline_finishes_a_requested_restart(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    execs: list[tuple[str, list[str]]] = []
    exits: list[int] = []
    monkeypatch.setattr("huxley.runtime.os.execv", lambda path, argv: execs.append((path, argv)))
    monkeypatch.setattr("huxley.runtime.os._exit", exits.append)
    runtime._restart_after_shutdown = True

    runtime._shutdown_deadline_expired()

    assert execs == [(sys.argv[0], sys.argv)]


async def test_shutdown_deadline_fires_on_a_timer(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    fired = threading.Event()
    monkeypatch.setattr(Runtime, "_SHUTDOWN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runtime, "_shutdown_deadline_expired", fired.set)

    runtime._arm_shutdown_deadline()

    assert await asyncio.to_thread(fired.wait, 2.0)


def test_main_exits_1_when_the_old_server_never_lets_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under launchd a non-zero exit means KeepAlive tries again later."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HUXLEY_OPENAI_API_KEY", "test-key")

    class _StuckRuntime:
        def __init__(self, _config: object) -> None:
            pass

        async def run(self) -> None:
            raise InstanceLockTimeoutError(tmp_path / "logs" / "huxley-8765.lock", 4242, 30.0)

    monkeypatch.setattr("huxley.runtime.Runtime", _StuckRuntime)

    with pytest.raises(SystemExit) as info:
        entry.main()

    assert info.value.code == 1
    assert "not starting" in capsys.readouterr().err
