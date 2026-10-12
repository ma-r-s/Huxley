"""Huxley framework configuration via environment variables.

Infrastructure / runtime knobs only. Persona-driven settings (system
prompt, voice, language, skill list, per-skill config, data directory)
live in `personas/<name>/persona.yaml` — see `huxley.persona`.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — pydantic-settings needs the runtime symbol

from pydantic import Field, SecretStr
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
    openai_api_key: str | None = None
    # Same audio price as the shut-down gpt-4o-mini-realtime-preview,
    # plus reasoning. See docs/triage.md T1.25.
    openai_model: str = "gpt-realtime-2.1-mini"
    # `persona.voice` is the source of truth. Setting `HUXLEY_OPENAI_VOICE`
    # overrides it for a single run (useful when A/B-testing voices without
    # editing `persona.yaml`). `None` = defer to the persona.
    openai_voice: str | None = None

    # --- Persona selection ---
    # When unset, the loader auto-discovers the only persona under
    # `./personas/` (if exactly one exists). Set to a directory name
    # under `./personas/` to disambiguate when multiple are present.
    persona: str | None = None
    # Start `persona` when the server starts instead of on the first
    # client connection (lazy boot, the default). Production sets it so
    # reminders, Telegram and `/health` work before the walkie reconnects
    # after a power cut or a deploy. Needs `persona`; without it the
    # server boots lazily and says so (`runtime.persona_boot_skipped`).
    persona_at_boot: bool = False

    # --- WebSocket server ---
    server_host: str = "localhost"
    server_port: int = 8765
    # The hardware client's token: `Authorization: Bearer <token>` on the
    # firmware routes (`/firmware/*`, docs/protocol.md "HTTP: /firmware and
    # /admin"). Unset (or empty), those routes refuse everyone with 503.
    # SecretStr: never printed in a repr or a log.
    device_token: SecretStr | None = None

    # --- Session ---
    # OpenAI ends a Realtime session at 60 minutes. After this many
    # minutes the provider reconnects at the first idle moment (summary
    # carried over), so the hard limit doesn't land in the middle of a turn.
    # Must leave time before the hard limit to find an idle moment.
    session_refresh_minutes: float = Field(default=50, gt=0, lt=60)

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = False
    log_file: Path | None = None  # If set, also write JSON logs to this path
    # Archived run logs older than this are deleted at startup; 0 keeps all.
    log_keep_days: int = Field(default=0, ge=0)
