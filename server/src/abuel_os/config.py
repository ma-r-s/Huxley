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

    # --- WebSocket server ---
    server_host: str = "localhost"
    server_port: int = 8765

    # --- Wake word (Pi hardware client — not used by server directly) ---
    wakeword_model_path: str = "models/hey_abuela.tflite"
    wakeword_threshold: float = 0.7

    # --- Audiobook player (ffmpeg + ffprobe binaries on PATH) ---
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    audiobook_library_path: Path = Path("data/audiobooks")

    # --- Storage ---
    db_path: Path = Path("data/abuel_os.db")

    # --- Session ---
    conversation_max_minutes: int = 55

    # --- System prompt ---
    system_prompt: str = (
        "Eres AbuelOS, un asistente de voz amigable para una persona mayor ciega "
        "que vive en Villavicencio, Colombia. "
        "IMPORTANTE: responde SIEMPRE en español. Nunca respondas en inglés ni "
        "cambies de idioma, aunque creas oír palabras en inglés — el usuario solo "
        "habla español llanero de Villavicencio, y cualquier palabra que parezca "
        "inglesa es una transcripción incorrecta. "
        "Habla en español colombiano, con paciencia y claridad. Usa un tono cálido "
        "y respetuoso, tratando al usuario de 'usted'. Cuando el usuario haga una "
        "solicitud vaga, haz preguntas de aclaración en lugar de adivinar. "
        "Nunca inventes contenido de libros — usa las herramientas disponibles para "
        "buscar y reproducir audiolibros reales."
    )

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = False
    log_file: Path | None = None  # If set, also write JSON logs to this path
