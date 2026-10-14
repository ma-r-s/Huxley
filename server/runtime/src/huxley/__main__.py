"""Entry point for Huxley: `python -m huxley` or the `huxley` script."""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import io
import sys

from dotenv import find_dotenv, load_dotenv

from huxley.config import Settings
from huxley.persona import PersonaError


def main() -> None:
    """Parse config, build the Runtime, run."""
    # A crash in native code (the Telegram call library segfaulted placing
    # a call on 2026-10-14: exit 139, nothing else in the journal) prints
    # every thread's Python stack to stderr, which systemd keeps. A stderr
    # with no file behind it (a captured test) has nowhere to write.
    with contextlib.suppress(io.UnsupportedOperation, ValueError):
        faulthandler.enable(all_threads=True)
    # Load .env into os.environ BEFORE constructing Settings. pydantic-settings
    # reads .env too, but only for fields declared on the model — skill
    # secrets like HUXLEY_TELEGRAM_API_ID aren't framework config and
    # shouldn't be; they need to land in os.environ so skill setup code
    # can read them.
    #
    # `usecwd=True` is load-bearing: the bare `load_dotenv()` walks up from
    # THIS FILE's directory, so in a source checkout every instance loaded
    # the repo's `server/runtime/.env` (its persona, its API key) no matter
    # which directory it was started from. That silently broke the
    # one-directory-per-human multi-instance layout in docs/architecture.md.
    load_dotenv(find_dotenv(usecwd=True))

    try:
        config = Settings()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Set HUXLEY_OPENAI_API_KEY or create a .env file.", file=sys.stderr)
        sys.exit(1)

    if not config.openai_api_key:
        print("Error: HUXLEY_OPENAI_API_KEY is required.", file=sys.stderr)
        sys.exit(1)

    # Lazy import so configuration errors above surface quickly without
    # paying the framework's import cost.
    from huxley.instance_lock import InstanceLockTimeoutError
    from huxley.runtime import Runtime

    runtime = Runtime(config)
    try:
        asyncio.run(runtime.run())
    except InstanceLockTimeoutError as e:
        # Exit non-zero so launchd's KeepAlive tries again later.
        print(f"[huxley] not starting: {e}", file=sys.stderr)
        sys.exit(1)
    except PersonaError as e:
        print(f"Persona error: {e}", file=sys.stderr)
        print(
            "Place a persona dir under ./personas/<name>/persona.yaml, "
            "set HUXLEY_PERSONA to a name under ./personas/, or run "
            "from a directory that contains ./personas/.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
