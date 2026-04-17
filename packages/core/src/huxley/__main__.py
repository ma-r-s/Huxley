"""Entry point for Huxley: `python -m huxley` or the `huxley` script."""

from __future__ import annotations

import asyncio
import sys

from huxley.config import Settings
from huxley.persona import PersonaError, load_persona, resolve_persona_path


def main() -> None:
    """Parse config, load persona, run the application."""
    try:
        config = Settings()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Set HUXLEY_OPENAI_API_KEY or create a .env file.", file=sys.stderr)
        sys.exit(1)

    if not config.openai_api_key:
        print("Error: HUXLEY_OPENAI_API_KEY is required.", file=sys.stderr)
        sys.exit(1)

    persona_path = resolve_persona_path(env_name=config.persona)
    try:
        persona = load_persona(persona_path)
    except PersonaError as e:
        print(f"Persona error: {e}", file=sys.stderr)
        print(
            "Set HUXLEY_PERSONA to a persona name under ./personas/, "
            "or run from a directory with ./personas/abuelos/persona.yaml.",
            file=sys.stderr,
        )
        sys.exit(1)

    from huxley.app import Application

    app = Application(config, persona)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
