"""Entry point for AbuelOS: python -m abuel_os"""

from __future__ import annotations

import argparse
import asyncio
import sys

from abuel_os.config import Settings


def main() -> None:
    """Parse config and run the application."""
    parser = argparse.ArgumentParser(
        prog="abuel_os",
        description="AbuelOS — voice assistant for abuelo",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: press Enter to trigger wake word (no hardware needed)",
    )
    args = parser.parse_args()

    try:
        config = Settings(dev_mode=args.dev)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Set ABUEL_OPENAI_API_KEY or create a .env file.", file=sys.stderr)
        sys.exit(1)

    if not config.openai_api_key:
        print("Error: ABUEL_OPENAI_API_KEY is required.", file=sys.stderr)
        sys.exit(1)

    from abuel_os.app import Application

    app = Application(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
