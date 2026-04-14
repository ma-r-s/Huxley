"""Application orchestrator — wires all subsystems together.

This is the top-level coordinator. It owns the state machine, all subsystems,
and all the callbacks that connect them. No other module imports from app.py —
communication is through callbacks injected at construction time.

Audio I/O is owned by the client (browser, ESP32). The server receives mic
frames over WebSocket, routes them to OpenAI, and streams response audio AND
audiobook audio back through the same WebSocket channel.
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING, Any

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

        # PTT state — gating logic lives in _on_audio_frame
        self._ptt_active = False
        self._ptt_frames_sent = 0
        self._assistant_speaking = False

        self.session = SessionManager(
            config=config,
            skill_registry=self.skill_registry,
            storage=self.storage,
            on_audio_delta=self._on_audio_delta,
            on_tool_action=self._on_tool_action,
            on_session_end=self._on_session_end,
            on_model_done=self._on_model_done,
            on_transcript=self._on_transcript,
            on_dev_event=self._on_dev_event,
        )

        self.server = AudioServer(
            host=config.server_host,
            port=config.server_port,
            on_wake_word=self._on_wake_word,
            on_ptt_start=self._on_ptt_start,
            on_ptt_stop=self._on_ptt_stop,
            on_audio_frame=self._on_audio_frame,
        )

        # AudiobookPlayer streams PCM chunks through the same server.send_audio
        # channel that the OpenAI model audio uses — one audio path for the client.
        self.audiobook_player = AudiobookPlayer(
            ffmpeg_path=config.ffmpeg_path,
            ffprobe_path=config.ffprobe_path,
            on_chunk=self._on_audiobook_chunk,
            on_finished=self._on_audiobook_finished,
            on_audio_clear=self._on_audio_clear,
        )

        self.audiobooks_skill = AudiobooksSkill(
            library_path=config.audiobook_library_path,
            player=self.audiobook_player,
            storage=self.storage,
        )
        self.system_skill = SystemSkill()
        self.skill_registry.register(self.audiobooks_skill)
        self.skill_registry.register(self.system_skill)

        self._shutdown_event = asyncio.Event()

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
        self.state_machine.on_enter(AppState.PLAYING, self._enter_playing)
        self.state_machine.on_exit(AppState.PLAYING, self._exit_playing)
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

        await self._shutdown_event.wait()

        server_task.cancel()
        await self._shutdown()

    def _signal_shutdown(self) -> None:
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Tear down all subsystems in reverse order."""
        await logger.ainfo("abuel_os_shutting_down")

        if self.state_machine.state == AppState.PLAYING:
            try:
                await self.audiobooks_skill.save_current_position()
            except Exception:
                await logger.aexception("shutdown_save_position_failed")

        if self.session.is_connected:
            await self.session.disconnect(save_summary=True)

        await self.skill_registry.teardown_all()
        await self.audiobook_player.stop()
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

    async def _enter_playing(self) -> None:
        """Entering PLAYING — the skill has pre-loaded the player in paused
        state. Disconnect the OpenAI session (save API cost), then resume the
        audiobook stream so book audio begins flowing right after the model's
        verbal acknowledgement finishes.
        """
        if self.session.is_connected:
            await self.session.disconnect(save_summary=True)
        await self.audiobook_player.resume()

    async def _exit_playing(self) -> None:
        """Exiting PLAYING — persist position and stop the player."""
        try:
            await self.audiobooks_skill.save_current_position()
        except Exception:
            await logger.aexception("exit_playing_save_failed")
        try:
            await self.audiobook_player.stop()
        except Exception:
            await logger.aexception("exit_playing_stop_failed")

    # --- Session callbacks ---

    async def _on_audio_delta(self, audio: bytes) -> None:
        if not self._assistant_speaking:
            self._assistant_speaking = True
            await self.server.send_model_speaking(True)
            await self.server.send_status("Respondiendo…")
        await self.server.send_audio(audio)

    async def _on_tool_action(self, action: str) -> None:
        if action == "start_playback":
            await self.state_machine.trigger("start_playback")

    async def _on_session_end(self) -> None:
        self._ptt_active = False
        self._ptt_frames_sent = 0
        self._assistant_speaking = False
        if self.state_machine.state == AppState.CONVERSING:
            await self.state_machine.trigger("disconnect")

    async def _on_transcript(self, role: str, text: str) -> None:
        await self.server.send_transcript(role, text)

    async def _on_dev_event(self, kind: str, payload: dict[str, Any]) -> None:
        """Forward session-originated dev-observability events to the client."""
        await self.server.send_dev_event(kind, payload)

    async def _on_state_transition(self, new_state: AppState) -> None:
        await self.server.send_state(new_state.name)

    # --- Audiobook player callbacks ---

    async def _on_audiobook_chunk(self, pcm: bytes) -> None:
        """PCM chunk from the audiobook player → forward to client speaker."""
        await self.server.send_audio(pcm)

    async def _on_audiobook_finished(self) -> None:
        """Audiobook played to EOF → transition PLAYING → IDLE."""
        if self.state_machine.state == AppState.PLAYING:
            await self.state_machine.trigger("playback_finished")

    async def _on_audio_clear(self) -> None:
        """Tell the client to drop any queued audio (fired on seek)."""
        await self.server.send_audio_clear()

    # --- Client callbacks ---

    async def _on_wake_word(self) -> None:
        current = self.state_machine.state
        if current not in (AppState.IDLE, AppState.PLAYING):
            return
        if current == AppState.PLAYING:
            # Interrupt playback: stop the ffmpeg stream immediately so no more
            # audiobook chunks are produced, persist position, then clear any
            # chunks still queued on the client. _exit_playing will also run
            # during the transition — its stop/save are idempotent.
            await self.audiobook_player.stop()
            await self.audiobooks_skill.save_current_position()
        # Fresh start — drop any residual audio on the client (audiobook tail
        # or leftover assistant speech from a prior turn).
        await self.server.send_audio_clear()
        await self.state_machine.trigger("wake_word")

    async def _on_audio_frame(self, pcm: bytes) -> None:
        """Mic frame from client — forward to session if PTT is active."""
        if self._ptt_active and self.session.is_connected and not self.session.is_model_speaking:
            await self.session.send_audio(pcm)
            self._ptt_frames_sent += 1

    async def _on_ptt_start(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            await self.server.send_status("No hay sesión activa — presiona Iniciar primero")
            return
        if self.session.is_model_speaking:
            await self.session.cancel_response()
            self._assistant_speaking = False
        # If a book was pre-loaded by the last tool call but the user is
        # interrupting mid-acknowledgement, stop the paused player too —
        # otherwise it would resume on the next state transition.
        if self.audiobook_player.is_playing:
            await self.audiobook_player.stop()
        # Always clear the client's audio queue on PTT press. Covers:
        #  - user interrupting the model mid-speech (buffered chunks not yet played)
        #  - trailing audio after model-done but before client's queue drained
        #  - any edge case where audio got queued out-of-band
        await self.server.send_audio_clear()
        self._ptt_active = True
        self._ptt_frames_sent = 0
        await self.server.send_status("Escuchando… (suelta para enviar)")

    async def _on_ptt_stop(self) -> None:
        if self.state_machine.state != AppState.CONVERSING:
            return
        frames = self._ptt_frames_sent
        self._ptt_active = False
        if frames < 3:
            await self.server.send_status("Muy corto — mantén el botón mientras hablas")
            await self.session.cancel_response()
            return
        await self.server.send_status("Enviado — esperando respuesta…")
        await self.session.commit_and_respond()

    async def _on_model_done(self) -> None:
        self._assistant_speaking = False
        await self.server.send_model_speaking(False)
        await self.server.send_status("Listo — mantén el botón para responder")
