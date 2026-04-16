"""Entry point for Huxley: python -m huxley"""

from __future__ import annotations

import asyncio
import sys

from huxley.config import Settings


def main() -> None:
    """Parse config and run the application."""
    try:
        config = Settings()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Set HUXLEY_OPENAI_API_KEY or create a .env file.", file=sys.stderr)
        sys.exit(1)

    if not config.openai_api_key:
        print("Error: HUXLEY_OPENAI_API_KEY is required.", file=sys.stderr)
        sys.exit(1)

    from huxley.app import Application

    app = Application(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
