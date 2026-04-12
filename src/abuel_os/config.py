"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All AbuelOS configuration.

    Values are loaded from environment variables prefixed with ABUEL_
    (e.g., ABUEL_OPENAI_API_KEY). A .env file in the working directory
    is also read automatically.
    """

    model_config = SettingsConfigDict(
        env_prefix="ABUEL_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # --- OpenAI Realtime API ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini-realtime-preview"
    openai_voice: str = "coral"

    # --- Audio ---
    audio_sample_rate: int = 24_000
    wakeword_sample_rate: int = 16_000
    audio_frame_duration_ms: int = 80
    audio_device_index: int | None = None

    # --- Wake word ---
    wakeword_model_path: str = "models/hey_abuela.tflite"
    wakeword_threshold: float = 0.7

    # --- Media ---
    mpv_socket_path: str = "/tmp/abuel_os_mpv.sock"
    audiobook_library_path: Path = Path("data/audiobooks")

    # --- Storage ---
    db_path: Path = Path("data/abuel_os.db")

    # --- Session ---
    silence_timeout_seconds: int = 30
    conversation_max_minutes: int = 55

    # --- System prompt ---
    system_prompt: str = (
        "Eres AbuelOS, un asistente de voz amigable para una persona mayor ciega "
        "que vive en Villavicencio, Colombia. Habla en español colombiano, con "
        "paciencia y claridad. Usa un tono cálido y respetuoso. Cuando el usuario "
        "haga una solicitud vaga, haz preguntas de aclaración en lugar de adivinar. "
        "Nunca inventes contenido de libros — usa las herramientas disponibles para "
        "buscar y reproducir audiolibros reales."
    )

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = False

    # --- Dev mode ---
    dev_mode: bool = False  # Keyboard Enter triggers wake word; no hardware needed
