"""Application orchestrator — wires all subsystems together.

This is the top-level coordinator. It owns the state machine, all
subsystems, and all the callbacks that connect them. No other module
imports from app.py — communication is through callbacks injected
at construction time.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import TYPE_CHECKING

import structlog

from abuel_os.audio.router import AudioRouter
from abuel_os.logging import setup_logging
from abuel_os.media.mpv import MpvClient
from abuel_os.session.manager import SessionManager
from abuel_os.skills import SkillRegistry
from abuel_os.skills.audiobooks import AudiobooksSkill
from abuel_os.skills.system import SystemSkill
from abuel_os.state.machine import StateMachine
from abuel_os.storage.db import Storage
from abuel_os.types import AppState, WakeWordDetectorProtocol
from abuel_os.wakeword.detector import WakeWordDetector

if TYPE_CHECKING:
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
        self.mpv = MpvClient(config.mpv_socket_path)
        self.skill_registry = SkillRegistry()
        self.speaker_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self.mic_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        self.wakeword: WakeWordDetectorProtocol
        if config.dev_mode:
            from abuel_os.wakeword.keyboard import KeyboardWakeWord

            self.wakeword = KeyboardWakeWord(on_detected=self._on_wake_word)
        else:
            self.wakeword = WakeWordDetector(
                model_path=config.wakeword_model_path,
                threshold=config.wakeword_threshold,
                on_detected=self._on_wake_word,
            )

        self.session = SessionManager(
            config=config,
            skill_registry=self.skill_registry,
            storage=self.storage,
            on_audio_delta=self._on_audio_delta,
            on_tool_action=self._on_tool_action,
            on_session_end=self._on_session_end,
        )

        self.audio_router = AudioRouter(
            mic_queue=self.mic_queue,
            wakeword_detector=self.wakeword,
            session_manager=self.session,
        )

        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Initialize subsystems and run the main loop."""
        setup_logging(level=self.config.log_level, json_output=self.config.log_json)
        await logger.ainfo("abuel_os_starting")

        # Initialize storage
        await self.storage.init()

        # Register skills
        audiobooks = AudiobooksSkill(
            library_path=self.config.audiobook_library_path,
            mpv=self.mpv,
            storage=self.storage,
        )
        system = SystemSkill(mpv=self.mpv)
        self.skill_registry.register(audiobooks)
        self.skill_registry.register(system)
        await self.skill_registry.setup_all()

        # Wire state machine callbacks
        self.state_machine.on_enter(AppState.CONNECTING, self._enter_connecting)
        self.state_machine.on_enter(AppState.PLAYING, self._enter_playing)
        self.state_machine.on_exit(AppState.PLAYING, self._exit_playing)

        # Start mpv before arming wake word (wake word fires immediately in dev mode)
        await self.mpv.start()

        # Setup wake word last — in dev mode this starts the keyboard listener
        await self.wakeword.setup()

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_shutdown)

        await logger.ainfo(
            "abuel_os_ready",
            skills=self.skill_registry.skill_names,
            tools=self.skill_registry.tool_names,
        )

        # Run audio router (blocks until shutdown)
        router_task = asyncio.create_task(self.audio_router.run())

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Graceful shutdown
        self.audio_router.stop()
        router_task.cancel()
        await self._shutdown()

    def _signal_shutdown(self) -> None:
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Tear down all subsystems in reverse order."""
        await logger.ainfo("abuel_os_shutting_down")

        # Save audiobook position if playing
        if self.state_machine.state == AppState.PLAYING:
            try:
                position = await self.mpv.get_position()
                # We'd need the current book_id — store it as state
                await logger.ainfo("saving_playback_position", position=position)
            except Exception:
                await logger.aexception("error_saving_position")

        # Disconnect session if active
        if self.session.is_connected:
            await self.session.disconnect(save_summary=True)

        # Teardown skills
        await self.skill_registry.teardown_all()

        # Stop mpv
        await self.mpv.stop()

        # Close storage
        await self.storage.close()

        await logger.ainfo("abuel_os_stopped")

    # --- State machine callbacks ---

    async def _enter_connecting(self) -> None:
        """Connect to the Realtime API."""
        try:
            await self.session.connect()
            self.audio_router.conversation_mode = True
            await self.state_machine.trigger("connected")
        except Exception:
            await logger.aexception("connection_failed")
            self.audio_router.conversation_mode = False
            await self.state_machine.trigger("failed")

    async def _enter_playing(self) -> None:
        """Switch to playback mode — disconnect session, enable wake word."""
        self.audio_router.conversation_mode = False
        self.audio_router.suppress_wakeword = False
        if self.session.is_connected:
            await self.session.disconnect(save_summary=True)

    async def _exit_playing(self) -> None:
        """Exiting playback — save position."""
        try:
            position = await self.mpv.get_position()
            if position > 0:
                await logger.ainfo("playback_paused", position=position)
        except Exception:
            pass

    # --- Session callbacks ---

    async def _on_audio_delta(self, audio: bytes) -> None:
        """Receive audio from the Realtime API and queue for speaker."""
        self.audio_router.suppress_wakeword = True
        with contextlib.suppress(asyncio.QueueFull):
            self.speaker_queue.put_nowait(audio)

    async def _on_tool_action(self, action: str) -> None:
        """Handle side effects from skill tool calls."""
        if action == "start_playback":
            await self.state_machine.trigger("start_playback")

    async def _on_session_end(self) -> None:
        """Called when the session WebSocket closes."""
        self.audio_router.conversation_mode = False
        self.audio_router.suppress_wakeword = False
        if self.state_machine.state == AppState.CONVERSING:
            await self.state_machine.trigger("disconnect")

    # --- Wake word callback ---

    async def _on_wake_word(self) -> None:
        """Wake word detected — transition based on current state."""
        current = self.state_machine.state
        if current in (AppState.IDLE, AppState.PLAYING):
            if current == AppState.PLAYING:
                await self.mpv.pause()
            await self.state_machine.trigger("wake_word")
