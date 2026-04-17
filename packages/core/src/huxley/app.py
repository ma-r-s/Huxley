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
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from huxley.loader import discover_skills
from huxley.logging import setup_logging
from huxley.server.server import AudioServer
from huxley.session.manager import SessionManager
from huxley.state.machine import StateMachine
from huxley.storage.db import Storage
from huxley.storage.skill import NamespacedSkillStorage
from huxley.turn import TurnCoordinator
from huxley_sdk import AppState, SkillContext, SkillRegistry

if TYPE_CHECKING:
    from huxley.config import Settings

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

        # Skill discovery via entry points. Stage 2 hardcodes the enabled
        # skill list here; stage 4 will read it from `persona.yaml`.
        for name, skill_cls in discover_skills(["audiobooks", "system"]).items():
            del name  # name is on the instance via .name; entry-point key is just the dispatch
            self.skill_registry.register(skill_cls())

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
            on_commit_failed=lambda: self.coordinator.on_commit_failed(),
            on_session_end=self._on_session_end,
            on_transcript=self._on_transcript,
        )

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
        self._shutting_down = False
        self._reconnect_task: asyncio.Task[None] | None = None

    def _build_skill_context(self, skill_name: str) -> SkillContext:
        """Construct the SkillContext handed to a skill at setup() time.

        Stage 2: storage is namespaced per-skill; logger is bound with
        `skill=<name>`; persona_data_dir is CWD (stage 4 will swap to the
        persona's data directory). Per-skill config is derived from
        `Settings` defaults — same key shape the persona.yaml will deliver
        in stage 4.
        """
        return SkillContext(
            logger=structlog.get_logger().bind(skill=skill_name),
            storage=NamespacedSkillStorage(self.storage, skill_name),
            persona_data_dir=Path.cwd(),
            config=self._skill_config(skill_name),
        )

    def _skill_config(self, skill_name: str) -> dict[str, Any]:
        """Return the per-skill config dict.

        Forward-designed for stage 4: the keys here mirror what
        `persona.yaml`'s `skills.<name>:` block will deliver. Today they're
        sourced from `Settings`; tomorrow from YAML.
        """
        match skill_name:
            case "audiobooks":
                return {
                    "library": str(self.config.audiobook_library_path),
                    "ffmpeg": self.config.ffmpeg_path,
                    "ffprobe": self.config.ffprobe_path,
                }
            case "system":
                return {}
            case _:
                return {}

    async def run(self) -> None:
        """Initialize subsystems and run the main loop."""
        log_file: Path | None = self.config.log_file
        if log_file is None:
            log_file = Path("logs/huxley.log")
        setup_logging(
            level=self.config.log_level,
            json_output=self.config.log_json,
            log_file=log_file,
        )
        await logger.ainfo("huxley_starting")

        await self.storage.init()
        await self.skill_registry.setup_all(self._build_skill_context)

        self.state_machine.on_enter(AppState.CONNECTING, self._enter_connecting)
        self.state_machine.on_transition(self._on_state_transition)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_shutdown)

        await logger.ainfo(
            "huxley_ready",
            skills=self.skill_registry.skill_names,
            tools=self.skill_registry.tool_names,
            server=f"ws://{self.config.server_host}:{self.config.server_port}",
        )
        print(
            f"\033[1;32m[Huxley] Server listening on "
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

        await logger.ainfo("huxley_shutting_down")
        self._shutting_down = True

        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        if self.session.is_connected:
            await self.session.disconnect(save_summary=True)

        await self.coordinator.interrupt()
        await self.skill_registry.teardown_all()
        await self.storage.close()

        await logger.ainfo("huxley_stopped")

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
        """OpenAI session receive loop exited — clean up + schedule reconnect."""
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
        """Trigger a fresh wake_word → CONNECTING → CONVERSING transition."""
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
            await logger.ainfo(
                "app.wake_word_rejected",
                state=self.state_machine.state.name,
            )
            return
        await self.server.send_audio_clear()
        await self.state_machine.trigger("wake_word")

    async def _on_audio_frame(self, pcm: bytes) -> None:
        """Mic frame from client — forward to the coordinator."""
        await self.coordinator.on_user_audio_frame(pcm)

    async def _on_ptt_start(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            await logger.ainfo(
                "app.ptt_rejected",
                state=self.state_machine.state.name,
            )
            await self.server.send_status("Conectando — espera un segundo")
            return
        await self.coordinator.on_ptt_start()

    async def _on_ptt_stop(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            return
        await self.coordinator.on_ptt_stop()
