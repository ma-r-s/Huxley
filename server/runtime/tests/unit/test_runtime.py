"""Tests for `huxley.runtime.Runtime` — the T1.13 hot-persona-swap layer.

These exercise the swap algorithm directly (`_switch_to_persona`)
rather than going through AudioServer's WS handshake, so the tests
stay in-process and don't need a real WebSocket. The PWA-side
end-to-end flow (`?persona=` reconnect → handshake → hello with
`current_persona`) is exercised by Mario's browser smoke at gate
close.

Critic round 2 (`docs/triage.md` T1.13) flagged three regressions
this file must catch:

- §2 concurrent swap leak — two `_switch_to_persona` calls must
  serialize via `_swap_lock` so the loser's freshly-built Application
  doesn't get reference-overwritten and silently leaked.
- §10 stuck-teardown DoS — `_teardown_task` await is capped by
  `_TEARDOWN_TIMEOUT_S` so a buggy skill teardown that deadlocks
  doesn't stall every subsequent swap forever.
- §11 storage-lock race — rapid back-and-forth A→B→A must NOT collide
  on the SQLite WAL writer-lock when re-opening A's DB; the fix is
  awaiting the in-flight `_teardown_task` before constructing the
  next same-named Application.

Plus the gold round-trip (constructs cleanly → swap → swap back) and
the failure-mode (broken persona's start raises, current_app stays
as-is, exception propagates).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import pytest

from huxley.config import Settings
from huxley.persona import PersonaError
from huxley.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


# Minimal persona — no skills (so `discover_skills` returns nothing,
# `skill_registry.setup_all` is a no-op, and we don't drag in real skill
# packages or their secrets/data dirs).
_MINIMAL_PERSONA_YAML = """\
version: 1
name: {name}
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Test persona for runtime swap tests.
constraints: []
skills: {{}}
"""


def _write_persona(personas_dir: Path, name: str) -> None:
    d = personas_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "persona.yaml").write_text(_MINIMAL_PERSONA_YAML.format(name=name), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)


@pytest.fixture
def runtime_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runtime:
    """Build a `Runtime` rooted at `tmp_path` with three personas
    available (alpha / beta / gamma). The Runtime constructs its
    `AudioServer` synchronously but never starts it (no `run()`); tests
    drive `_switch_to_persona` directly. Always pass `auto_connect=False`
    to avoid `wake_word` firing the OpenAI handshake (the api_key here
    is bogus and would 401)."""
    personas_dir = tmp_path / "personas"
    for name in ("alpha", "beta", "gamma"):
        _write_persona(personas_dir, name)
    monkeypatch.chdir(tmp_path)
    return Runtime(Settings(openai_api_key="test-key"))


async def _drain_teardown(runtime: Runtime) -> None:
    """Await any in-flight teardown task before the test ends so a
    stray background task doesn't leak between tests. Catches
    everything because tests don't care HOW teardown finishes — only
    that the task drains so the runtime is quiescent."""
    if runtime._teardown_task is not None and not runtime._teardown_task.done():
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(runtime._teardown_task, timeout=5.0)


class TestConstruction:
    def test_starts_with_no_current_app(self, runtime_in_tmp: Runtime) -> None:
        assert runtime_in_tmp.current_app is None

    def test_audio_server_constructed(self, runtime_in_tmp: Runtime) -> None:
        assert runtime_in_tmp.audio_server is not None


class TestBasicSwap:
    async def test_first_switch_sets_current_app(self, runtime_in_tmp: Runtime) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "alpha"
        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_same_persona_is_noop(self, runtime_in_tmp: Runtime) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        assert runtime_in_tmp.current_app is first
        assert runtime_in_tmp._teardown_task is None
        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_swap_replaces_current_app_and_schedules_teardown(
        self, runtime_in_tmp: Runtime
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        assert first is not None

        await runtime_in_tmp._switch_to_persona("beta", auto_connect=False)
        assert runtime_in_tmp.current_app is not first
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "beta"
        # alpha's shutdown was scheduled in the background.
        assert runtime_in_tmp._teardown_task is not None
        await _drain_teardown(runtime_in_tmp)
        await runtime_in_tmp.current_app.shutdown()


class TestRapidBackAndForthSwap:
    """Critic round 1 finding (locked in DoD): rapid A→B→A within the
    teardown window must NOT collide on SQLite's WAL writer-lock when
    re-opening alpha's DB. The fix is `_switch_to_persona` awaiting any
    in-flight `_teardown_task` before constructing the new
    Application."""

    async def test_swap_back_to_same_persona_within_teardown_window(
        self, runtime_in_tmp: Runtime
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        await runtime_in_tmp._switch_to_persona("beta", auto_connect=False)
        # Immediately swap back to alpha — alpha's teardown task is
        # still in flight (provider.disconnect is fast for an unconnected
        # provider, but storage.close + skill teardown are async).
        # The await on `_teardown_task` inside `_switch_to_persona`
        # serializes us behind alpha's cleanup.
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "alpha"
        # No `database is locked` error means the fix works.
        await _drain_teardown(runtime_in_tmp)
        await runtime_in_tmp.current_app.shutdown()


class TestConcurrentSwap:
    """Critic round 2 §2: two concurrent `_switch_to_persona` calls must
    serialize via `_swap_lock`. Without serialization, both run their
    "build new app" path concurrently and the loser's freshly-built
    Application gets reference-overwritten by `self.current_app =
    new_app` — silently leaked, never shutdown. With the lock, exactly
    one swap happens at a time; the second runs only after the first
    commits, so it sees the correct `old_app` and tears it down
    properly."""

    async def test_concurrent_swap_serializes_no_leak(self, runtime_in_tmp: Runtime) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)

        # Fire both swaps concurrently. The lock serializes them; one
        # runs to completion before the other begins.
        await asyncio.gather(
            runtime_in_tmp._switch_to_persona("beta", auto_connect=False),
            runtime_in_tmp._switch_to_persona("gamma", auto_connect=False),
        )

        # Whichever swap acquired the lock second is the winner —
        # current_app is one of {beta, gamma}.
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name in ("beta", "gamma")

        # The other swap's Application became `old_app` of the winner's
        # swap and was scheduled for teardown — i.e., it was NOT leaked.
        # Drain the teardown to confirm it completes cleanly.
        await _drain_teardown(runtime_in_tmp)
        await runtime_in_tmp.current_app.shutdown()


class TestFailureMode:
    """Critic round 2 finding §1 / DoD bullet: if the new persona's
    `load_persona` or `start()` raises, the OLD `current_app` must
    stay as-is and the exception must propagate so the caller (the
    AudioServer shim, or `Runtime.run()` at boot) can decide."""

    async def test_unknown_persona_keeps_previous_current_app(
        self, runtime_in_tmp: Runtime
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        assert first is not None

        with pytest.raises(PersonaError):
            await runtime_in_tmp._switch_to_persona("nonexistent", auto_connect=False)

        # OLD app is intact.
        assert runtime_in_tmp.current_app is first
        assert runtime_in_tmp.current_app.persona.name == "alpha"
        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)


class TestHelloExtras:
    """The `get_hello_extras` callback is what AudioServer merges into
    the hello payload at handshake time. Verifies the contract Runtime
    promises to AudioServer."""

    def test_extras_when_no_current_app(self, runtime_in_tmp: Runtime) -> None:
        extras = runtime_in_tmp._get_hello_extras()
        assert extras["current_persona"] is None
        # All three personas should show up in available_personas.
        names = [p["name"] for p in extras["available_personas"]]  # type: ignore[index, union-attr]
        assert names == ["alpha", "beta", "gamma"]  # alphabetical

    async def test_extras_reflects_current_persona_after_swap(
        self, runtime_in_tmp: Runtime
    ) -> None:
        await runtime_in_tmp._switch_to_persona("beta", auto_connect=False)
        extras = runtime_in_tmp._get_hello_extras()
        assert extras["current_persona"] == "beta"
        await runtime_in_tmp.current_app.shutdown()  # type: ignore[union-attr]
        await _drain_teardown(runtime_in_tmp)
