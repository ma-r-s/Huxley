"""The app around a voice-session gap (the planned refresh, an outage).

- A press while there is no session can't start a turn; the walkie shows
  no status text, so the press gets the reconnect loop's double beep.
- The coordinator hears when the session is up again
  (`on_session_connected`): content a press paused during the gap
  resumes there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from huxley.config import Settings
from huxley.reconnect import no_signal_tone_pcm
from huxley.runtime import Runtime
from huxley.state.machine import AppState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from huxley.app import Application


_PERSONA_YAML = """\
version: 1
name: p
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Test persona with no skills.
constraints: []
skills: {}
"""


@pytest.fixture
async def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Application]:
    persona_dir = tmp_path / "personas" / "p"
    persona_dir.mkdir(parents=True)
    (persona_dir / "persona.yaml").write_text(_PERSONA_YAML, encoding="utf-8")
    (persona_dir / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    runtime = Runtime(Settings(openai_api_key="test-key"))
    # No session: the app stays IDLE (the api key is bogus anyway).
    await runtime._switch_to_persona("p", auto_connect=False)
    current = runtime.current_app
    assert current is not None
    yield current
    await current.shutdown()


async def test_press_without_a_session_beeps(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert app.state_machine.state is AppState.IDLE
    send_audio = AsyncMock()
    monkeypatch.setattr(app.server, "send_audio", send_audio)
    monkeypatch.setattr(app.coordinator, "on_ptt_rejected", AsyncMock())

    await app.on_ptt_start()

    app.coordinator.on_ptt_rejected.assert_awaited_once()  # type: ignore[attr-defined]
    send_audio.assert_awaited_once_with(no_signal_tone_pcm())


async def test_a_connect_tells_the_coordinator(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.provider, "connect", AsyncMock())
    connected = AsyncMock()
    monkeypatch.setattr(app.coordinator, "on_session_connected", connected)

    await app.state_machine.trigger("wake_word")

    assert app.state_machine.state is AppState.CONVERSING
    connected.assert_awaited_once()


async def test_a_failed_connect_does_not(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.provider, "connect", AsyncMock(side_effect=OSError("no network")))
    connected = AsyncMock()
    monkeypatch.setattr(app.coordinator, "on_session_connected", connected)

    await app.state_machine.trigger("wake_word")

    assert app.state_machine.state is AppState.IDLE
    connected.assert_not_awaited()
