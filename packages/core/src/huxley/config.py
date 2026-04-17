"""Huxley framework configuration via environment variables.

Infrastructure / runtime knobs only. Persona-driven settings (system
prompt, voice, language, skill list, per-skill config, data directory)
live in `personas/<name>/persona.yaml` — see `huxley.persona`.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — pydantic-settings needs the runtime symbol

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Huxley framework configuration.

    Values are loaded from environment variables prefixed with `HUXLEY_`
    (e.g. `HUXLEY_OPENAI_API_KEY`). A `.env` file in the working
    directory is read automatically.

    Anything persona-shaped lives in `persona.yaml`, not here. Legacy env
    vars removed in stage 4 (`HUXLEY_SYSTEM_PROMPT`, `HUXLEY_DB_PATH`,
    `HUXLEY_AUDIOBOOK_LIBRARY_PATH`, `HUXLEY_FFMPEG_PATH`,
    `HUXLEY_FFPROBE_PATH`) are ignored on parse rather than crashing
    startup, so an old `.env` keeps working until the dev cleans it up.
    """

    model_config = SettingsConfigDict(
        env_prefix="HUXLEY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI Realtime API ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini-realtime-preview"
    # `persona.voice` is the source of truth. Setting `HUXLEY_OPENAI_VOICE`
    # overrides it for a single run (useful when A/B-testing voices without
    # editing `persona.yaml`). `None` = defer to the persona.
    openai_voice: str | None = None

    # --- Persona selection ---
    # When unset, falls back to the default persona directory
    # (`./personas/abuelos`). Set to a directory name under `./personas/`.
    persona: str | None = None

    # --- WebSocket server ---
    server_host: str = "localhost"
    server_port: int = 8765

    # --- Wake word (Pi hardware client — not used by server directly) ---
    wakeword_model_path: str = "models/hey_abuela.tflite"
    wakeword_threshold: float = 0.7

    # --- Session ---
    conversation_max_minutes: int = 55

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = False
    log_file: Path | None = None  # If set, also write JSON logs to this path
