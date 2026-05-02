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
        # Without force, same-name is a no-op (Application identity
        # preserved) — see test_same_persona_is_noop above.
        # WITH force, the existing app is torn down + a freshly-built
        # one takes its place.
        await runtime_in_tmp._switch_to_persona("alpha", auto_connect=False, force=True)
        assert runtime_in_tmp.current_app is not first
        assert runtime_in_tmp.current_app is not None
        assert runtime_in_tmp.current_app.persona.name == "alpha"
        # The previous app was scheduled for teardown, not leaked.
        assert runtime_in_tmp._teardown_task is not None
        await _drain_teardown(runtime_in_tmp)
        await runtime_in_tmp.current_app.shutdown()

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
