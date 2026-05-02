"""Skill installer (Marketplace v2 Phase D).

Wraps `uv add huxley-skill-<name>` as an async subprocess, validates the
package name, captures output for the server log, and returns a
structured result. The runtime's `_shim_install_skill` orchestrates
this + a follow-up `_perform_restart()` so the new entry point is
visible to the freshly-execed Python.

**Scope cuts** (Phase D planning critic §C):

- No streaming progress to the PWA. The subprocess output goes to
  the server log; the PWA shows a simple "Installing… (this may
  take a minute)" spinner. Re-add streaming if anyone asks.
- No `importlib.invalidate_caches()`. We `os.execv` post-success;
  the new process discovers entry points fresh.
- No registry-cache validation gate. The regex + PyPI namespace +
  curated registry are the trust chain. Adding a "must be in last
  fetch" check creates the cache-staleness foot-gun.

**Locked contracts** (Phase D planning critic §E):

- Dataclasses, not tuples. Future fields (`bytes_downloaded`,
  `phase`, etc.) slot in without breaking callers.
- `error_code` (machine-readable) is separate from `error_message`
  (human-readable). PWA branches on the code; the message lands
  in the UI / log.
- `install_skill` is async. Caller awaits the subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()


# Tightened from the initial sketch (Phase D planning critic §A9):
# matches PyPI's normalized hyphenated package names; rejects
# leading/trailing hyphens, underscores, dots, and any path-like
# weirdness. PyPI permits more characters in raw names but this is
# the surface huxley-skill-* installs go through, so locking the
# convention here is the simpler trust boundary.
_PACKAGE_NAME_RE = re.compile(r"^huxley-skill-[a-z0-9][a-z0-9-]*$")

# Default subprocess timeout. C-extension wheels on a Pi can take
# 60-90s; 180s gives 2x headroom. Configurable per-call so future
# heavy installs can extend.
DEFAULT_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class InstallResult:
    """Outcome of a single install_skill call. Locked Phase D §E.3.

    `error_code` is the machine-readable branch the PWA / runtime
    uses; `error_message` is the human string for the UI / log.
    Both are None on success.
    """

    ok: bool
    package: str
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class InstallEvent:
    """Lifecycle marker for the install operation. v1 emits `started`
    once and `complete` once (kind="complete" carries the ok flag);
    v2 may add `queued`, `rolled_back`, etc.

    The on_event callback is optional — Phase D's wire flow uses it
    to push a `started` frame to the PWA before the subprocess kicks
    off, so the UI can flip to a spinner immediately. The `complete`
    case is sent by the runtime shim AFTER the subprocess returns,
    not by the installer itself, because the shim has additional
    state (`restart_required`) the installer doesn't know about.
    """

    kind: str  # "started" | "complete"
    package: str


async def install_skill(
    package_name: str,
    *,
    on_event: Callable[[InstallEvent], Awaitable[None]] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cwd: str | None = None,
) -> InstallResult:
    """Run `uv add <package_name>` as a subprocess.

    Returns an `InstallResult`. Failure modes (`error_code`):

    - ``"validation_failed"``: package name doesn't match the
      `huxley-skill-*` convention. No subprocess is spawned.
    - ``"package_not_found"``: uv add failed because the package
      doesn't exist on PyPI (or the resolver couldn't find a matching
      version). Subprocess returncode 1 with characteristic stderr.
    - ``"timeout"``: subprocess exceeded `timeout_s`.
    - ``"uv_failed"``: subprocess returncode != 0 for any other
      reason (build error, lock conflict, disk full, etc.). The
      stderr tail goes in `error_message`.
    - ``"internal_error"``: spawn failed before the subprocess could
      run (uv binary missing, etc.).

    `cwd` defaults to the calling process's cwd (which for Huxley is
    `server/runtime/` per how `uv run huxley` is launched). The
    workspace's `pyproject.toml` resolution finds the right one
    (verified Phase D planning critic §A5).
    """
    if not _PACKAGE_NAME_RE.match(package_name):
        await logger.awarning(
            "installer.validation_failed",
            package=package_name,
        )
        return InstallResult(
            ok=False,
            package=package_name,
            error_code="validation_failed",
            error_message=(
                f"Package name {package_name!r} does not match the "
                "expected huxley-skill-* pattern."
            ),
        )

    if on_event is not None:
        await on_event(InstallEvent(kind="started", package=package_name))

    await logger.ainfo(
        "installer.subprocess_starting",
        package=package_name,
        timeout_s=timeout_s,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "uv",
            "add",
            package_name,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        await logger.aexception(
            "installer.uv_not_on_path",
            package=package_name,
        )
        return InstallResult(
            ok=False,
            package=package_name,
            error_code="internal_error",
            error_message="uv binary not found on PATH.",
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_s,
        )
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(BaseException):
            await proc.wait()
        await logger.aerror(
            "installer.timeout",
            package=package_name,
            timeout_s=timeout_s,
        )
        return InstallResult(
            ok=False,
            package=package_name,
            error_code="timeout",
            error_message=(f"`uv add {package_name}` did not finish within {timeout_s:.0f}s."),
        )

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode == 0:
        await logger.ainfo(
            "installer.subprocess_success",
            package=package_name,
            stdout_tail=stdout[-500:],
        )
        return InstallResult(ok=True, package=package_name)

    # Classify the failure. uv's stderr signals are stable enough
    # for "package not found" (the most common user-error case);
    # everything else is uv_failed with the stderr tail attached.
    error_code = (
        "package_not_found"
        if "no matching distribution" in stderr.lower()
        or "could not find a version" in stderr.lower()
        or "not found" in stderr.lower()
        else "uv_failed"
    )
    await logger.aerror(
        "installer.subprocess_failed",
        package=package_name,
        returncode=proc.returncode,
        error_code=error_code,
        stderr_tail=stderr[-500:],
    )
    return InstallResult(
        ok=False,
        package=package_name,
        error_code=error_code,
        error_message=stderr.strip()[-500:] or "uv add failed (no stderr).",
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "InstallEvent",
    "InstallResult",
    "install_skill",
]
