"""Tests for `huxley.installer.install_skill` (Marketplace v2 Phase D).

Pin the contract:
1. Validation: package name not matching `huxley-skill-<key>` returns
   `validation_failed` without spawning a subprocess.
2. Success: `uv add` exits 0 → `InstallResult(ok=True)`.
3. Package-not-found: characteristic stderr → `package_not_found` code.
4. Generic uv failure: non-zero exit + arbitrary stderr → `uv_failed`.
5. Timeout: subprocess exceeding `timeout_s` → `timeout`, subprocess
   killed.
6. uv binary missing: `FileNotFoundError` on spawn → `internal_error`.
7. The `started` event fires before the subprocess returns; `complete`
   is emitted by the runtime shim, not the installer (so this module
   does NOT emit `complete` events).

Subprocess is mocked via `asyncio.create_subprocess_exec` patches.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from huxley import installer
from huxley.installer import InstallEvent, InstallResult


class _FakeProc:
    """Mimics `asyncio.subprocess.Process` for the installer's
    `communicate()` + `kill()` calls."""

    def __init__(
        self,
        returncode: int,
        stdout: bytes = b"",
        stderr: bytes = b"",
        delay: float = 0.0,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._delay = delay
        self._killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self._killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    proc: _FakeProc,
) -> list[tuple]:
    """Patch `asyncio.create_subprocess_exec` to return `proc` and
    record the call args. Returns the calls list for assertions."""
    calls: list[tuple[Any, ...]] = []

    async def _stub(*args: Any, **kwargs: Any) -> _FakeProc:
        calls.append((args, kwargs))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _stub)
    return calls


# ── Validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_name",
    [
        "stocks",  # no prefix
        "huxley-skill-",  # empty key
        "huxley-skill-Stocks",  # uppercase
        "huxley-skill-foo_bar",  # underscore
        "huxley-skill-../evil",  # path traversal
        "huxley-skill-foo.bar",  # dot
        "",
    ],
)
async def test_validation_rejects_bad_names(
    bad_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names that don't match the regex never reach the subprocess."""
    calls = _patch_subprocess(monkeypatch, _FakeProc(returncode=0))
    result = await installer.install_skill(bad_name)
    assert isinstance(result, InstallResult)
    assert result.ok is False
    assert result.error_code == "validation_failed"
    assert calls == []  # subprocess never spawned


@pytest.mark.parametrize(
    "good_name",
    [
        "huxley-skill-stocks",
        "huxley-skill-foo",
        "huxley-skill-foo-bar",
        "huxley-skill-a1b2c3",
    ],
)
async def test_validation_accepts_canonical_names(
    good_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_subprocess(monkeypatch, _FakeProc(returncode=0))
    result = await installer.install_skill(good_name)
    assert result.ok is True


# ── Success / failure paths ─────────────────────────────────────────────


async def test_success_returns_ok_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_subprocess(monkeypatch, _FakeProc(returncode=0))
    result = await installer.install_skill("huxley-skill-stocks")
    assert result.ok is True
    assert result.package == "huxley-skill-stocks"
    assert result.error_code is None
    assert result.error_message is None
    # Confirm the subprocess command matches what the planning critic
    # locked: ["uv", "add", "huxley-skill-stocks"].
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[:3] == ("uv", "add", "huxley-skill-stocks")


async def test_package_not_found_returns_package_not_found_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(
        returncode=1,
        stderr=b"error: no matching distribution found for huxley-skill-foo\n",
    )
    _patch_subprocess(monkeypatch, proc)
    result = await installer.install_skill("huxley-skill-foo")
    assert result.ok is False
    assert result.error_code == "package_not_found"
    assert "no matching distribution" in (result.error_message or "")


async def test_generic_failure_returns_uv_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(
        returncode=1,
        stderr=b"error: failed to write lockfile (disk full?)\n",
    )
    _patch_subprocess(monkeypatch, proc)
    result = await installer.install_skill("huxley-skill-foo")
    assert result.ok is False
    assert result.error_code == "uv_failed"
    assert "lockfile" in (result.error_message or "")


async def test_timeout_kills_subprocess_and_returns_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(returncode=0, delay=10.0)  # would block 10s
    _patch_subprocess(monkeypatch, proc)
    result = await installer.install_skill("huxley-skill-foo", timeout_s=0.05)
    assert result.ok is False
    assert result.error_code == "timeout"
    # Subprocess kill was called.
    assert proc._killed is True


async def test_uv_not_on_path_returns_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raises(*args: Any, **kwargs: Any) -> _FakeProc:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raises)
    result = await installer.install_skill("huxley-skill-foo")
    assert result.ok is False
    assert result.error_code == "internal_error"
    assert "uv" in (result.error_message or "").lower()


# ── Event lifecycle ─────────────────────────────────────────────────────


async def test_started_event_fires_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`on_event` receives a `started` kind exactly once, BEFORE the
    subprocess returns. The runtime shim emits `complete` itself
    (since it knows `restart_required`); the installer does not."""
    events: list[InstallEvent] = []

    async def _capture(ev: InstallEvent) -> None:
        events.append(ev)

    _patch_subprocess(monkeypatch, _FakeProc(returncode=0))
    await installer.install_skill("huxley-skill-foo", on_event=_capture)
    assert len(events) == 1
    assert events[0].kind == "started"
    assert events[0].package == "huxley-skill-foo"


async def test_started_event_not_fired_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the package name fails validation, no event fires — saves
    the PWA from a flicker of "Installing…" before the error frame."""
    events: list[InstallEvent] = []

    async def _capture(ev: InstallEvent) -> None:
        events.append(ev)

    await installer.install_skill("invalid-name", on_event=_capture)
    assert events == []


# ── Critic's recommended single highest-ROI integration test ────────────
# (Phase D planning critic §D.) Combines: validation, subprocess
# command shape, ok-path return value, and event ordering. If a future
# refactor breaks any of them, this single test fails loudly.


async def test_full_happy_path_critic_recommended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(
        returncode=0,
        stdout=b" + huxley-skill-stocks==0.1.0\n",
    )
    calls = _patch_subprocess(monkeypatch, proc)

    events: list[InstallEvent] = []

    async def _capture(ev: InstallEvent) -> None:
        events.append(ev)

    result = await installer.install_skill(
        "huxley-skill-stocks",
        on_event=_capture,
        cwd="/some/path",
    )

    # Result shape.
    assert result.ok is True
    assert result.package == "huxley-skill-stocks"
    assert result.error_code is None

    # Subprocess invocation.
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:3] == ("uv", "add", "huxley-skill-stocks")
    assert kwargs.get("cwd") == "/some/path"

    # Event sequence: exactly one `started` event from the installer.
    assert [e.kind for e in events] == ["started"]
