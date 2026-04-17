"""Entry point for Huxley: `python -m huxley` or the `huxley` script."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from huxley.config import Settings


def _cwd_help() -> str:
    """Suggest the right CWD when paths/env aren't resolving."""
    cwd = Path.cwd()
    if (cwd / ".env").exists() or cwd.name == "core":
        return ""  # we're already in packages/core/, no hint needed
    return (
        "\nHint: run from packages/core/ where .env and data/ live.\n"
        f"  cd {cwd / 'packages' / 'core'} && uv run huxley\n"
        "(Path resolution becomes CWD-independent in stage 4 of the refactor.)"
    )


def main() -> None:
    """Parse config and run the application."""
    try:
        config = Settings()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Set HUXLEY_OPENAI_API_KEY or create a .env file.", file=sys.stderr)
        print(_cwd_help(), file=sys.stderr)
        sys.exit(1)

    if not config.openai_api_key:
        print("Error: HUXLEY_OPENAI_API_KEY is required.", file=sys.stderr)
        print(_cwd_help(), file=sys.stderr)
        sys.exit(1)

    from huxley.app import Application

    app = Application(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
