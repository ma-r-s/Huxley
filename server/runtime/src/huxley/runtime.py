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
from typing import TYPE_CHECKING

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
from huxley.server.server import AudioServer

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
        """Process-wide lifecycle: setup logging + signals, bring up
        default persona, run audio server until shutdown."""
        log_file = self._config.log_file or Path("logs/huxley.log")
        setup_logging(
            level=self._config.log_level,
            json_output=self._config.log_json,
            log_file=log_file,
        )
        await logger.ainfo("huxley_starting")

        # Resolve which persona to bring up first. Locked rule: env >
        # single-persona autodiscovery > alphabetic-first-with-loud-log.
        default_name = pick_default_persona_name(env_name=self._config.persona)
        if default_name is None:
            msg = (
                "no personas could be discovered. Place a persona dir "
                "under ./personas/<name>/ or set HUXLEY_PERSONA."
            )
            raise PersonaError(msg)

        # Eager-connect the default at boot so first PTT is instant.
        await self._switch_to_persona(default_name, auto_connect=True)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_shutdown)

        await logger.ainfo(
            "huxley_ready",
            persona=default_name,
            server=f"ws://{self._config.server_host}:{self._config.server_port}",
        )
        print(
            f"\033[1;32m[Huxley] Server listening on "
            f"ws://{self._config.server_host}:{self._config.server_port}\033[0m",
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
        """
        async with self._swap_lock:
            old_app = self.current_app
            if old_app is not None and old_app.persona.name == name:
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

            if old_app is not None:
                self._teardown_task = asyncio.create_task(self._teardown_app(old_app))

    async def _teardown_app(self, app: Application) -> None:
        try:
            await app.shutdown()
            await logger.ainfo("runtime.old_app_shutdown_complete", persona=app.persona.name)
        except Exception:
            await logger.aexception("runtime.old_app_shutdown_failed", persona=app.persona.name)

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

        Eager-connects (`auto_connect=True`) so the user's first PTT
        after the swap reaches a CONVERSING session instead of being
        rejected by the IDLE-state guard. Critic round 2 §4."""
        if name is None:
            # No persona requested — same-persona path. The subsequent
            # `on_language_select` callback handles a language flip.
            return
        if self.current_app is not None and self.current_app.persona.name == name:
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
        restart."""
        return {
            "current_persona": (self.current_app.persona.name if self.current_app else None),
            "available_personas": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "language": s.language,
                }
                for s in list_personas()
            ],
        }
