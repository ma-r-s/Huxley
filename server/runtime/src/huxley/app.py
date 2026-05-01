"""Application orchestrator — wires all subsystems together.

This is the top-level coordinator. It owns the state machine, all subsystems,
the `TurnCoordinator`, and every callback that connects them. No other module
imports from app.py — communication is through callbacks injected at
construction time.

Skills are loaded via Python entry points (`huxley.loader.discover_skills`),
so the framework never imports a concrete skill class. Each skill receives a
`SkillContext` carrying a per-skill logger, namespaced storage, the persona
data dir, and the per-skill config dict.

Audio I/O is owned by the client (browser, ESP32). The server receives mic
frames over WebSocket, routes them to OpenAI via the session manager, and
streams response audio back through the same WebSocket via the `AudioServer`.
Turn sequencing (model speech vs tool factories, interrupts) lives on the
`TurnCoordinator` — see `docs/turns.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from huxley.background import TaskSupervisor
from huxley.cost import CostTracker
from huxley.focus.manager import FocusManager
from huxley.loader import discover_skills
from huxley.reconnect import no_signal_tone_pcm, run_reconnect_loop
from huxley.state.machine import StateMachine
from huxley.storage.backup import ensure_daily_snapshot
from huxley.storage.db import Storage
from huxley.storage.secrets import JsonFileSecrets
from huxley.storage.skill import NamespacedSkillStorage
from huxley.turn import TurnCoordinator
from huxley.voice.openai_realtime import OpenAIRealtimeProvider
from huxley.voice.provider import VoiceProviderCallbacks
from huxley_sdk import AppState, InvalidTransitionError, SkillContext, SkillRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from huxley.config import Settings
    from huxley.persona import PersonaSpec, ResolvedPersona
    from huxley.server.server import AudioServer

logger = structlog.get_logger()


# Generic status strings surfaced to the client while the session is
# still connecting to OpenAI (before the persona can speak). Indexed by
# language code with English as the final fallback. Kept tiny on
# purpose — long-form user-facing copy is the persona's job.
_STATUS_CONNECTING: dict[str, str] = {
    "es": "Conectando\u2026",
    "en": "Connecting\u2026",
    "fr": "Connexion\u2026",
}
_STATUS_CONNECTED: dict[str, str] = {
    "es": "Conectado \u2014 mantén el botón para hablar",
    "en": "Connected \u2014 hold the button to speak",
    "fr": "Connecté \u2014 maintenez le bouton pour parler",
}
_STATUS_FAILED: dict[str, str] = {
    "es": "Error al conectar \u2014 intenta de nuevo",
    "en": "Connection failed \u2014 please try again",
    "fr": "Échec de connexion \u2014 réessayez",
}
_STATUS_WAIT: dict[str, str] = {
    "es": "Conectando \u2014 espera un segundo",
    "en": "Connecting \u2014 one moment",
    "fr": "Connexion \u2014 un instant",
}


class Application:
    """Top-level application orchestrator.

    Creates and owns all subsystems. Wires callbacks for inter-component
    communication. Manages the async main loop and graceful shutdown.
    """

    def __init__(
        self,
        config: Settings,
        persona: PersonaSpec,
        audio_server: AudioServer,
        *,
        language: str | None = None,
    ) -> None:
        self.config = config
        self.persona = persona
        # Active-session language (ISO 639-1). The runtime passes the
        # client's `?lang=<code>` in here when a persona swap is driven
        # by a reconnect that carries a language too — so the new
        # OpenAI session opens in the right language from the start
        # instead of in the persona's default and then immediately
        # disconnect+reconnect in the requested language. `None` means
        # "use persona default." `_resolved_persona` caches the
        # language-collapsed view; resolved upfront with `language` so
        # the first skill setup runs in the right locale without a
        # subsequent reconfigure churn.
        self._active_language = language
        self._resolved_persona: ResolvedPersona = persona.resolve(language)
        self.state_machine = StateMachine()
        self.storage = Storage(persona.data_dir / f"{persona.name.lower()}.db")
        self.skill_registry = SkillRegistry()

        # T1.13 — `AudioServer` is now process-lifelong (owned by `Runtime`)
        # so it can survive persona swaps. Application receives a reference
        # and uses its `send_X` methods to push messages to the active
        # client, but does NOT install its own callbacks — those are
        # routed through Runtime's shims to `current_app._on_X` so a
        # swap rebinds the dispatch target atomically.
        self.server = audio_server

        # Skill discovery via entry points. The persona names which skills it
        # wants — in declaration order — and the loader resolves each to a
        # class via the `huxley.skills` entry-point group.
        enabled = list(persona.skills.keys())
        for _name, skill_cls in discover_skills(enabled).items():
            self.skill_registry.register(skill_cls())

        # Provider callbacks — lambdas so `self.coordinator` resolves at call
        # time (constructed below). The provider fires these as it receives
        # events from the LLM; the coordinator processes them.
        provider_callbacks = VoiceProviderCallbacks(
            on_audio_delta=lambda pcm: self.coordinator.on_audio_delta(pcm),
            on_tool_call=lambda cid, name, args: self.coordinator.on_tool_call(cid, name, args),
            on_response_done=lambda: self.coordinator.on_response_done(),
            on_audio_done=lambda: self.coordinator.on_audio_done(),
            on_commit_failed=lambda: self.coordinator.on_commit_failed(),
            on_session_end=self._on_session_end,
            on_transcript=self._on_transcript,
        )

        # Cost tracker observes per-response usage and warns at daily-total
        # thresholds. Kill switch fires `provider.disconnect(save_summary=True)`
        # when the hard ceiling is crossed — protection against runaway
        # tool-loop bugs that could 100x a normal day's bill. See cost.py.
        self.cost_tracker = CostTracker(
            storage=self.storage,
            model=config.openai_model,
            on_kill_switch=self._cost_kill_switch_disconnect,
        )

        self.provider = OpenAIRealtimeProvider(
            config=config,
            persona=persona,
            skill_registry=self.skill_registry,
            storage=self.storage,
            callbacks=provider_callbacks,
            cost_tracker=self.cost_tracker,
        )

        # FocusManager — serialized arbitrator over the single speaker.
        # Constructed here so it outlives the coordinator (survives session
        # reconnects); started in `start()` where a running loop is guaranteed,
        # stopped in `shutdown()`. The coordinator holds the reference but
        # doesn't use it until Stage 1c.2 (CONTENT-channel routing).
        self.focus_manager = FocusManager.with_default_channels()

        # TaskSupervisor — pool of named long-running async tasks for skills.
        # Owns lifecycle: skills call `ctx.background_task(...)`, supervisor
        # restarts crashes within budget, cancels everything at shutdown.
        # Stored on `self` so `shutdown` can stop it; passed into each
        # SkillContext via the `background_task` field.
        self.task_supervisor = TaskSupervisor(
            send_dev_event=self.server.send_dev_event,
        )

        self.coordinator = TurnCoordinator(
            send_audio=self.server.send_audio,
            send_audio_clear=self.server.send_audio_clear,
            send_status=self.server.send_status,
            send_model_speaking=self.server.send_model_speaking,
            send_dev_event=self.server.send_dev_event,
            send_set_volume=self.server.send_set_volume,
            send_input_mode=self.server.send_input_mode,
            send_claim_started=self.server.send_claim_started,
            send_claim_ended=self.server.send_claim_ended,
            send_stream_started=self.server.send_stream_started,
            send_stream_ended=self.server.send_stream_ended,
            provider=self.provider,
            dispatch_tool=self.skill_registry.dispatch,
            status_messages=self._resolved_persona.ui_strings or None,
            focus_manager=self.focus_manager,
        )

        # `_shutting_down` gates auto-reconnect inside `_on_session_end`
        # so a teardown-triggered provider disconnect doesn't kick off a
        # reconnect race. Set by `shutdown()`. The shutdown signal /
        # signal-handler setup itself lives at the `Runtime` level
        # (process-wide), not on Application.
        self._shutting_down = False
        self._reconnect_task: asyncio.Task[None] | None = None
        # Active session row in `sessions` table, lazily created on the
        # first transcript. None means "no row yet"; `_on_transcript`
        # creates one (via `Storage.start_or_resume_session` which
        # collapses fragmenting reconnects into one logical
        # conversation per the 30-minute idle window). Cleared when
        # `_on_session_end` finalizes the row. T1.12.
        self._active_session_id: int | None = None

    async def _check_skill_schema_versions(self) -> None:
        """Log warnings on declared-vs-stored mismatch. **Read-only.**

        Per ``docs/skill-marketplace.md`` § Schema versioning, the new
        declared version is persisted ONLY after ``setup_all`` succeeds
        (see :meth:`_persist_skill_schema_versions`). This split means a
        skill that throws in ``setup()`` doesn't leave ``schema_meta``
        at the new version while skill state is half-initialized — the
        next boot will see the SAME mismatch and re-warn, which is the
        right behavior for a torn upgrade.

        On first boot for a (skill, persona) pair: stored is None, log
        nothing — the equal-version case after the first persist is also
        silent, so the first-boot path stays symmetric.

        On equal version: silent no-op. T1.13's hot persona swap calls
        ``setup()`` on every reconnect; emitting a heartbeat event here
        would create log noise on every swap.

        Defensive: ``int(getattr(...))`` may raise if a skill declares
        ``data_schema_version`` as a non-numeric value. Catching falls
        back to 1 (the default) and logs a single error so authors see
        their bug.
        """
        for skill in self.skill_registry.skills:
            try:
                declared = int(getattr(skill, "data_schema_version", 1))
            except (TypeError, ValueError):
                await logger.aerror(
                    "skill.schema.invalid_declared_version",
                    skill=skill.name,
                    raw=repr(getattr(skill, "data_schema_version", None)),
                )
                continue
            stored = await self.storage.get_skill_schema_version(skill.name)
            if stored is None or stored == declared:
                # First boot or equal — silent. Persist happens in the
                # post-setup phase regardless.
                continue
            if declared > stored:
                await logger.awarning(
                    "skill.schema.upgrade_needed",
                    skill=skill.name,
                    declared=declared,
                    stored=stored,
                )
            else:
                await logger.awarning(
                    "skill.schema.downgrade_detected",
                    skill=skill.name,
                    declared=declared,
                    stored=stored,
                )

    async def _persist_skill_schema_versions(self) -> None:
        """Write each skill's declared ``data_schema_version`` to
        ``schema_meta``. Called AFTER ``setup_all`` succeeds.

        Splitting persist from check (see :meth:`_check_skill_schema_versions`)
        lets a torn setup leave the OLD stored version intact, so the
        next boot re-evaluates against the same starting state. Without
        this split, a skill that fails ``setup()`` after the version was
        written would lose the warning the next time it booted.
        """
        for skill in self.skill_registry.skills:
            try:
                declared = int(getattr(skill, "data_schema_version", 1))
            except (TypeError, ValueError):
                # Already logged in the check phase; skip the persist.
                continue
            stored = await self.storage.get_skill_schema_version(skill.name)
            if stored == declared:
                continue
            await self.storage.set_skill_schema_version(skill.name, declared)

    def _build_skill_context(self, skill_name: str) -> SkillContext:
        """Construct the SkillContext handed to a skill at setup() / reconfigure().

        Per-skill config comes from the current `ResolvedPersona` — the
        framework has already merged any `skills.<name>.i18n.<lang>`
        overrides for the active language and dropped the nested `i18n`
        block. `language` is the active ISO 639-1 code; skills that need
        per-language tool descriptions read it from here (or from
        `config["_language"]`, set to the same value). Storage is a
        per-skill namespaced view; `persona_data_dir` is the persona's
        resolved data directory, so any relative paths in cfg resolve
        there.
        """
        resolved = self._resolved_persona

        # Skill-named subscribe wrapper. The skill_name capture lets
        # `unregister_client_event_subscribers` (called at shutdown
        # before teardown_all) remove all of this skill's subs cheaply.
        # Type-erased to satisfy the Protocol's positional-only shape.
        def _subscribe_client_event(
            key: str, handler: Callable[[dict[str, Any]], Awaitable[None]], /
        ) -> None:
            self.server.register_client_event_subscriber(skill_name, key, handler)

        return SkillContext(
            logger=structlog.get_logger().bind(skill=skill_name),
            storage=NamespacedSkillStorage(self.storage, skill_name),
            secrets=JsonFileSecrets(self.persona.data_dir / "secrets" / skill_name),
            persona_data_dir=self.persona.data_dir,
            config=resolved.skills.get(skill_name, {}),
            language=resolved.language_code,
            inject_turn=self.coordinator.inject_turn,
            inject_turn_and_wait=self.coordinator.inject_turn_and_wait,
            background_task=self.task_supervisor.start,
            start_input_claim=self.coordinator.start_input_claim,
            cancel_active_claim=self.coordinator.cancel_active_claim,
            subscribe_client_event=_subscribe_client_event,
            emit_server_event=self.server.send_server_event,
        )

    async def start(self, *, auto_connect: bool = True) -> None:
        """Bring up the per-persona runtime: storage, skills, state machine.

        Does NOT block. Returns once the Application is ready to receive
        callbacks from `AudioServer` (which lives at the `Runtime` level
        and is already listening). The `Runtime` is responsible for the
        process-wide concerns (logging setup, signal handlers, the audio
        server's `run()` task, the eventual `shutdown()` call).

        `auto_connect=True` (the default for the FIRST persona at boot)
        eagerly fires `wake_word` so the OpenAI session is ready before
        the user's first PTT — preserves the "instant first press" UX.
        `auto_connect=False` (for mid-session persona swaps) leaves the
        new persona in IDLE; the next user PTT triggers connect via the
        existing state-machine path.
        """
        # Snapshot the existing DB before opening it for the new run, so
        # today's backup captures the state we're about to mutate. Idempotent
        # (no-op if today's snapshot exists). On a fresh checkout this is a
        # no-op since the DB doesn't exist yet.
        try:
            ensure_daily_snapshot(self.storage.db_path)
        except Exception:
            await logger.aexception("storage_snapshot_failed")

        await self.storage.init()
        # Spawn the FocusManager's actor task now that a loop is running.
        # Safe to do before any skill needs it — observers acquire/release
        # through the mailbox, so events are serialized from the first call.
        self.focus_manager.start()
        # T1.14: per-skill data_schema_version mismatch warnings BEFORE
        # setup so the operator sees the drift before any skill state
        # touches disk. The version is **persisted only after** setup_all
        # succeeds — a torn setup leaves stored at the old version, so
        # next boot re-warns the same way.
        await self._check_skill_schema_versions()
        await self.skill_registry.setup_all(self._build_skill_context)
        await self._persist_skill_schema_versions()

        self.state_machine.on_enter(AppState.CONNECTING, self._enter_connecting)
        self.state_machine.on_transition(self._on_state_transition)

        await logger.ainfo(
            "huxley_app_ready",
            persona=self.persona.name,
            skills=self.skill_registry.skill_names,
            tools=self.skill_registry.tool_names,
        )

        if auto_connect:
            # Auto-connect to OpenAI at startup so the first press is instant — no
            # lost audio from "the first half of what I said while holding the
            # button." Idle sessions cost zero tokens (see turns.md §7), so there's
            # no reason to stay disconnected until the user presses. If the connect
            # fails, `_enter_connecting` catches it and drops back to IDLE; the
            # user can retry manually.
            await self.state_machine.trigger("wake_word")

    async def shutdown(self) -> None:
        """Tear down all subsystems in reverse order."""
        await logger.ainfo("huxley_shutting_down")
        self._shutting_down = True
        # First: stop dispatching client_event to skills. A handler
        # firing mid-shutdown could route through a coordinator /
        # FocusManager we're about to stop. Inbound messages still
        # log telemetry; only the skill-dispatch fan-out is gated.
        # Round-3 review (2026-04-29) found a window between this
        # method's per-skill unregister loop and the actual shutdown
        # of FocusManager / coordinator where a freshly-arrived event
        # could land in a half-stopped graph.
        self.server.disable_client_event_dispatch()

        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        if self.provider.is_connected:
            await self.provider.disconnect(save_summary=True)

        await self.coordinator.interrupt()
        # Stop the FocusManager AFTER the coordinator's interrupt (which
        # fires its own NONE cleanup through the observer directly). FM stop
        # drains any pending mailbox events + delivers StopAll to all
        # remaining Activities. Safe to call here — skills have torn down
        # by now; no new acquires arrive.
        await self.focus_manager.stop()
        # Unregister every skill's client_event subscriptions BEFORE
        # teardown_all runs. Otherwise a buggy teardown could trigger
        # a handler we're about to dismantle, or — worse — a client
        # message racing the shutdown could fire a handler whose
        # backing skill is already half-destructed. Cheap because the
        # registry is a single dict.
        for name in self.skill_registry.skill_names:
            self.server.unregister_client_event_subscribers(name)

        async def _on_teardown_error(skill_name: str, exc: BaseException) -> None:
            # SDK can't depend on structlog; framework supplies the
            # logging callback so a buggy skill's teardown is loud
            # without blocking the rest. Round-3 review (2026-04-29):
            # before this, an unguarded `for x: await x.teardown()`
            # let one raising skill leave B+C up with live background
            # tasks until task_supervisor.stop() cancelled them.
            await logger.aexception("skill.teardown_failed", skill=skill_name, exc_info=exc)

        await self.skill_registry.teardown_all(on_error=_on_teardown_error)
        # Cancel any background tasks the skills had supervised. Skills
        # got first crack at clean cancellation via their own teardown
        # (which can call `handle.cancel()` for tasks they want to stop
        # gracefully); this is the safety-net cancel for whatever's left.
        await self.task_supervisor.stop()
        await self.storage.close()

        await logger.ainfo("huxley_stopped")

    # --- State machine callbacks ---

    async def _enter_connecting(self) -> None:
        # Resolve the persona for whatever language the active client
        # requested (or the default, pre-connect). This rebuilds per-skill
        # configs with i18n overrides merged and localized ui_strings
        # pushed into the coordinator so status labels match the language
        # the upcoming OpenAI session will run in.
        await self._apply_language(self._active_language)
        await self.server.send_status(
            _STATUS_CONNECTING.get(self._resolved_persona.language_code, _STATUS_CONNECTING["en"])
        )
        try:
            await self.provider.connect(language=self._resolved_persona.language_code)
            await self.state_machine.trigger("connected")
            await self.server.send_status(
                _STATUS_CONNECTED.get(
                    self._resolved_persona.language_code, _STATUS_CONNECTED["en"]
                )
            )
        except Exception:
            await logger.aexception("connection_failed")
            await self.state_machine.trigger("failed")
            await self.server.send_status(
                _STATUS_FAILED.get(self._resolved_persona.language_code, _STATUS_FAILED["en"])
            )

    async def _apply_language(self, language: str | None) -> None:
        """Resolve the persona for `language`, push ui_strings to the
        coordinator, and reconfigure skills so their internal state
        reflects the active language before the next LLM session opens.

        Idempotent: if the resolved language already matches, the call
        is a cheap re-resolve + push. Unsupported codes silently fall
        back to the persona's default (see `PersonaSpec.resolve`).
        """
        self._resolved_persona = self.persona.resolve(language)
        self._active_language = self._resolved_persona.language_code
        self.coordinator.set_ui_strings(self._resolved_persona.ui_strings or None)
        await self.skill_registry.reconfigure_all(self._build_skill_context)
        await logger.ainfo(
            "app.language_applied",
            language=self._resolved_persona.language_code,
            ui_strings=sorted(self._resolved_persona.ui_strings.keys()),
        )

    async def _on_state_transition(self, new_state: AppState) -> None:
        # T1.13: AudioServer is shared across the current Application
        # AND the previous one being torn down in the background after
        # a persona swap. If we relay state transitions while
        # `_shutting_down`, the old app's IDLE/disconnect transitions
        # leak to the NEW persona's connection and the PWA sees a
        # spurious CONVERSING→IDLE flap (firing the error tone +
        # poisoning the appState the PTT handler dispatches on).
        # Suppress here so the dying app can't speak to the wire.
        if self._shutting_down:
            return
        await self.server.send_state(new_state.name)

    # --- Session callbacks ---

    async def _cost_kill_switch_disconnect(self) -> None:
        """Cost ceiling crossed — drop the OpenAI session, save summary.

        Triggered by `CostTracker` when the day's cost crosses the
        kill-switch threshold (default $20). Disconnect halts ongoing
        token consumption; auto-reconnect will fire on the next user
        action (PTT). The summary is preserved so context survives the
        forced reset.
        """
        await logger.aerror("app.cost_kill_switch_disconnect")
        if self.provider.is_connected:
            await self.provider.disconnect(save_summary=True)

    async def _on_session_end(self, summary: str | None) -> None:
        """OpenAI session receive loop exited — clean up + schedule reconnect.

        `summary` is the LLM-generated conversation summary the
        provider produced on its way out (None if `disconnect` was
        called with `save_summary=False` or the WS died unexpectedly
        before a summary could be computed). The framework owns the
        storage write — calling `Storage.end_session(session_id,
        summary)` here, with the active session id captured in
        `_on_transcript`, eliminates the race where the provider's
        post-callback `save_summary` was attributed to the next
        session's row (T1.12 critic finding).
        """
        # Finalize the active session row before any other teardown so
        # the storage write happens against the right id even if a
        # reconnect races into a new session below.
        if self._active_session_id is not None:
            sid = self._active_session_id
            self._active_session_id = None
            try:
                await self.storage.end_session(sid, summary)
            except Exception:
                await logger.aexception("session_end_persist_failed", session_id=sid)

        await self.coordinator.on_session_disconnected()
        if self.state_machine.state == AppState.CONVERSING:
            await self.state_machine.trigger("disconnect")

        will_reconnect = not self._shutting_down and self.state_machine.state == AppState.IDLE
        await logger.ainfo(
            "app.session_end",
            shutting_down=self._shutting_down,
            state=self.state_machine.state.name,
            will_reconnect=will_reconnect,
        )

        if not will_reconnect:
            return
        self._reconnect_task = asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self) -> None:
        """Retry connect with backoff until success or state leaves IDLE.

        Each attempt triggers a fresh wake_word → CONNECTING transition;
        `_enter_connecting` calls `provider.connect()` and flips us to
        CONVERSING on success or back to IDLE on failure. The loop
        watches the resulting state to decide whether to keep retrying.
        After 3 consecutive failures an audible beep plays before every
        subsequent attempt so a blind user knows the device is alive.
        """
        await logger.ainfo("session_auto_reconnect")

        def is_connected() -> bool:
            return self.state_machine.state == AppState.CONVERSING

        async def attempt() -> bool:
            if self.state_machine.state != AppState.IDLE:
                return True  # someone else (user PTT) already reconnected
            # InvalidTransitionError is possible if another task raced us
            # into CONNECTING. Treat as "someone else is handling it" —
            # the state check below tells us whether it worked.
            with contextlib.suppress(InvalidTransitionError):
                await self.state_machine.trigger("wake_word")
            return is_connected()

        async def announce() -> None:
            await self.server.send_audio(no_signal_tone_pcm())

        def should_continue() -> bool:
            return not self._shutting_down and self.state_machine.state == AppState.IDLE

        await run_reconnect_loop(
            connect_attempt=attempt,
            announce=announce,
            should_continue=should_continue,
            sleep=asyncio.sleep,
        )

    async def _on_transcript(self, role: str, text: str) -> None:
        # Lazily start (or resume) the session row on the first
        # transcript. The 30-minute resume window collapses
        # fragmenting reconnects (auto-reconnect, language switch,
        # cost kill, browser refresh) into one user-visible
        # conversation. Storage handles the resume vs. new-session
        # decision; we just hold the active id.
        if self._active_session_id is None:
            try:
                self._active_session_id = await self.storage.start_or_resume_session()
            except Exception:
                await logger.aexception("session_start_failed")
        if self._active_session_id is not None:
            try:
                await self.storage.record_turn(self._active_session_id, role, text)
            except Exception:
                await logger.aexception(
                    "session_record_turn_failed",
                    session_id=self._active_session_id,
                    role=role,
                )
        await self.server.send_transcript(role, text)

    # --- Session history (T1.12) ---

    async def on_list_sessions(self) -> None:
        """Client opened the SessionsSheet — return persisted history."""
        sessions = await self.storage.list_sessions()
        payload = [
            {
                "id": s.id,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "last_turn_at": s.last_turn_at,
                "turn_count": s.turn_count,
                "preview": s.preview,
                "summary": s.summary,
            }
            for s in sessions
        ]
        await self.server.send_sessions_list(payload)

    async def on_get_session(self, session_id: int) -> None:
        """Client clicked a session preview — return its full transcript."""
        turns = await self.storage.get_session_turns(session_id)
        payload = [{"idx": t.idx, "role": t.role, "text": t.text} for t in turns]
        await self.server.send_session_detail(session_id, payload)

    async def on_delete_session(self, session_id: int) -> None:
        """Client tapped Delete on the SessionDetailSheet — privacy floor."""
        await self.storage.delete_session(session_id)
        await self.server.send_session_deleted(session_id)

    # --- Client callbacks ---

    async def on_wake_word(self) -> None:
        if self.state_machine.state != AppState.IDLE:
            await logger.ainfo(
                "app.wake_word_rejected",
                state=self.state_machine.state.name,
            )
            return
        await self.server.send_audio_clear()
        await self.state_machine.trigger("wake_word")

    async def on_reset(self) -> None:
        """Drop the current OpenAI session and reconnect fresh — dev tool."""
        await logger.ainfo("app.reset", state=self.state_machine.state.name)
        await self.coordinator.interrupt()
        await self.storage.clear_summaries()
        if self.provider.is_connected:
            await self.provider.disconnect(save_summary=False)
        # on_session_end fires from the receive loop's finally clause,
        # transitions state → IDLE, and schedules a fresh auto-reconnect.
        # Nothing else needed here.

    async def on_language_select(self, language: str | None) -> None:
        """Client asked for a specific persona translation on this session.

        Fires right after a client's WebSocket handshake completes (see
        `AudioServer._handle_connection`), or on an in-session
        `set_language` message. If the requested language differs from
        the currently-running session, drop the OpenAI session so
        auto-reconnect spins up a fresh one in the new language. If it
        matches (or is `None` meaning "persona default"), this is a cheap
        no-op — idempotent by design so duplicate client selects don't
        churn the LLM session.
        """
        resolved = self.persona.resolve(language)
        target = resolved.language_code
        current = self._resolved_persona.language_code
        await logger.ainfo(
            "app.language_select",
            requested=language,
            resolved=target,
            current=current,
            supported=list(self.persona.supported_languages),
        )
        # Always capture the client's preference so the next connect,
        # even from a different code path (auto-reconnect), uses it.
        self._active_language = target
        if target == current and self.provider.is_connected:
            # Session already on the right language; refresh skill
            # configs in-place and push localized ui_strings but keep
            # the LLM session alive. Cheap path for "client reconnected
            # with the same language."
            await self._apply_language(target)
            return
        if self.provider.is_connected:
            # Different language — drop the current session; the
            # provider's `on_session_end` callback flips the state
            # machine to IDLE, which triggers auto-reconnect via
            # `_enter_connecting`, which calls `_apply_language` with
            # the new `_active_language`.
            await self.provider.disconnect(save_summary=True)
            return
        # Not connected yet (startup race or mid-reconnect). Apply now
        # so the next connect picks it up naturally.
        await self._apply_language(target)

    async def on_audio_frame(self, pcm: bytes) -> None:
        """Mic frame from client — forward to the coordinator."""
        await self.coordinator.on_user_audio_frame(pcm)

    async def on_ptt_start(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            await logger.ainfo(
                "app.ptt_rejected",
                state=self.state_machine.state.name,
            )
            await self.server.send_status(
                _STATUS_WAIT.get(self._resolved_persona.language_code, _STATUS_WAIT["en"])
            )
            return
        await self.coordinator.on_ptt_start()

    async def on_ptt_stop(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            return
        await self.coordinator.on_ptt_stop()
