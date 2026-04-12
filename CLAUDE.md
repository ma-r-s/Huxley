# AbuelOS

Voice assistant for a blind elderly user. Conversational AI (OpenAI Realtime API) with extensible skill system.

## Architecture

- **Application** (`app.py`): Orchestrator. Owns all subsystems, wires callbacks, manages lifecycle.
- **StateMachine** (`state/machine.py`): 4 states — IDLE, CONNECTING, CONVERSING, PLAYING. Transition table with async callbacks.
- **SkillRegistry** (`skills/__init__.py`): Collects tool definitions from skills, routes tool calls. Skills are `Protocol`-based (structural typing).
- **SessionManager** (`session/manager.py`): WebSocket lifecycle to OpenAI Realtime API. Streams audio, dispatches tool calls, handles reconnection.
- **AudioRouter** (`audio/router.py`): Routes mic input to wake word detector (always) and session (when conversing). Runs PyAudio capture in thread.
- **MpvClient** (`media/mpv.py`): Async IPC client for mpv (JSON over Unix socket). Manages media playback.
- **Storage** (`storage/db.py`): aiosqlite wrapper for bookmarks, conversation summaries, settings.

## Key patterns

- No event bus — direct callbacks through Application
- Skills return `ToolResult(action="start_playback")` to signal side effects without coupling to session/state
- Audio capture at 24kHz, downsampled to 16kHz for wake word detection
- mpv runs in `--idle` mode, communicates via IPC socket
- WebSocket disconnected during media playback (saves API cost)

## Commands

```bash
uv sync                              # Install deps
uv run ruff check src/ tests/        # Lint
uv run ruff format src/ tests/       # Format
uv run mypy src/                     # Type check
uv run pytest tests/unit/ -v         # Unit tests
uv run pytest tests/ -v -m "not integration"  # All non-integration tests
uv run python -m abuel_os            # Run the assistant
```

## Rules

- Always use `uv`, never `npm`/`pip`/`yarn`
- `ruff` for linting and formatting
- `mypy --strict` must pass
- Tests for all non-hardware abstractions
- Skills must implement the `Skill` protocol from `types.py`
- No circular imports — dependency flows downward (see architecture)
