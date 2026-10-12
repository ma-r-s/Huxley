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
from typing import TYPE_CHECKING, Any

import pytest
import structlog

from huxley.app import Application
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


def _record_app_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_starts: int = 0,
    fail_after: int = 0,
    hold_shutdown: asyncio.Event | None = None,
) -> list[tuple[str, int]]:
    """Swap the runtime's `Application` for a subclass that records
    start / shutdown order. The first `fail_after` starts succeed, the
    next `fail_starts` raise, later ones succeed. With `hold_shutdown`,
    every shutdown waits for that event before running."""
    events: list[tuple[str, int]] = []
    starts = {"n": 0}

    class _Recording(Application):
        async def start(self, *, auto_connect: bool = True) -> None:
            starts["n"] += 1
            if fail_after < starts["n"] <= fail_after + fail_starts:
                msg = "start failed (test)"
                raise RuntimeError(msg)
            events.append(("start", id(self)))
            await super().start(auto_connect=auto_connect)

        async def shutdown(self) -> None:
            if hold_shutdown is not None:
                await hold_shutdown.wait()
            events.append(("shutdown_begin", id(self)))
            await super().shutdown()
            events.append(("shutdown_end", id(self)))

    monkeypatch.setattr("huxley.runtime.Application", _Recording)
    return events


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

    async def test_same_persona_noop_when_dir_differs_from_yaml_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1.19 regression: the same-persona short-circuit must
        compare against the directory basename (the canonical id
        `?persona=` resolves against), NOT `PersonaSpec.name` which
        is the YAML's display label.

        The existing `test_same_persona_is_noop` shadows this bug
        because the test fixture writes YAML with `name: {dir}` so
        directory basename and label happen to agree. This test
        creates a persona where they diverge (dir=basic,
        yaml.name="Basic" — exactly the shape of the four shipped
        personas) and asserts the second `_switch_to_persona("basic")`
        no-ops instead of running a full swap with teardown."""
        personas_dir = tmp_path / "personas"
        d = personas_dir / "basic"
        d.mkdir(parents=True)
        (d / "persona.yaml").write_text(
            _MINIMAL_PERSONA_YAML.format(name="Basic"), encoding="utf-8"
        )
        (d / "data").mkdir()
        monkeypatch.chdir(tmp_path)

        runtime = Runtime(Settings(openai_api_key="test-key"))
        await runtime._switch_to_persona("basic", auto_connect=False)
        first = runtime.current_app
        assert first is not None
        # Sanity: dir basename and YAML label disagree, otherwise
        # this test trivially passes via the existing test's path.
        assert first.persona.name == "Basic"
        assert first.persona.data_dir.parent.name == "basic"

        # Second swap to the SAME persona via its canonical id.
        # Pre-T1.19 this fired a full swap (Realtime handshake +
        # skill re-setup); post-T1.19 it short-circuits.
        await runtime._switch_to_persona("basic", auto_connect=False)
        assert runtime.current_app is first
        assert runtime._teardown_task is None

        await runtime.current_app.shutdown()
        await _drain_teardown(runtime)

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


class TestLazyBoot:
    """Lazy boot: Runtime.run() doesn't pre-pick a persona. The first
    WS connection's `_shim_persona_select` does, falling back to
    `pick_default_persona_name` when no `?persona=` query param is
    supplied. This removes the boot-time HUXLEY_PERSONA= ceremony — the
    PWA picker becomes the single source of truth for which persona is
    active."""

    async def test_first_connect_with_persona_query_sets_current_app(
        self, runtime_in_tmp: Runtime
    ) -> None:
        # Server boots with no current_app; first WS connection arrives
        # with `?persona=beta`; shim brings beta up.
        assert runtime_in_tmp.current_app is None
        await runtime_in_tmp._shim_persona_select("beta", language=None)
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "beta"
        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_first_connect_without_persona_query_falls_back_to_default(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No HUXLEY_PERSONA env var, no `?persona=` query → shim falls
        # back to pick_default_persona_name. With three personas
        # (alpha/beta/gamma) and no env, that's "alpha" (alphabetic
        # first, with a loud warning logged at the picker level).
        monkeypatch.delenv("HUXLEY_PERSONA", raising=False)
        assert runtime_in_tmp.current_app is None
        await runtime_in_tmp._shim_persona_select(None, language=None)
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "alpha"
        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_lazy_boot_no_personas_logs_and_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Edge case: a personas dir is created with NO valid persona
        # (e.g. someone deleted them between boot and first connect).
        # Shim should log warning and return without raising — the WS
        # handshake proceeds with no current_app and the client sees
        # `current_persona: null` in the hello extras.
        (tmp_path / "personas").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HUXLEY_PERSONA", raising=False)
        runtime = Runtime(Settings(openai_api_key="test-key"))
        await runtime._shim_persona_select(None, language=None)
        assert runtime.current_app is None  # no crash, no swap


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


class TestReloadCurrentPersona:
    """Marketplace v2 Phase B: writes to persona.yaml or per-skill
    secrets must take effect without a process restart. The runtime
    achieves this by re-running `_switch_to_persona` against the
    *current* persona name with `force=True`, which bypasses the
    same-name short-circuit and re-reads persona.yaml + re-runs
    setup_all on every skill."""

    async def test_force_bypasses_same_persona_shortcircuit(self, runtime_in_tmp: Runtime) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        assert first is not None
        # Without force, same-name is a no-op (Application identity
        # preserved) — see test_same_persona_is_noop above.
        # WITH force, the existing app is torn down + a freshly-built
        # one takes its place.
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False, force=True)
        assert runtime_in_tmp.current_app is not first
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "alpha"
        # The previous app was torn down before the new one started,
        # not leaked and not left running in the background.
        assert first._shutting_down
        assert runtime_in_tmp._teardown_task is not None
        assert runtime_in_tmp._teardown_task.done()
        await runtime_in_tmp.current_app.shutdown()

    async def test_reload_stops_old_app_before_new_one_starts(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A same-persona reload must not run two apps on one persona:
        they would share the SQLite DB and the Telegram session, which a
        live client keeps write-locked, so the new Telegram setup could
        never start (it would be disabled for the rest of the run).

        These reload tests drive `_switch_to_persona(force=True,
        auto_connect=False)`, the call `_reload_current_persona` makes
        minus the provider connect, so nothing reaches the network."""
        events = _record_app_lifecycle(monkeypatch)
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False, force=True)
        second = runtime_in_tmp.current_app
        assert second is not None
        assert second is not first

        assert events == [
            ("start", id(first)),
            ("shutdown_begin", id(first)),
            ("shutdown_end", id(first)),
            ("start", id(second)),
        ]
        await second.shutdown()

    async def test_failed_reload_restores_the_previous_persona(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old app is already gone when the new one fails to start.
        The runtime brings the persona back as it was before the edit
        instead of leaving the user with no assistant, and the failure
        still propagates so `_reload_current_persona` logs it."""
        _record_app_lifecycle(monkeypatch, fail_starts=1, fail_after=1)
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        assert first is not None

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(RuntimeError, match="start failed"),
        ):
            await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False, force=True)

        restored = runtime_in_tmp.current_app
        assert restored is not None
        assert restored is not first
        assert restored.persona is first.persona  # the pre-edit spec
        assert "runtime.reload_restored_previous" in [e["event"] for e in logs]
        assert runtime_in_tmp._get_hello_extras()["session_ready"] is True
        await restored.shutdown()

    async def test_failed_reload_and_restore_marks_session_not_ready(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_app_lifecycle(monkeypatch, fail_starts=2, fail_after=1)
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)

        with pytest.raises(RuntimeError, match="start failed"):
            await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False, force=True)

        assert runtime_in_tmp.current_app is None
        extras = runtime_in_tmp._get_hello_extras()
        assert extras["session_ready"] is False
        assert "alpha" in str(extras["session_error"])

    async def test_connect_during_reload_waits_instead_of_lazy_booting(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """While a reload runs, `current_app` is None. A connect in that
        gap must wait for the reload, not take the lazy-boot path and
        swap to the default persona (alpha) afterwards."""
        release = asyncio.Event()
        _record_app_lifecycle(monkeypatch, hold_shutdown=release)
        await runtime_in_tmp._switch_to_persona("beta", auto_connect=False)

        reload_task = asyncio.create_task(
            runtime_in_tmp._switch_to_persona("beta", auto_connect=False, force=True)
        )
        for _ in range(50):
            if runtime_in_tmp.current_app is None:
                break
            await asyncio.sleep(0.01)
        assert runtime_in_tmp.current_app is None  # inside the gap

        connect_task = asyncio.create_task(runtime_in_tmp._shim_persona_select(None, None))
        await asyncio.sleep(0.05)
        assert not connect_task.done()  # waiting on the reload
        release.set()
        await reload_task
        await connect_task

        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.data_dir.parent.name == "beta"
        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_reload_helper_is_noop_when_no_current_app(
        self, runtime_in_tmp: Runtime
    ) -> None:
        # Lazy-boot window: server is up but no persona selected yet.
        # _reload_current_persona must NOT raise — it just logs and
        # returns. (A WS write during this window can't happen anyway
        # because the panel is empty, but defense in depth.)
        assert runtime_in_tmp.current_app is None
        await runtime_in_tmp._reload_current_persona()
        assert runtime_in_tmp.current_app is None

    async def test_reload_helper_reloads_current_persona(self, runtime_in_tmp: Runtime) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        first = runtime_in_tmp.current_app
        await runtime_in_tmp._reload_current_persona()
        assert runtime_in_tmp.current_app is not first
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "alpha"
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

    async def test_current_persona_is_directory_basename_not_yaml_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: post-T1.13 fix locked down PersonaSummary.name to be
        the directory basename (the canonical id ?persona= resolves
        against), but `current_persona` in the hello extras kept reading
        PersonaSpec.name (the YAML's display label). When directory
        basename ≠ YAML name (e.g. dir=basic, yaml.name="Basic"), the
        hello pushed `Basic` while the picker compared against
        available_personas[].name == "basic" — so the active row
        highlight broke after every swap.

        This test creates a persona where the two differ and asserts the
        hello uses the directory basename. The earlier
        `test_extras_reflects_current_persona_after_swap` test shadowed
        this bug because its fixture writes YAML with `name: {dir}` —
        directory and label happen to agree.
        """
        personas_dir = tmp_path / "personas"
        # Directory "basic", but YAML name is "Basic" (the display
        # label) — mirrors the real basic/persona.yaml shape that
        # tripped Mario's voice smoke.
        d = personas_dir / "basic"
        d.mkdir(parents=True)
        (d / "persona.yaml").write_text(
            _MINIMAL_PERSONA_YAML.format(name="Basic"), encoding="utf-8"
        )
        (d / "data").mkdir()
        monkeypatch.chdir(tmp_path)

        runtime = Runtime(Settings(openai_api_key="test-key"))
        await runtime._switch_to_persona("basic", auto_connect=False)
        try:
            extras = runtime._get_hello_extras()
            # Must be the directory basename, not the YAML label.
            assert extras["current_persona"] == "basic"
            # And the available_personas entry uses the same id (already
            # verified by the post-T1.13 fix; regression-pinning here so
            # the two remain in sync).
            available = extras["available_personas"]
            assert isinstance(available, list)
            assert available[0]["name"] == "basic"
            assert available[0]["display_name"] == "Basic"
        finally:
            await runtime.current_app.shutdown()  # type: ignore[union-attr]
            await _drain_teardown(runtime)


class TestT116SessionReadiness:
    """T1.16 — explicit session-readiness signal in hello extras.

    Background: pre-T1.16, a server whose `_shim_persona_select`
    raised on lazy boot would still emit a normal-looking hello
    + `state: IDLE` to the client. The PWA showed "Hold to talk"
    but every PTT silently failed because `current_app is None`.
    The fix introduces `_last_session_error` + `session_ready` /
    `session_error` fields in `_get_hello_extras` so the client
    can gate PTT on a real "the server can serve a turn" boolean.

    The defensive `_shim_get_skills_state` test pins the second
    half of the symptom — a perpetually-loading skills panel
    when `build_skills_state` raised mid-flight (the previous
    log-and-return path silently dropped the response).
    """

    async def test_lazy_boot_failure_sets_session_error(self, runtime_in_tmp: Runtime) -> None:
        # No current_app + a name that doesn't exist on disk.
        # `_shim_persona_select` catches the exception and stashes
        # the error so the next hello signals not-ready.
        assert runtime_in_tmp.current_app is None
        await runtime_in_tmp._shim_persona_select("nonexistent", language=None)
        assert runtime_in_tmp.current_app is None
        assert runtime_in_tmp._last_session_error is not None
        assert "nonexistent" in runtime_in_tmp._last_session_error

        extras = runtime_in_tmp._get_hello_extras()
        assert extras["session_ready"] is False
        assert extras["session_error"] is not None

    async def test_successful_swap_clears_session_error(self, runtime_in_tmp: Runtime) -> None:
        # Pre-stamp a stale error from a previous failed connect, then
        # run a successful swap and verify the next hello flips ready.
        runtime_in_tmp._last_session_error = "stale error from earlier connect"
        await runtime_in_tmp._shim_persona_select("alpha", language=None)
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp._last_session_error is None

        extras = runtime_in_tmp._get_hello_extras()
        assert extras["session_ready"] is True
        assert extras["session_error"] is None

        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_swap_failure_with_live_app_stays_ready(self, runtime_in_tmp: Runtime) -> None:
        """A persona swap to a broken target while a healthy
        `current_app` is already serving must keep `session_ready`
        true. The existing app still works; only the user's
        requested swap was rejected."""
        await runtime_in_tmp._shim_persona_select("alpha", language=None)
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp._last_session_error is None

        await runtime_in_tmp._shim_persona_select("nonexistent", language=None)
        # Old app still there.
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.data_dir.parent.name == "alpha"

        # Hello says ready (existing app serves) — the transient
        # swap failure surfaces via the status frame, not via
        # `session_ready=False`.
        extras = runtime_in_tmp._get_hello_extras()
        assert extras["session_ready"] is True
        assert extras["session_error"] is None

        await runtime_in_tmp.current_app.shutdown()
        await _drain_teardown(runtime_in_tmp)

    async def test_get_skills_state_responds_on_build_failure(
        self,
        runtime_in_tmp: Runtime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defensive: if `build_skills_state` raises, the shim must
        still send a response (empty payload + status) so the PWA's
        skills panel doesn't sit on "Loading…" forever."""
        sent_skills: list[dict[str, Any]] = []
        sent_status: list[str] = []

        async def _capture_skills(payload: dict[str, Any]) -> None:
            sent_skills.append(payload)

        async def _capture_status(message: str) -> None:
            sent_status.append(message)

        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_skills_state", _capture_skills)
        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_status", _capture_status)

        def _boom(_: Any) -> dict[str, Any]:
            msg = "synthetic build failure"
            raise RuntimeError(msg)

        monkeypatch.setattr("huxley.runtime.build_skills_state", _boom)

        await runtime_in_tmp._shim_get_skills_state()

        # Empty payload sent, not a silent drop.
        assert len(sent_skills) == 1
        assert sent_skills[0] == {"skills": []}
        # Status frame surfaced the failure to the user.
        assert len(sent_status) == 1
        assert "skills" in sent_status[0].lower()


class TestPhaseBWriteShims:
    """Marketplace v2 Phase B: PWA-driven config edits land via these
    shims. Each writes to disk (persona.yaml or values.json) and
    triggers `_reload_current_persona` so running skills pick up the
    new state. The fake personas have empty `skills: {}` blocks so
    the writes don't conflict with discover_skills (no real skill
    packages are loaded by these tests)."""

    async def test_set_skill_enabled_persists_to_persona_yaml(
        self, runtime_in_tmp: Runtime, tmp_path: Path
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        # The fake skill name doesn't need to be installed in the venv —
        # the shim writes to YAML regardless. discover_skills will fail
        # on the next reload because there's no entry point, so we
        # capture the YAML write before the reload runs.
        await runtime_in_tmp._shim_set_skill_enabled("system", enabled=True)
        # YAML on disk now lists `system:` under skills:
        from huxley.persona_yaml import load_persona_yaml

        yaml_path = tmp_path / "personas" / "alpha" / "persona.yaml"
        data = load_persona_yaml(yaml_path)
        assert "system" in data["skills"]
        # The reload may have failed (system isn't registered in the test
        # venv), but the runtime's current_app should still be valid —
        # _reload_current_persona's failure is logged + non-raising.
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_set_skill_enabled_false_removes_from_yaml(
        self, runtime_in_tmp: Runtime, tmp_path: Path
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        # First add a skill, then remove it.
        from huxley.persona_yaml import load_persona_yaml, save_persona_yaml
        from huxley.persona_yaml import set_skill_enabled as yaml_enable

        yaml_path = tmp_path / "personas" / "alpha" / "persona.yaml"
        data = load_persona_yaml(yaml_path)
        yaml_enable(data, "system", enabled=True)
        save_persona_yaml(yaml_path, data)

        await runtime_in_tmp._shim_set_skill_enabled("system", enabled=False)
        data = load_persona_yaml(yaml_path)
        assert "system" not in data["skills"]
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_set_skill_config_replaces_block(
        self, runtime_in_tmp: Runtime, tmp_path: Path
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        await runtime_in_tmp._shim_set_skill_config("system", {"timezone": "Europe/Madrid"})
        from huxley.persona_yaml import load_persona_yaml

        yaml_path = tmp_path / "personas" / "alpha" / "persona.yaml"
        data = load_persona_yaml(yaml_path)
        assert dict(data["skills"]["system"]) == {"timezone": "Europe/Madrid"}
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_set_skill_secret_writes_to_secrets_dir(
        self, runtime_in_tmp: Runtime, tmp_path: Path
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        await runtime_in_tmp._shim_set_skill_secret("stocks", "api_key", "sk-VERYSECRET")
        secrets_path = (
            tmp_path / "personas" / "alpha" / "data" / "secrets" / "stocks" / "values.json"
        )
        assert secrets_path.exists()
        contents = secrets_path.read_text(encoding="utf-8")
        assert "sk-VERYSECRET" in contents
        # Perms locked down to 0600 (file) — the JsonFileSecrets contract.
        assert (secrets_path.stat().st_mode & 0o777) == 0o600
        # Parent dir 0700.
        assert (secrets_path.parent.stat().st_mode & 0o777) == 0o700
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_delete_skill_secret_removes_key(
        self, runtime_in_tmp: Runtime, tmp_path: Path
    ) -> None:
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        await runtime_in_tmp._shim_set_skill_secret("stocks", "api_key", "sk-A")
        await runtime_in_tmp._shim_set_skill_secret("stocks", "extra", "sk-B")
        await runtime_in_tmp._shim_delete_skill_secret("stocks", "api_key")
        secrets_path = (
            tmp_path / "personas" / "alpha" / "data" / "secrets" / "stocks" / "values.json"
        )
        contents = secrets_path.read_text(encoding="utf-8")
        assert "sk-A" not in contents
        assert "sk-B" in contents  # other keys untouched
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_writes_are_noop_when_no_current_app(self, runtime_in_tmp: Runtime) -> None:
        # Lazy-boot window: server up, no persona selected. All four
        # shims must NOT crash — they log a warning and return.
        assert runtime_in_tmp.current_app is None
        await runtime_in_tmp._shim_set_skill_enabled("system", enabled=True)
        await runtime_in_tmp._shim_set_skill_config("system", {"x": 1})
        await runtime_in_tmp._shim_set_skill_secret("stocks", "k", "v")
        await runtime_in_tmp._shim_delete_skill_secret("stocks", "k")
        # Still no current app afterward.
        assert runtime_in_tmp.current_app is None


class TestPhaseDInstallShim:
    """Marketplace v2 Phase D: install + restart wiring. The
    `_shim_install_skill` orchestrates validation → subprocess → restart;
    `_perform_restart` sets the flag + signals shutdown so `run()` exec's
    last. The actual `os.execv` is unreachable in unit tests (it would
    replace the test runner) — we patch it to record-and-return.

    The single highest-ROI test (planning critic §D) lives here:
    `test_install_full_happy_path_with_concurrent_rejection`.
    """

    async def test_perform_restart_sets_flag_and_signals_shutdown(
        self, runtime_in_tmp: Runtime
    ) -> None:
        assert runtime_in_tmp._restart_after_shutdown is False
        assert not runtime_in_tmp._shutdown_event.is_set()
        await runtime_in_tmp._perform_restart()
        assert runtime_in_tmp._restart_after_shutdown is True
        assert runtime_in_tmp._shutdown_event.is_set()

    async def test_perform_restart_idempotent(self, runtime_in_tmp: Runtime) -> None:
        # Second call within the same boot is a no-op (the first
        # already requested restart; we don't want competing
        # exec attempts).
        await runtime_in_tmp._perform_restart()
        # Manually clear shutdown_event to detect if the second call
        # re-sets it.
        runtime_in_tmp._shutdown_event.clear()
        await runtime_in_tmp._perform_restart()
        assert not runtime_in_tmp._shutdown_event.is_set()

    async def test_install_validation_rejects_bad_name(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Use a bare AudioServer mock that captures send_install_event.
        sent: list[dict[str, Any]] = []

        async def _capture(payload: dict[str, Any]) -> None:
            sent.append(payload)

        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_install_event", _capture)
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)

        await runtime_in_tmp._shim_install_skill("invalid-package-name")

        # Failure event sent, no restart.
        assert len(sent) == 1
        assert sent[0]["ok"] is False
        assert sent[0]["error_code"] == "validation_failed"
        assert runtime_in_tmp._restart_after_shutdown is False
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_install_full_happy_path_with_concurrent_rejection(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase D planning critic §D — the single test that catches
        the most. Asserts:
          1. Install runs the right subprocess command from the right cwd.
          2. On success, install_event {complete, ok=True, restart_required=True}
             is sent.
          3. `_perform_restart` is triggered (restart_after_shutdown=True).
          4. While the install is running, a concurrent install for a
             DIFFERENT skill is rejected with `install_in_progress` code.
          5. The mid-install hello extras include `install_in_progress`
             with the package name.
        """
        sent: list[dict[str, Any]] = []

        async def _capture(payload: dict[str, Any]) -> None:
            sent.append(payload)

        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_install_event", _capture)

        # Patch the installer's subprocess to return success without
        # touching the real venv. The install_skill function reads
        # the current `asyncio.create_subprocess_exec`; we replace it.
        captured_args: list[tuple[Any, ...]] = []

        class _FakeProc:
            def __init__(self) -> None:
                self.returncode = 0
                self._can_finish = asyncio.Event()

            async def communicate(self) -> tuple[bytes, bytes]:
                # Block until allowed. Lets the test fire a concurrent
                # install while we're "in flight".
                await self._can_finish.wait()
                return b"installed\n", b""

            def kill(self) -> None:
                pass

            async def wait(self) -> int:
                return 0

        proc = _FakeProc()

        async def _stub_subprocess(*args: Any, **kwargs: Any) -> _FakeProc:
            captured_args.append((args, kwargs))
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _stub_subprocess)
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)

        # Fire the install but don't await — let it block on `communicate`.
        install_task = asyncio.create_task(
            runtime_in_tmp._shim_install_skill("huxley-skill-foo"),
        )
        # Yield control so the install reaches the `await` on subprocess.
        await asyncio.sleep(0.05)

        # Mid-flight: confirm hello extras carry install_in_progress.
        extras = runtime_in_tmp._get_hello_extras()
        assert extras["install_in_progress"] is not None
        assert extras["install_in_progress"]["package"] == "huxley-skill-foo"

        # Concurrent install for a different skill is refused.
        await runtime_in_tmp._shim_install_skill("huxley-skill-bar")
        # The concurrent attempt sent ONE event (the rejection).
        rejected = [s for s in sent if s.get("package") == "huxley-skill-bar"]
        assert len(rejected) == 1
        assert rejected[0]["ok"] is False
        assert rejected[0]["error_code"] == "install_in_progress"

        # Phase D post-impl critic §11 — assert the second install
        # did NOT spawn a subprocess. Without this, a future bug in
        # the concurrency gate could spawn a parallel `uv add` while
        # passing the rejection event check.
        assert len(captured_args) == 1, "Concurrent install must not spawn a second subprocess"

        # Allow the in-flight install to complete.
        proc._can_finish.set()
        await install_task

        # Subprocess command: `uv add huxley-skill-foo`.
        assert len(captured_args) == 1
        args, _ = captured_args[0]
        assert args[:3] == ("uv", "add", "huxley-skill-foo")

        # Success event with restart_required=true.
        success = [
            s
            for s in sent
            if s.get("package") == "huxley-skill-foo" and s.get("kind") == "complete"
        ]
        assert len(success) == 1
        assert success[0]["ok"] is True
        assert success[0]["restart_required"] is True

        # Restart triggered.
        assert runtime_in_tmp._restart_after_shutdown is True

        # Cleanup.
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_install_refused_when_claim_active(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase D post-impl critic §1: mid-call gate must include
        active InputClaims. The original implementation reached into
        the private `_active_claim_id`; we now use the public
        `is_busy` accessor which composes claim + stream state."""
        sent: list[dict[str, Any]] = []

        async def _capture(payload: dict[str, Any]) -> None:
            sent.append(payload)

        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_install_event", _capture)

        # Simulate an active claim.
        runtime_in_tmp.audio_server._active_claim_id = "claim-fake"
        # No subprocess should ever be spawned in this branch.
        spawn_calls = []

        async def _stub(*args: Any, **kwargs: Any) -> None:
            spawn_calls.append(args)
            return None

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _stub)

        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        await runtime_in_tmp._shim_install_skill("huxley-skill-foo")

        # Refusal sent.
        refused = [s for s in sent if s.get("kind") == "complete"]
        assert len(refused) == 1
        assert refused[0]["ok"] is False
        assert refused[0]["error_code"] == "in_call"
        # No subprocess.
        assert spawn_calls == []
        # No restart.
        assert runtime_in_tmp._restart_after_shutdown is False

        runtime_in_tmp.audio_server._active_claim_id = None
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_install_refused_when_stream_active(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same gate, stream side. Audiobook / radio / news stream
        with no claim still blocks install."""
        sent: list[dict[str, Any]] = []

        async def _capture(payload: dict[str, Any]) -> None:
            sent.append(payload)

        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_install_event", _capture)
        runtime_in_tmp.audio_server._active_stream_id = "stream-fake"

        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        await runtime_in_tmp._shim_install_skill("huxley-skill-foo")

        refused = [s for s in sent if s.get("kind") == "complete"]
        assert len(refused) == 1
        assert refused[0]["error_code"] == "in_call"

        runtime_in_tmp.audio_server._active_stream_id = None
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_restart_server_refused_when_busy(self, runtime_in_tmp: Runtime) -> None:
        """Phase D post-impl critic §2: maintenance Restart button
        is gated server-side too. Defense in depth — UI gate isn't
        enough for ESP32 / future clients."""
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)
        runtime_in_tmp.audio_server._active_claim_id = "fake"
        await runtime_in_tmp._shim_restart_server()
        # Restart NOT triggered.
        assert runtime_in_tmp._restart_after_shutdown is False

        runtime_in_tmp.audio_server._active_claim_id = None
        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()

    async def test_install_failure_does_not_trigger_restart(
        self, runtime_in_tmp: Runtime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[dict[str, Any]] = []

        async def _capture(payload: dict[str, Any]) -> None:
            sent.append(payload)

        monkeypatch.setattr(runtime_in_tmp.audio_server, "send_install_event", _capture)

        class _FailProc:
            def __init__(self) -> None:
                self.returncode = 1

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b"error: no matching distribution found\n"

            def kill(self) -> None:
                pass

            async def wait(self) -> int:
                return 1

        async def _stub(*args: Any, **kwargs: Any) -> _FailProc:
            return _FailProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _stub)
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False)

        await runtime_in_tmp._shim_install_skill("huxley-skill-nope")

        complete = [
            s
            for s in sent
            if s.get("kind") == "complete" and s.get("package") == "huxley-skill-nope"
        ]
        assert len(complete) == 1
        assert complete[0]["ok"] is False
        assert complete[0]["error_code"] == "package_not_found"
        # No restart on failure.
        assert runtime_in_tmp._restart_after_shutdown is False

        await _drain_teardown(runtime_in_tmp)
        if runtime_in_tmp.current_app is not None:
            await runtime_in_tmp.current_app.shutdown()
