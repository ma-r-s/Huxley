"""Application orchestrator — wires all subsystems together.

This is the top-level coordinator. It owns the state machine, all subsystems,
the `TurnCoordinator`, and every callback that connects them. No other module
imports from app.py — communication is through callbacks injected at
construction time.

Audio I/O is owned by the client (browser, ESP32). The server receives mic
frames over WebSocket, routes them to OpenAI via the session manager, and
streams response audio back through the same WebSocket via the `AudioServer`.
Turn sequencing (model speech vs tool factories, interrupts) lives on the
`TurnCoordinator` — see `docs/turns.md`.
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

import structlog

from abuel_os.logging import setup_logging
from abuel_os.media.audiobook_player import AudiobookPlayer
from abuel_os.server.server import AudioServer
from abuel_os.session.manager import SessionManager
from abuel_os.skills import SkillRegistry
from abuel_os.skills.audiobooks import AudiobooksSkill
from abuel_os.skills.system import SystemSkill
from abuel_os.state.machine import StateMachine
from abuel_os.storage.db import Storage
from abuel_os.turn import TurnCoordinator
from abuel_os.types import AppState

if TYPE_CHECKING:
    from pathlib import Path

    from abuel_os.config import Settings

logger = structlog.get_logger()


class Application:
    """Top-level application orchestrator.

    Creates and owns all subsystems. Wires callbacks for inter-component
    communication. Manages the async main loop and graceful shutdown.
    """

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.state_machine = StateMachine()
        self.storage = Storage(config.db_path)
        self.skill_registry = SkillRegistry()

        self.server = AudioServer(
            host=config.server_host,
            port=config.server_port,
            on_wake_word=self._on_wake_word,
            on_ptt_start=self._on_ptt_start,
            on_ptt_stop=self._on_ptt_stop,
            on_audio_frame=self._on_audio_frame,
        )

        # Stateless ffmpeg wrapper — used by AudiobooksSkill to build factory
        # closures. The player has no callbacks or mutable state; each
        # `stream()` call is an independent subprocess, cancelled cleanly by
        # the coordinator's `interrupt()` when a new turn starts.
        self.audiobook_player = AudiobookPlayer(
            ffmpeg_path=config.ffmpeg_path,
            ffprobe_path=config.ffprobe_path,
        )

        self.audiobooks_skill = AudiobooksSkill(
            library_path=config.audiobook_library_path,
            player=self.audiobook_player,
            storage=self.storage,
        )
        self.system_skill = SystemSkill()
        self.skill_registry.register(self.audiobooks_skill)
        self.skill_registry.register(self.system_skill)

        # Session manager — thin transport. Callbacks reference
        # `self.coordinator` via closures so construction order stays simple:
        # session is built before the coordinator; the lambdas resolve
        # `self.coordinator` at call time (after coordinator construction).
        self.session = SessionManager(
            config=config,
            skill_registry=self.skill_registry,
            storage=self.storage,
            on_audio_delta=lambda pcm: self.coordinator.on_audio_delta(pcm),
            on_function_call=lambda cid, name, args: self.coordinator.on_function_call(
                cid, name, args
            ),
            on_response_done=lambda: self.coordinator.on_response_done(),
            on_audio_done=lambda: self.coordinator.on_audio_done(),
            on_session_end=self._on_session_end,
            on_transcript=self._on_transcript,
        )

        # Turn coordinator — owns all audio sequencing and interrupt logic.
        self.coordinator = TurnCoordinator(
            send_audio=self.server.send_audio,
            send_audio_clear=self.server.send_audio_clear,
            send_status=self.server.send_status,
            send_model_speaking=self.server.send_model_speaking,
            send_user_audio_to_session=self.session.send_audio,
            send_dev_event=self.server.send_dev_event,
            oai_send_function_output=self.session.send_function_output,
            oai_commit=self.session.commit_and_respond,
            oai_cancel=self.session.cancel_response,
            oai_request_response=self.session.request_response,
            oai_is_connected=lambda: self.session.is_connected,
            dispatch_tool=self.skill_registry.dispatch,
        )

        self._shutdown_event = asyncio.Event()
        # Flipped True in `_shutdown()` before disconnecting the session, so
        # `_on_session_end`'s auto-reconnect policy knows to stand down.
        self._shutting_down = False
        # Held reference to the auto-reconnect background task so it isn't
        # garbage-collected mid-flight (RUF006). See `_on_session_end`.
        self._reconnect_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Initialize subsystems and run the main loop."""
        from pathlib import Path

        log_file: Path | None = self.config.log_file
        if log_file is None:
            log_file = Path("logs/abuel_os.log")
        setup_logging(
            level=self.config.log_level,
            json_output=self.config.log_json,
            log_file=log_file,
        )
        await logger.ainfo("abuel_os_starting")

        await self.storage.init()
        await self.skill_registry.setup_all()

        self.state_machine.on_enter(AppState.CONNECTING, self._enter_connecting)
        self.state_machine.on_transition(self._on_state_transition)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_shutdown)

        await logger.ainfo(
            "abuel_os_ready",
            skills=self.skill_registry.skill_names,
            tools=self.skill_registry.tool_names,
            server=f"ws://{self.config.server_host}:{self.config.server_port}",
        )
        print(
            f"\033[1;32m[AbuelOS] Server listening on "
            f"ws://{self.config.server_host}:{self.config.server_port}\033[0m",
            flush=True,
        )

        server_task = asyncio.create_task(self.server.run())

        # Auto-connect to OpenAI at startup so the first press is instant — no
        # lost audio from "the first half of what I said while holding the
        # button." Idle sessions cost zero tokens (see turns.md §7), so there's
        # no reason to stay disconnected until the user presses. If the connect
        # fails, `_enter_connecting` catches it and drops back to IDLE; the
        # user can retry manually.
        await self.state_machine.trigger("wake_word")

        await self._shutdown_event.wait()

        server_task.cancel()
        await self._shutdown()

    def _signal_shutdown(self) -> None:
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Tear down all subsystems in reverse order."""
        import contextlib

        await logger.ainfo("abuel_os_shutting_down")
        # Must flip BEFORE disconnecting so `_on_session_end` sees it and
        # doesn't kick off an auto-reconnect during teardown.
        self._shutting_down = True

        # Any in-flight reconnect task must be cancelled — it'd try to open a
        # new session while we're tearing the current one down.
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        if self.session.is_connected:
            await self.session.disconnect(save_summary=True)

        await self.coordinator.interrupt()
        await self.skill_registry.teardown_all()
        await self.storage.close()

        await logger.ainfo("abuel_os_stopped")

    # --- State machine callbacks ---

    async def _enter_connecting(self) -> None:
        await self.server.send_status("Conectando…")
        try:
            await self.session.connect()
            await self.state_machine.trigger("connected")
            await self.server.send_status("Conectado — mantén el botón para hablar")
        except Exception:
            await logger.aexception("connection_failed")
            await self.state_machine.trigger("failed")
            await self.server.send_status("Error al conectar — intenta de nuevo")

    async def _on_state_transition(self, new_state: AppState) -> None:
        await self.server.send_state(new_state.name)

    # --- Session callbacks ---

    async def _on_session_end(self) -> None:
        """OpenAI session receive loop exited — clean up + schedule reconnect.

        The receive loop exits on: explicit `disconnect()` (normal shutdown or
        55-min `_timeout_loop`), a dropped WebSocket, or an unhandled error in
        the loop. In all non-shutdown cases we auto-reconnect so the user
        never experiences the "CONNECTING" phase between turns — first press
        of every gesture is instant.

        The reconnect is scheduled as a background task rather than invoked
        inline because `_on_session_end` runs from inside
        `session.disconnect()`'s finally chain — firing `connect()` synchronously
        would overwrite `self._ws` before `disconnect()` finishes cleaning it up.
        """
        await self.coordinator.on_session_disconnected()
        if self.state_machine.state == AppState.CONVERSING:
            await self.state_machine.trigger("disconnect")

        if self._shutting_down:
            return
        if self.state_machine.state != AppState.IDLE:
            return
        self._reconnect_task = asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self) -> None:
        """Trigger a fresh wake_word → CONNECTING → CONVERSING transition.

        Runs in its own task so the previous `disconnect()` can fully unwind
        first. If the reconnect fails, `_enter_connecting` catches it and
        trips `failed`, dropping back to IDLE — the user retries with a
        manual press. No retry loop here: a broken OpenAI endpoint would
        otherwise spam reconnects.
        """
        await logger.ainfo("session_auto_reconnect")
        try:
            await self.state_machine.trigger("wake_word")
        except Exception:
            await logger.aexception("auto_reconnect_failed")

    async def _on_transcript(self, role: str, text: str) -> None:
        await self.server.send_transcript(role, text)

    # --- Client callbacks ---

    async def _on_wake_word(self) -> None:
        if self.state_machine.state != AppState.IDLE:
            return
        await self.server.send_audio_clear()
        await self.state_machine.trigger("wake_word")

    async def _on_audio_frame(self, pcm: bytes) -> None:
        """Mic frame from client — forward to the coordinator."""
        await self.coordinator.on_user_audio_frame(pcm)

    async def _on_ptt_start(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            # Rare race — client only sends ptt_start from CONVERSING.
            # If we land here it's usually the auto-reconnect window.
            await self.server.send_status("Conectando — espera un segundo")
            return
        await self.coordinator.on_ptt_start()

    async def _on_ptt_stop(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            return
        await self.coordinator.on_ptt_stop()
