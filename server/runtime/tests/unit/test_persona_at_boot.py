"""Persona at boot (`HUXLEY_PERSONA_AT_BOOT`) and the runtime's `/health`.

Production restarts after power cuts and deploys. With lazy boot (the
default) the persona only starts when the first client connects, so
until the walkie reconnects nothing runs: no reminders, no Telegram,
nothing for the deploy script to check. These tests drive the real
`Runtime.run()` on a free port (logging setup and the shutdown deadline
stubbed out) and check:

- off: unchanged lazy boot, the first connect starts the persona;
- on: the persona starts before any client, and a client connecting
  afterwards (or during the boot) gets it without a second start;
- a boot that fails leaves the server serving, and the next connect retries;
- `/health` before and after a client connects.

`Application.start` is wrapped to record the `auto_connect` it was asked
for, then started without connecting (the api key is fake).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import structlog
from websockets.asyncio.client import connect

from huxley import runtime as runtime_mod
from huxley.app import Application
from huxley.config import Settings
from huxley.loader import ENTRY_POINT_GROUP
from huxley.runtime import Runtime, git_commit
from tests.unit.http_probe import free_port, http_get, until, wait_for_http

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PERSONA_YAML = """\
version: 1
name: {name}
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Test persona for boot tests.
constraints: []
skills: {{}}
"""


class _Starts:
    """What the wrapped `Application.start` saw."""

    def __init__(self) -> None:
        self.attempts = 0
        self.ok: list[bool] = []  # auto_connect of each start that succeeded
        self.personas: list[str] = []  # persona id of each successful start
        self.fail_first = 0
        # Set: a start parks here after the app came up (storage open,
        # skills set up), like a provider connect that never returns.
        self.hold_after_start: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.shutdowns = 0


@pytest.fixture
def starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Starts:
    for name in ("alpha", "beta"):
        d = tmp_path / "personas" / name
        (d / "data").mkdir(parents=True)
        (d / "persona.yaml").write_text(_PERSONA_YAML.format(name=name), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("huxley.runtime.setup_logging", lambda **_: None)
    # A test that fails with the boot still held must not spend the real
    # 10 s settling it at shutdown.
    monkeypatch.setattr(Runtime, "_TEARDOWN_TIMEOUT_S", 1.0)
    rec = _Starts()

    class _Wrapped(Application):
        async def start(self, *, auto_connect: bool = True) -> None:
            rec.attempts += 1
            if rec.attempts <= rec.fail_first:
                msg = "start failed (test)"
                raise RuntimeError(msg)
            await super().start(auto_connect=False)
            rec.started.set()
            if rec.hold_after_start is not None:
                await rec.hold_after_start.wait()
            rec.ok.append(auto_connect)
            rec.personas.append(self.persona.data_dir.parent.name)

        async def shutdown(self) -> None:
            rec.shutdowns += 1
            await super().shutdown()

    monkeypatch.setattr("huxley.runtime.Application", _Wrapped)
    return rec


def _gate_boot_swap(runtime: Runtime) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold the boot's swap before it takes the swap lock: the window in
    which a connect would otherwise see no persona and no swap running.
    Returns (entered, release)."""
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime._switch_to_persona
    calls = {"n": 0}

    async def _gated(name: str, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:  # the boot's
            entered.set()
            await release.wait()
        await original(name, **kwargs)

    runtime._switch_to_persona = _gated  # type: ignore[method-assign]
    return entered, release


def _runtime(*, persona: str | None, at_boot: bool, port: int) -> Runtime:
    runtime = Runtime(
        Settings(
            openai_api_key="test-key",
            persona=persona,
            persona_at_boot=at_boot,
            server_host="127.0.0.1",
            server_port=port,
        )
    )
    # The real one exits the process 20 s after shutdown starts if it
    # hasn't finished: that would be pytest.
    runtime._arm_shutdown_deadline = lambda: None  # type: ignore[method-assign]
    return runtime


@contextlib.asynccontextmanager
async def _running(
    runtime: Runtime, port: int, *, release: asyncio.Event | None = None
) -> AsyncIterator[None]:
    """Run the server for the block. `release`: a held boot, let go on the
    way out so a failing test still shuts down promptly."""
    task = asyncio.create_task(runtime.run())
    try:
        await wait_for_http(port)
        yield
    finally:
        if release is not None:
            release.set()
        runtime._signal_shutdown()
        await asyncio.wait_for(task, timeout=10)


async def _health(port: int) -> dict[str, Any]:
    reply = await http_get(port, "/health")
    assert reply.status == 200, reply
    data: dict[str, Any] = reply.json()
    return data


async def _hello(ws: Any) -> dict[str, Any]:
    """The hello. A connect that starts the persona (lazy boot) gets the
    swap's `skills_state` push first."""
    while True:
        msg: dict[str, Any] = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        if msg["type"] == "hello":
            return msg


def _events(logs: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in logs]


class TestOff:
    async def test_lazy_boot_unchanged(self, starts: _Starts) -> None:
        port = free_port()
        runtime = _runtime(persona="alpha", at_boot=False, port=port)
        with structlog.testing.capture_logs() as logs:
            async with _running(runtime, port):
                health = await _health(port)
                assert runtime.current_app is None
                assert starts.attempts == 0
                assert health["persona"] is None
                assert health["ok"] is False
                assert health["session"] is None
                assert health["skills"] == {}
                async with connect(f"ws://127.0.0.1:{port}/") as ws:
                    hello = await _hello(ws)
                assert hello["current_persona"] == "alpha"
                assert hello["session_ready"] is True
                # The first connect started it, eagerly connecting.
                assert starts.ok == [True]
        assert not [e for e in _events(logs) if e.startswith("runtime.persona_boot")]


class TestOn:
    async def test_persona_runs_before_any_client(self, starts: _Starts) -> None:
        port = free_port()
        runtime = _runtime(persona="alpha", at_boot=True, port=port)
        with structlog.testing.capture_logs() as logs:
            async with _running(runtime, port):
                await until(lambda: "runtime.persona_boot_ready" in _events(logs))
                assert runtime.current_app is not None
                assert not runtime.audio_server.has_client
                assert starts.ok == [True]  # same path as a first connect: auto-connect

                before = await _health(port)
                assert before["persona"] == "alpha"
                assert before["session"] == {"state": "IDLE", "provider_connected": False}
                assert before["ok"] is False  # the stub never connects the provider
                assert before["client"] is None
                assert before["walkie_last_seen"] is None
                assert before["recording"] is False
                assert before["pid"] == os.getpid()
                assert before["uptime_s"] >= 0
                datetime.fromisoformat(before["started_at"])
                assert before["commit"] == git_commit(Path(runtime_mod.__file__).resolve().parent)
                installed = {ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)}
                assert before["skills"] == dict.fromkeys(sorted(installed), "disabled")

                async with connect(f"ws://127.0.0.1:{port}/") as ws:
                    hello = await _hello(ws)
                    assert hello["current_persona"] == "alpha"
                    assert hello["session_ready"] is True
                    after = await _health(port)
                    assert after["client"]["connected"] is True
                    assert after["client"]["conn_id"] == hello["conn_id"]
                    assert after["persona"] == "alpha"
                # The client got the running persona: no second start.
                assert starts.attempts == 1
        events = _events(logs)
        assert events.index("runtime.persona_boot_started") < events.index(
            "runtime.persona_boot_ready"
        )
        # Nobody was connected when it came up: the app was told so.
        presence = [e for e in logs if e["event"] == "coord.client_presence"]
        assert [e["present"] for e in presence][:2] == [False, True]

    async def test_connect_during_boot_waits_for_it(self, starts: _Starts) -> None:
        port = free_port()
        runtime = _runtime(persona="alpha", at_boot=True, port=port)
        entered, release = _gate_boot_swap(runtime)
        with structlog.testing.capture_logs() as logs:
            async with _running(runtime, port, release=release):
                await asyncio.wait_for(entered.wait(), timeout=5)
                async with connect(f"ws://127.0.0.1:{port}/") as ws:
                    hello_task = asyncio.create_task(_hello(ws))
                    await until(lambda: "runtime.connect_waits_for_persona_boot" in _events(logs))
                    # Without the wait the connect would have lazily started
                    # the persona itself by now (it takes ~50 ms).
                    await asyncio.sleep(0.2)
                    assert not hello_task.done()
                    assert starts.attempts == 0
                    release.set()
                    hello = await hello_task
                    assert hello["current_persona"] == "alpha"
                    assert hello["session_ready"] is True
                assert starts.attempts == 1
        # A client was there when the boot finished: no "nobody listens"
        # notice; the first presence the app hears is the client's.
        presence = [e["present"] for e in logs if e["event"] == "coord.client_presence"]
        assert presence[0] is True

    async def test_connect_for_another_persona_during_boot_ends_on_it(
        self, starts: _Starts
    ) -> None:
        """The boot finishes first, then the client's `?persona=` swaps, as
        on any later connect. Without the wait, the client's lazy start
        would be swapped away by the boot finishing after it."""
        port = free_port()
        runtime = _runtime(persona="alpha", at_boot=True, port=port)
        entered, release = _gate_boot_swap(runtime)
        async with _running(runtime, port, release=release):
            await asyncio.wait_for(entered.wait(), timeout=5)
            async with connect(f"ws://127.0.0.1:{port}/?persona=beta") as ws:
                hello_task = asyncio.create_task(_hello(ws))
                await asyncio.sleep(0.2)
                release.set()
                hello = await hello_task
                assert hello["current_persona"] == "beta"
                boot = runtime._boot_task
                await until(lambda: boot is not None and boot.done())
                assert runtime.current_app is not None
                assert runtime.current_app.persona.data_dir.parent.name == "beta"
            assert starts.personas == ["alpha", "beta"]

    async def test_shutdown_during_boot_with_a_client_waiting(
        self, starts: _Starts, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deploy restarts the server and the walkie reconnects at once,
        during the boot; then a stop comes before the boot finishes. The
        stop must not hang on the parked connection, start nothing more,
        and close what the half-started app opened."""
        monkeypatch.setattr(Runtime, "_TEARDOWN_TIMEOUT_S", 0.3)
        starts.hold_after_start = asyncio.Event()  # never set: stuck
        port = free_port()
        runtime = _runtime(persona="alpha", at_boot=True, port=port)
        with structlog.testing.capture_logs() as logs:
            task = asyncio.create_task(runtime.run())
            try:
                await wait_for_http(port)
                await asyncio.wait_for(starts.started.wait(), timeout=5)
                async with connect(f"ws://127.0.0.1:{port}/"):
                    await until(lambda: runtime.audio_server.has_client)
                    await asyncio.sleep(0.1)  # its handler parks on the boot
                    runtime._signal_shutdown()
                    await asyncio.wait_for(asyncio.shield(task), timeout=5)
            finally:
                # On a failure above, unwind instead of hanging the loop.
                starts.hold_after_start.set()
                runtime._signal_shutdown()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(task, timeout=10)
        events = _events(logs)
        assert "runtime.persona_boot_abandoned" in events
        assert "runtime.persona_start_cancelled" in events
        assert "runtime.persona_select_skipped" in events
        assert "runtime.session_ready" not in events
        assert starts.attempts == 1  # nothing started during the stop
        assert starts.shutdowns == 1  # the half-started app was closed
        assert runtime.current_app is None

    async def test_failed_boot_keeps_serving_and_next_connect_retries(
        self, starts: _Starts
    ) -> None:
        starts.fail_first = 1
        port = free_port()
        runtime = _runtime(persona="alpha", at_boot=True, port=port)
        with structlog.testing.capture_logs() as logs:
            async with _running(runtime, port):
                await until(lambda: "runtime.persona_boot_failed" in _events(logs))
                assert runtime.current_app is None
                health = await _health(port)
                assert health["persona"] is None
                assert health["ok"] is False
                async with connect(f"ws://127.0.0.1:{port}/") as ws:
                    hello = await _hello(ws)
                assert hello["current_persona"] == "alpha"
                assert hello["session_ready"] is True
                assert starts.attempts == 2
                assert starts.ok == [True]
        failed = next(e for e in logs if e["event"] == "runtime.persona_boot_failed")
        assert failed["persona"] == "alpha"
        assert "RuntimeError" in failed["error"]
        assert "runtime.persona_boot_ready" not in _events(logs)

    async def test_without_a_configured_persona_it_boots_lazily(self, starts: _Starts) -> None:
        port = free_port()
        runtime = _runtime(persona=None, at_boot=True, port=port)
        with structlog.testing.capture_logs() as logs:
            async with _running(runtime, port):
                await until(lambda: "runtime.persona_boot_skipped" in _events(logs))
                assert runtime.current_app is None
                assert starts.attempts == 0
                assert (await _health(port))["persona"] is None


class TestHealthFields:
    def _app(self, *, connected: bool, state: str = "CONVERSING") -> Any:
        return SimpleNamespace(
            provider=SimpleNamespace(is_connected=connected),
            state_machine=SimpleNamespace(state=SimpleNamespace(name=state)),
            persona=SimpleNamespace(data_dir=Path("/x/personas/abuelos/data")),
            skill_registry=SimpleNamespace(
                skill_names=["audiobooks", "reminders"], disabled_skill_names=["telegram"]
            ),
        )

    def test_ok_needs_a_persona_and_a_connected_session(self, tmp_path: Path) -> None:
        runtime = Runtime(Settings(openai_api_key="test-key"))
        runtime._installed_skills = ["audiobooks", "reminders", "stocks", "telegram"]
        assert runtime._get_health()["ok"] is False  # no persona

        runtime.current_app = self._app(connected=False, state="IDLE")
        health = runtime._get_health()
        assert health["ok"] is False
        assert health["session"] == {"state": "IDLE", "provider_connected": False}

        runtime.current_app = self._app(connected=True)
        health = runtime._get_health()
        assert health["ok"] is True
        assert health["persona"] == "abuelos"
        assert health["session"] == {"state": "CONVERSING", "provider_connected": True}
        assert health["skills"] == {
            "audiobooks": "ok",
            "reminders": "ok",
            "stocks": "disabled",
            "telegram": "failed",
        }


class TestGitCommit:
    def test_outside_a_checkout_is_none(self, tmp_path: Path) -> None:
        assert git_commit(tmp_path) is None

    @pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
    def test_short_head_of_the_checkout(self, tmp_path: Path) -> None:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", "x")
        (tmp_path / "pkg").mkdir()
        assert git_commit(tmp_path / "pkg") == git("rev-parse", "--short", "HEAD")
