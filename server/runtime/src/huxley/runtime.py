"""Process-level runtime: owns the lifelong AudioServer + a swappable
current Application.

A Huxley process runs ONE `Runtime`. The Runtime hosts ONE persona at a
time (`current_app`) — when a client reconnects with a different
`?persona=<name>` query string, the Runtime constructs a new
Application for that persona and atomically replaces `current_app`.
The TCP listener stays bound across swaps; only the per-persona stack
(storage, skills, provider, coordinator) churns.

See `docs/triage.md` T1.13 for the locked design + critic dispositions.

Responsibility split:

- `Runtime` owns `AudioServer` and `current_app`. It implements the
  swap algorithm (pre-validate new → atomic ref swap → background
  teardown of old). It also resolves the default persona at boot per
  the locked rule (env > single-persona autodiscovery > alphabetic).
- `Application` owns one persona's stack — reconstructed on each
  persona swap. It accepts a reference to the lifelong `AudioServer`
  so its `send_*` methods reach the active client; it does NOT install
  callbacks on `AudioServer` itself.

The `AudioServer`'s callbacks are bound to runtime-level `_shim_*`
methods that forward to `current_app._on_*`. This way the dispatch
target rebinds atomically when `current_app` changes — no need to tell
the AudioServer "your callbacks moved."
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from huxley.app import Application
from huxley.logging import setup_logging
from huxley.persona import (
    PersonaError,
    _find_named_persona,
    list_personas,
    load_persona,
    pick_default_persona_name,
)
from huxley.persona_yaml import (
    load_persona_yaml,
    save_persona_yaml,
    set_skill_config,
    set_skill_enabled,
)
from huxley.server.server import AudioServer
from huxley.skills_state import build_skills_state
from huxley.storage.secrets import JsonFileSecrets

if TYPE_CHECKING:
    from huxley.config import Settings

logger = structlog.get_logger()


class Runtime:
    """Process-level orchestrator. See module docstring."""

    def __init__(self, config: Settings) -> None:
        self._config = config
        # Single TCP listener for the process; lifelong across persona
        # swaps. Callbacks are runtime-level shims (see `_shim_*`).
        self.audio_server = AudioServer(
            host=config.server_host,
            port=config.server_port,
            on_wake_word=self._shim_wake_word,
            on_ptt_start=self._shim_ptt_start,
            on_ptt_stop=self._shim_ptt_stop,
            on_audio_frame=self._shim_audio_frame,
            on_reset=self._shim_reset,
            on_language_select=self._shim_language_select,
            on_list_sessions=self._shim_list_sessions,
            on_get_session=self._shim_get_session,
            on_delete_session=self._shim_delete_session,
            on_persona_select=self._shim_persona_select,
            on_get_skills_state=self._shim_get_skills_state,
            on_set_skill_enabled=self._shim_set_skill_enabled,
            on_set_skill_config=self._shim_set_skill_config,
            on_set_skill_secret=self._shim_set_skill_secret,
            on_delete_skill_secret=self._shim_delete_skill_secret,
            get_hello_extras=self._get_hello_extras,
        )
        self.current_app: Application | None = None
        # Background task that tears down the PREVIOUS Application
        # after a swap. Tracked so a rapid back-and-forth swap (A→B→A
        # within ~500ms) can await the in-flight teardown before
        # re-opening the same SQLite DB — fixes the WAL writer-lock
        # collision the critic flagged.
        self._teardown_task: asyncio.Task[None] | None = None
        # Serializes `_switch_to_persona` so two concurrent swap requests
        # (PWA in two tabs, StrictMode double-mount, rapid picker clicks)
        # can't race their reference-overwrite of `current_app`. Without
        # this, the LOSER's freshly-built Application is silently leaked
        # — never shutdown, holds open SQLite + FocusManager + a live
        # OpenAI session if `auto_connect=True`. Critic round 2 finding
        # §2; see docs/triage.md T1.13.
        self._swap_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._shutting_down = False

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """Process-wide lifecycle: setup logging + signals, run audio
        server until shutdown.

        **Lazy boot**: the runtime starts without a `current_app`. The
        first WebSocket connection drives persona selection via
        ``?persona=<name>``; the shim falls back to
        ``pick_default_persona_name`` if no query param is supplied.
        Cost: the very first PTT after a server start pays the
        Realtime handshake latency (~1-2s) since there's no
        preconnected session. Benefit: no ``HUXLEY_PERSONA=`` ceremony
        for development; the PWA picker is the single source of truth
        for which persona is active.

        If ``HUXLEY_PERSONA`` IS set in the environment, it still wins
        — the env var becomes the default the lazy fallback picks.
        """
        log_file = self._config.log_file or Path("logs/huxley.log")
        setup_logging(
            level=self._config.log_level,
            json_output=self._config.log_json,
            log_file=log_file,
        )
        await logger.ainfo("huxley_starting")

        # Validate at least one persona exists so we fail loudly NOW
        # (rather than on the first WS connect, which surfaces in the
        # client as a confusing handshake rejection). pick_default_persona_name
        # returns the first usable persona; we discard the value and
        # rely on the shim to pick at first-connect time. Env var still
        # influences the fallback inside the shim.
        if pick_default_persona_name(env_name=self._config.persona) is None:
            msg = "no personas could be discovered. Place a persona dir under ./personas/<name>/."
            raise PersonaError(msg)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_shutdown)

        await logger.ainfo(
            "huxley_ready",
            persona=None,
            note="lazy boot — first WS connect picks the persona",
            server=f"ws://{self._config.server_host}:{self._config.server_port}",
        )
        print(
            f"\033[1;32m[Huxley] Server listening on "
            f"ws://{self._config.server_host}:{self._config.server_port}"
            f" — awaiting first client to pick persona\033[0m",
            flush=True,
        )

        server_task = asyncio.create_task(self.audio_server.run())
        await self._shutdown_event.wait()

        await logger.ainfo("huxley_shutting_down")
        self._shutting_down = True
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task

        # Drain any pending teardown task before tearing down current.
        if self._teardown_task is not None and not self._teardown_task.done():
            with contextlib.suppress(Exception):
                await self._teardown_task

        if self.current_app is not None:
            with contextlib.suppress(Exception):
                await self.current_app.shutdown()

        await logger.ainfo("huxley_stopped")

    def _signal_shutdown(self) -> None:
        self._shutdown_event.set()

    # ── Persona swap algorithm ──────────────────────────────────────────

    # Cap on how long a swap will wait for the previous Application's
    # teardown (provider summary write, skill teardowns) to finish before
    # giving up. A buggy skill teardown that deadlocks an `await` would
    # otherwise stall every subsequent swap forever — DoSing the picker.
    # Critic round 2 finding §10. Ten seconds is generous for the LLM-
    # backed summary call (typical 1-3s) plus skill teardowns.
    _TEARDOWN_TIMEOUT_S = 10.0

    async def _switch_to_persona(
        self,
        name: str,
        *,
        auto_connect: bool = False,
        language: str | None = None,
        force: bool = False,
    ) -> None:
        """Build a new Application for `name`, atomically replace
        `current_app`, schedule old's teardown in the background.

        Pre-validates: builds new + starts before tearing down old. If
        `start()` fails, the OLD Application (if any) stays as
        `current_app` and the exception propagates so the caller can
        decide. If there was no `current_app` (first call at boot), the
        exception propagates and `current_app` stays None — the runtime
        can't serve until a working persona loads.

        Serialized via `_swap_lock` so concurrent calls (e.g. two-tab
        PWA, StrictMode double-mount) don't leak a freshly-built
        Application via reference-overwrite. Critic round 2 §2.

        `force=True` bypasses the same-persona short-circuit. Used by
        `_reload_current_persona()` (Phase B) to apply persona.yaml /
        secrets edits via a fresh load + setup_all without a process
        restart.
        """
        async with self._swap_lock:
            old_app = self.current_app
            if not force and old_app is not None and old_app.persona.name == name:
                return  # no-op — already on this persona

            # Storage-lock-race fix (critic round 1): if a teardown task
            # is in flight, wait it before constructing a new Application
            # that would open the same SQLite DB. Critical for rapid
            # back-and-forth swap (A→B→A within ~500ms) where the OLD
            # shutdown's provider summary write may still be writing to
            # abuelos.db when the new abuelos opens it. Capped by
            # `_TEARDOWN_TIMEOUT_S` (round 2 §10) so a stuck teardown
            # doesn't block subsequent swaps forever — log + abandon.
            if self._teardown_task is not None and not self._teardown_task.done():
                try:
                    # `shield` so the wait_for timeout doesn't cancel the
                    # underlying teardown — we still want it to complete
                    # in the background; we just stop waiting on it.
                    await asyncio.wait_for(
                        asyncio.shield(self._teardown_task),
                        timeout=self._TEARDOWN_TIMEOUT_S,
                    )
                except TimeoutError:
                    await logger.awarning(
                        "runtime.teardown_timeout_abandoned",
                        timeout_s=self._TEARDOWN_TIMEOUT_S,
                        note="previous teardown still running; proceeding anyway",
                    )
                except Exception:
                    # Teardown raised — already logged inside
                    # `_teardown_app`. Don't block the new swap on a
                    # botched cleanup.
                    pass
            self._teardown_task = None

            await logger.ainfo(
                "runtime.persona_swap_started",
                from_=old_app.persona.name if old_app else None,
                to=name,
            )

            persona_path = _find_named_persona(name)
            try:
                new_persona = load_persona(persona_path)
            except PersonaError:
                await logger.aexception("runtime.persona_load_failed", persona=name)
                raise

            new_app = Application(
                self._config,
                new_persona,
                audio_server=self.audio_server,
                language=language,
            )
            try:
                await new_app.start(auto_connect=auto_connect)
            except Exception:
                # Cleanup partial init; OLD app (if any) untouched.
                with contextlib.suppress(Exception):
                    await new_app.shutdown()
                await logger.aexception("runtime.persona_start_failed", persona=name)
                raise

            self.current_app = new_app
            await logger.ainfo(
                "runtime.persona_swap_committed",
                from_=old_app.persona.name if old_app else None,
                to=name,
            )

            # Push fresh skills_state to the connected client right after
            # the swap commits. The PWA's SkillsSheet caches the previous
            # persona's enabled-flags + current_config + secret_keys_set,
            # which would otherwise read as the new persona's state until
            # the user re-opened DeviceSheet and triggered a refetch.
            # Phase B's "refresh after write" flow uses the same push.
            try:
                payload = build_skills_state(self.current_app)
                await self.audio_server.send_skills_state(payload)
            except Exception:
                await logger.aexception("skills_state.swap_push_failed")

            if old_app is not None:
                self._teardown_task = asyncio.create_task(self._teardown_app(old_app))

    async def _teardown_app(self, app: Application) -> None:
        try:
            await app.shutdown()
            await logger.ainfo("runtime.old_app_shutdown_complete", persona=app.persona.name)
        except Exception:
            await logger.aexception("runtime.old_app_shutdown_failed", persona=app.persona.name)

    async def _reload_current_persona(self) -> None:
        """Reload the active persona without changing identity.

        Marketplace v2 Phase B writes (toggle skill, edit config,
        edit secret) call this after persisting to disk so the
        running skills pick up the new state. Mechanism: the existing
        `_switch_to_persona` builds a new Application from a fresh
        persona.yaml load + runs setup_all on every skill, which is
        exactly what's needed — the only thing missing is the
        same-persona short-circuit, which `force=True` bypasses.

        Audio-wise this is heavier than just notifying skills:
        the OpenAI session disconnects + reconnects (~1-2s gap). For
        Phase B's config-time use case that's acceptable; mid-call
        config edits aren't a supported flow. Phase D (auto-install)
        uses a different path because the venv changes mid-run.

        No-op if no current persona is loaded (lazy-boot window).
        Failures are logged but not raised — the WS write callsite
        still acknowledges to the PWA so the user sees the disk
        write succeeded; the reload-failure shows up in the server
        log for diagnosis.
        """
        if self.current_app is None:
            await logger.ainfo("runtime.reload.no_current_app")
            return
        name = self.current_app.persona.data_dir.parent.name
        try:
            await self._switch_to_persona(
                name,
                auto_connect=True,
                language=self.current_app._active_language,
                force=True,
            )
        except Exception:
            await logger.aexception("runtime.reload_failed", persona=name)

    # ── AudioServer callback shims ──────────────────────────────────────
    #
    # AudioServer fires these for every wire event. Each shim forwards
    # to the active Application's handler. While `current_app` is None
    # (between bootstrap failure and shutdown, or before the first
    # successful persona load), we no-op — the WS still establishes but
    # no events route, and the next `?persona=` reconnect can recover.

    async def _shim_wake_word(self) -> None:
        if self.current_app is not None:
            await self.current_app.on_wake_word()

    async def _shim_ptt_start(self) -> None:
        if self.current_app is not None:
            await self.current_app.on_ptt_start()

    async def _shim_ptt_stop(self) -> None:
        if self.current_app is not None:
            await self.current_app.on_ptt_stop()

    async def _shim_audio_frame(self, pcm: bytes) -> None:
        if self.current_app is not None:
            await self.current_app.on_audio_frame(pcm)

    async def _shim_reset(self) -> None:
        if self.current_app is not None:
            await self.current_app.on_reset()

    async def _shim_language_select(self, language: str | None) -> None:
        if self.current_app is not None:
            await self.current_app.on_language_select(language)

    async def _shim_list_sessions(self) -> None:
        if self.current_app is not None:
            await self.current_app.on_list_sessions()

    async def _shim_get_session(self, session_id: int) -> None:
        if self.current_app is not None:
            await self.current_app.on_get_session(session_id)

    async def _shim_delete_session(self, session_id: int) -> None:
        if self.current_app is not None:
            await self.current_app.on_delete_session(session_id)

    async def _shim_get_skills_state(self) -> None:
        """Marketplace v2 Phase A — PWA opens DeviceSheet's Skills section.

        Built at the runtime level (not on `current_app`) so the panel
        works during the lazy-boot window before any persona is
        selected: clients see the list of installed skills with
        `enabled=False` everywhere, ready to be turned on once a
        persona loads. Failures are logged + the response is skipped;
        we don't crash the WS connection over a malformed entry-point.
        """
        try:
            payload = build_skills_state(self.current_app)
        except Exception:
            await logger.aexception("skills_state.build_failed")
            return
        await self.audio_server.send_skills_state(payload)

    # ── Phase B write shims ──────────────────────────────────────────
    #
    # Each takes a validated payload from AudioServer (types coerced /
    # rejected upstream), persists to disk via the appropriate
    # primitive (`persona_yaml.set_skill_*` for YAML edits,
    # `JsonFileSecrets` for secrets), then triggers a hot reload of
    # the active persona so running skills pick up the new state.
    #
    # No-op when `current_app is None` (lazy-boot window) — there's
    # no persona.yaml to mutate. Failures during write or reload are
    # logged but not raised; the WS write callsite already acked the
    # frame, and Phase B's UX surfaces the failure via the next
    # `skills_state` push (which reflects the actual disk state).

    async def _shim_set_skill_enabled(self, skill_name: str, enabled: bool) -> None:
        if self.current_app is None:
            await logger.awarning(
                "runtime.set_skill_enabled.no_current_app",
                skill=skill_name,
            )
            return
        persona_path = self.current_app.persona.data_dir.parent / "persona.yaml"
        try:
            data = await asyncio.to_thread(load_persona_yaml, persona_path)
            set_skill_enabled(data, skill_name, enabled)
            await asyncio.to_thread(save_persona_yaml, persona_path, data)
        except Exception:
            await logger.aexception(
                "runtime.set_skill_enabled.write_failed",
                skill=skill_name,
                enabled=enabled,
            )
            return
        await logger.ainfo(
            "runtime.set_skill_enabled.persisted",
            skill=skill_name,
            enabled=enabled,
        )
        await self._reload_current_persona()

    async def _shim_set_skill_config(self, skill_name: str, config: dict[str, Any]) -> None:
        if self.current_app is None:
            await logger.awarning(
                "runtime.set_skill_config.no_current_app",
                skill=skill_name,
            )
            return
        persona_path = self.current_app.persona.data_dir.parent / "persona.yaml"
        try:
            data = await asyncio.to_thread(load_persona_yaml, persona_path)
            set_skill_config(data, skill_name, config)
            await asyncio.to_thread(save_persona_yaml, persona_path, data)
        except Exception:
            await logger.aexception(
                "runtime.set_skill_config.write_failed",
                skill=skill_name,
            )
            return
        await logger.ainfo(
            "runtime.set_skill_config.persisted",
            skill=skill_name,
            keys=sorted(config.keys()),
        )
        await self._reload_current_persona()

    async def _shim_set_skill_secret(self, skill_name: str, key: str, value: str) -> None:
        if self.current_app is None:
            await logger.awarning(
                "runtime.set_skill_secret.no_current_app",
                skill=skill_name,
                key=key,
            )
            return
        # The skill's secrets dir lives at <persona>/data/secrets/<name>/.
        # JsonFileSecrets handles the atomic write + 0700/0600 perms.
        secrets_dir = self.current_app.persona.data_dir / "secrets" / skill_name
        secrets = JsonFileSecrets(secrets_dir)
        try:
            await secrets.set(key, value)
        except Exception:
            await logger.aexception(
                "runtime.set_skill_secret.write_failed",
                skill=skill_name,
                key=key,
            )
            return
        await logger.ainfo(
            "runtime.set_skill_secret.persisted",
            skill=skill_name,
            key=key,
        )
        await self._reload_current_persona()

    async def _shim_delete_skill_secret(self, skill_name: str, key: str) -> None:
        if self.current_app is None:
            await logger.awarning(
                "runtime.delete_skill_secret.no_current_app",
                skill=skill_name,
                key=key,
            )
            return
        secrets_dir = self.current_app.persona.data_dir / "secrets" / skill_name
        secrets = JsonFileSecrets(secrets_dir)
        try:
            await secrets.delete(key)
        except Exception:
            await logger.aexception(
                "runtime.delete_skill_secret.write_failed",
                skill=skill_name,
                key=key,
            )
            return
        await logger.ainfo(
            "runtime.delete_skill_secret.persisted",
            skill=skill_name,
            key=key,
        )
        await self._reload_current_persona()

    async def _shim_persona_select(self, name: str | None, language: str | None) -> None:
        """Fired by AudioServer on each new connection with the
        `?persona=<name>` and `?lang=<code>` query params parsed out.
        Triggers a swap if the name differs from the current persona;
        the language is threaded into the new Application so its
        OpenAI session opens in the requested language from the start
        (avoids a "open in default → on_language_select fires →
        disconnect+reconnect to switch language" cascade that leaked
        a spurious CONVERSING→IDLE state to the new client mid-swap,
        firing the error tone and breaking PTT for the user). Critic
        round 3 finding from Mario's smoke.

        Failures are caught and logged — we do NOT propagate them to
        AudioServer because that would crash the WS handshake.

        Lazy-boot: when `current_app is None` (the post-startup state
        before any client has connected), this is the **first**
        connection driving persona selection. If the client supplied
        `?persona=<name>` we use it; otherwise we fall back to
        `pick_default_persona_name` (HUXLEY_PERSONA env var if set,
        else single-persona autodiscovery, else alphabetic-first). The
        first PTT pays the Realtime handshake latency in this state;
        every subsequent connect is fast because `current_app` stays
        live until process shutdown.

        Eager-connects (`auto_connect=True`) so the user's first PTT
        after the swap reaches a CONVERSING session instead of being
        rejected by the IDLE-state guard. Critic round 2 §4."""
        # Lazy-boot path: no current_app yet. Pick a name (env var
        # default if no query param was supplied) and bring it up.
        if self.current_app is None:
            chosen = name or pick_default_persona_name(env_name=self._config.persona)
            if chosen is None:
                # No personas at all — Runtime.run() validated this
                # already, so reaching here means the personas/ dir
                # got deleted between boot and first connect. Log and
                # let the WS handshake proceed with no app; the client
                # will see no `current_persona` and surface "no
                # personas available."
                await logger.awarning(
                    "runtime.lazy_boot.no_persona_pickable",
                    requested=name,
                    env=self._config.persona,
                )
                return
            try:
                await self._switch_to_persona(chosen, auto_connect=True, language=language)
            except Exception:
                await logger.aexception(
                    "runtime.lazy_boot.first_swap_failed",
                    persona=chosen,
                )
            return

        if name is None:
            # No persona requested on a subsequent connect — same-persona
            # path. The subsequent `on_language_select` callback handles
            # a language flip.
            return
        # Compare against directory basename (the canonical id ?persona=
        # resolves), NOT persona.name (the YAML display label). Same
        # id-vs-display foot-gun the post-T1.13 fix locked down for
        # PersonaSummary.name and the hello extras current_persona.
        if self.current_app.persona.data_dir.parent.name == name:
            # Same persona; let `on_language_select` (fired right after
            # by AudioServer) handle a language change if any.
            return
        try:
            await self._switch_to_persona(name, auto_connect=True, language=language)
        except Exception:
            await logger.aexception("runtime.persona_swap_via_query_failed", persona=name)

    # ── Hello payload extras (T1.13 additive fields) ───────────────────

    def _get_hello_extras(self) -> dict[str, object]:
        """Inject `current_persona` + `available_personas` into the
        hello payload at handshake time. Strict additive — old clients
        ignore unknown keys, so no protocol bump is required.

        `available_personas` is enumerated fresh on each connection;
        cheap (single directory listing) and means a persona added to
        `./personas/` while the server is running shows up without a
        restart.

        `current_persona` is the **directory basename** (the canonical
        id `?persona=` resolves against), NOT `PersonaSpec.name` which
        is the YAML's display label (`"Basic"`, `"Chief"`, ...). The
        PWA picker compares this against `available_personas[].name`
        which is also the directory basename, so both must agree.
        Conflating them was the regression that made post-swap UI show
        the wrong active row even when the swap actually landed (the
        same id-vs-display-name foot-gun the post-T1.13 fix locked
        down for `available_personas` but missed here).
        """
        return {
            "current_persona": (
                self.current_app.persona.data_dir.parent.name if self.current_app else None
            ),
            "available_personas": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "language": s.language,
                }
                for s in list_personas()
            ],
        }
