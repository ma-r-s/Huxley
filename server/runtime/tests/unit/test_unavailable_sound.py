"""The persona's "unavailable" sound (T1.43): what the user hears when
OpenAI won't serve.

Before, a press with no session got the double beep whatever the cause,
and a turn OpenAI refused (no credit, a spend limit) hung in silence. Now:

- The clip (`unavailable_sound` in persona.yaml) is read once at app
  start; missing or in the wrong format, the double beep stays.
- Path A, a press with no session: the short beep while held; at the
  release, the clip once the outage has lasted 60 s or the provider
  refused the account (nothing more for a short gap).
- Path B, a refused turn: the coordinator ends it; a user turn gets the
  clip (or the beep) and the next press works, a proactive turn gets no
  sound and waits at the head of the queue.
"""

from __future__ import annotations

import time
import wave
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import structlog

from huxley import app as app_module
from huxley.config import Settings
from huxley.focus.manager import FocusManager
from huxley.focus.vocabulary import Channel
from huxley.reconnect import no_signal_tone_pcm
from huxley.runtime import Runtime
from huxley.state.machine import AppState
from huxley.turn.coordinator import TurnCoordinator, TurnState
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine
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


def _write_wav(
    path: Path, *, frames: int = 2400, channels: int = 1, width: int = 2, rate: int = 24_000
) -> bytes:
    """A WAV of a constant non-zero sample; returns its PCM frames."""
    pcm = (b"\x10\x00" * channels * (width // 2) if width >= 2 else b"\x80" * channels) * frames
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return pcm


# --- the loader --------------------------------------------------------------


@pytest.fixture
async def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[[str | None], Coroutine[Any, Any, Application]]]:
    """Start a persona `p` (IDLE, no session) whose persona.yaml carries
    `unavailable_sound: <value>` when given. Shut down after the test."""
    persona_dir = tmp_path / "personas" / "p"
    persona_dir.mkdir(parents=True)
    (persona_dir / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    started: list[Application] = []

    async def _make(unavailable_sound: str | None) -> Application:
        yaml = _PERSONA_YAML
        if unavailable_sound is not None:
            yaml += f"unavailable_sound: {unavailable_sound}\n"
        (persona_dir / "persona.yaml").write_text(yaml, encoding="utf-8")
        runtime = Runtime(Settings(openai_api_key="test-key"))
        await runtime._switch_to_persona("p", auto_connect=False)
        current = runtime.current_app
        assert current is not None
        started.append(current)
        return current

    yield _make
    for application in started:
        await application.shutdown()


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == name]


async def test_a_valid_clip_is_loaded_once_at_start(
    tmp_path: Path, make_app: Callable[[str | None], Coroutine[Any, Any, Application]]
) -> None:
    pcm = _write_wav(tmp_path / "personas" / "p" / "sounds" / "unavailable.wav")

    with structlog.testing.capture_logs() as logs:
        application = await make_app("sounds/unavailable.wav")

    assert application._unavailable_sound == pcm
    assert application.coordinator._unavailable_sound == pcm
    (loaded,) = _events(logs, "app.unavailable_sound_loaded")
    assert loaded["ms"] == 100  # 2400 frames at 24 kHz
    assert _events(logs, "app.unavailable_sound_invalid") == []


async def test_a_missing_clip_keeps_the_beep(
    make_app: Callable[[str | None], Coroutine[Any, Any, Application]],
) -> None:
    """abuelos ships the key before its WAV lands: it must boot and beep."""
    with structlog.testing.capture_logs() as logs:
        application = await make_app("sounds/unavailable.wav")

    assert application._unavailable_sound is None
    assert application.coordinator._unavailable_sound is None
    (invalid,) = _events(logs, "app.unavailable_sound_invalid")
    assert invalid["reason"] == "missing"


@pytest.mark.parametrize(
    ("fmt", "why"),
    [
        ({"channels": 2}, "2 channel(s)"),
        ({"rate": 44_100}, "44100 Hz"),
        ({"width": 1}, "8-bit"),
    ],
    ids=["stereo", "44k1", "8bit"],
)
async def test_a_clip_in_the_wrong_format_keeps_the_beep(
    tmp_path: Path,
    make_app: Callable[[str | None], Coroutine[Any, Any, Application]],
    fmt: dict[str, int],
    why: str,
) -> None:
    _write_wav(tmp_path / "personas" / "p" / "bad.wav", **fmt)

    with structlog.testing.capture_logs() as logs:
        application = await make_app("bad.wav")

    assert application._unavailable_sound is None
    (invalid,) = _events(logs, "app.unavailable_sound_invalid")
    assert invalid["reason"].startswith("format:")
    assert why in invalid["reason"]


def test_a_file_that_is_not_a_wav_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "clip.wav"
    path.write_bytes(b"ID3 this is an mp3, not a wav")
    with pytest.raises(ValueError, match="not a PCM WAV"):
        app_module._read_wire_wav(path)


def test_an_empty_wav_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "clip.wav"
    _write_wav(path, frames=0)
    with pytest.raises(ValueError, match="empty"):
        app_module._read_wire_wav(path)


async def test_no_key_no_clip_and_no_log(
    make_app: Callable[[str | None], Coroutine[Any, Any, Application]],
) -> None:
    with structlog.testing.capture_logs() as logs:
        application = await make_app(None)

    assert application._unavailable_sound is None
    assert [e for e in logs if e["event"].startswith("app.unavailable_sound")] == []


# --- path A: a press with no session ------------------------------------------
# The press itself always gets the short double beep (the button is held,
# the user is talking). In a real outage the release then plays the clip:
# the walkie answers after release.


@pytest.fixture
async def clip_app(
    tmp_path: Path, make_app: Callable[[str | None], Coroutine[Any, Any, Application]]
) -> tuple[Application, bytes]:
    pcm = _write_wav(tmp_path / "personas" / "p" / "sounds" / "unavailable.wav")
    return await make_app("sounds/unavailable.wav"), pcm


def _down_for(application: Application, seconds: float) -> None:
    application.coordinator._session_down_since = time.monotonic() - seconds


class _Heard:
    """What a press-and-release with no session sent, and logged."""

    def __init__(self, sent: list[bytes], logs: list[dict[str, Any]]) -> None:
        self.sent = sent
        self.logs = logs

    def event(self, name: str) -> dict[str, Any]:
        (found,) = _events(self.logs, name)
        return found


async def _press_and_release(application: Application, monkeypatch: pytest.MonkeyPatch) -> _Heard:
    assert application.state_machine.state is AppState.IDLE
    send_audio = AsyncMock()
    monkeypatch.setattr(application.server, "send_audio", send_audio)
    with structlog.testing.capture_logs() as logs:
        await application.on_ptt_start()
        await application.on_ptt_stop()
    return _Heard([c.args[0] for c in send_audio.await_args_list], logs)


_BEEP = no_signal_tone_pcm()


async def test_a_short_gap_only_beeps(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planned refresh reconnects in 2-3 s: a press then must not talk."""
    application, _ = clip_app
    _down_for(application, 3.0)

    heard = await _press_and_release(application, monkeypatch)

    assert heard.sent == [_BEEP]
    beep = heard.event("app.ptt_rejected_beep")
    assert beep["reason"] == "short_gap"
    assert 2.5 < beep["down_s"] < 10
    assert _events(heard.logs, "app.unavailable_played") == []


async def test_a_long_outage_beeps_then_plays_the_clip_on_release(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, pcm = clip_app
    _down_for(application, 61.0)
    send_audio = AsyncMock()
    monkeypatch.setattr(application.server, "send_audio", send_audio)

    await application.on_ptt_start()
    # Held: only the beep so far; a clip now would be talked over.
    assert [c.args[0] for c in send_audio.await_args_list] == [_BEEP]
    with structlog.testing.capture_logs() as logs:
        await application.on_ptt_stop()

    assert [c.args[0] for c in send_audio.await_args_list] == [_BEEP, pcm]
    (played,) = _events(logs, "app.unavailable_played")
    assert played["reason"] == "down_long"
    assert played["down_s"] >= 61


async def test_a_long_outage_without_a_clip_only_beeps(
    make_app: Callable[[str | None], Coroutine[Any, Any, Application]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = await make_app(None)
    _down_for(application, 120.0)

    heard = await _press_and_release(application, monkeypatch)

    assert heard.sent == [_BEEP]
    beep = heard.event("app.ptt_rejected_beep")
    assert beep["reason"] == "no_clip"
    assert beep["unavailable"] == "down_long"


async def test_a_billing_refusal_on_connect_plays_the_clip_at_once(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No credit: no point beeping "retrying" for a minute first."""
    application, pcm = clip_app
    refused = RuntimeError("OpenAI refused the session: insufficient_quota (HTTP 429)")
    monkeypatch.setattr(application.provider, "connect", AsyncMock(side_effect=refused))
    monkeypatch.setattr(application, "_schedule_reconnect", AsyncMock())
    _down_for(application, 0.5)

    with structlog.testing.capture_logs() as logs:
        await application.state_machine.trigger("wake_word")
    (failed,) = _events(logs, "connection_failed")
    assert failed["refused"] == "insufficient_quota"

    heard = await _press_and_release(application, monkeypatch)

    assert heard.sent == [_BEEP, pcm]
    assert heard.event("app.ptt_rejected_beep")["reason"] == "clip_on_release"
    assert heard.event("app.unavailable_played")["reason"] == "refused:insufficient_quota"


async def test_a_refusal_in_the_exception_chain_counts(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code may sit in a chained exception, as the traceback shows it."""
    application, pcm = clip_app

    def _chained() -> Exception:
        try:
            try:
                raise ValueError("error: project_spend_limit_exceeded")
            except ValueError as inner:
                raise OSError("connect failed") from inner
        except OSError as outer:
            return outer
        raise AssertionError  # pragma: no cover

    monkeypatch.setattr(application.provider, "connect", AsyncMock(side_effect=_chained()))
    monkeypatch.setattr(application, "_schedule_reconnect", AsyncMock())
    await application.state_machine.trigger("wake_word")

    heard = await _press_and_release(application, monkeypatch)

    assert heard.sent == [_BEEP, pcm]


async def test_a_network_failure_is_not_a_refusal(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, _ = clip_app
    monkeypatch.setattr(
        application.provider, "connect", AsyncMock(side_effect=OSError("no network"))
    )
    monkeypatch.setattr(application, "_schedule_reconnect", AsyncMock())
    _down_for(application, 0.5)
    await application.state_machine.trigger("wake_word")

    heard = await _press_and_release(application, monkeypatch)

    assert heard.sent == [_BEEP]


async def test_a_session_back_during_the_hold_skips_the_clip(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, _ = clip_app
    _down_for(application, 61.0)
    send_audio = AsyncMock()
    monkeypatch.setattr(application.server, "send_audio", send_audio)
    monkeypatch.setattr(application.provider, "connect", AsyncMock())
    monkeypatch.setattr(application.coordinator, "on_session_connected", AsyncMock())
    coord_stop = AsyncMock()
    monkeypatch.setattr(application.coordinator, "on_ptt_stop", coord_stop)

    await application.on_ptt_start()
    await application.state_machine.trigger("wake_word")  # back while held
    with structlog.testing.capture_logs() as logs:
        await application.on_ptt_stop()

    assert [c.args[0] for c in send_audio.await_args_list] == [_BEEP]
    (skipped,) = _events(logs, "app.unavailable_skipped")
    assert skipped["why"] == "session_back"
    coord_stop.assert_awaited_once()


async def test_a_connect_does_not_clear_the_refusal_a_reply_does(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI can accept the session and refuse every answer: only a
    completed reply proves it serves again."""
    application, _ = clip_app
    application._refusal_code = "insufficient_quota"
    monkeypatch.setattr(application.provider, "connect", AsyncMock())
    monkeypatch.setattr(application.coordinator, "on_session_connected", AsyncMock())

    await application.state_machine.trigger("wake_word")

    assert application.state_machine.state is AppState.CONVERSING
    assert application._refusal_code == "insufficient_quota"

    coord_done = AsyncMock()
    monkeypatch.setattr(application.coordinator, "on_response_done", coord_done)
    with structlog.testing.capture_logs() as logs:
        await application.provider._cb.on_response_done()

    assert application._refusal_code is None
    coord_done.assert_awaited_once()
    (cleared,) = _events(logs, "app.refusal_cleared")
    assert cleared["code"] == "insufficient_quota"


async def test_a_refused_turn_marks_the_refusal_for_the_next_press(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path B feeds path A: once a turn was refused, a press after the
    session drops gets the clip at once, not after a minute of beeps."""
    application, pcm = clip_app
    on_refused = AsyncMock()
    monkeypatch.setattr(application.coordinator, "on_response_refused", on_refused)
    callback = application.provider._cb.on_response_refused
    assert callback is not None

    await callback("organization_spend_limit_exceeded")

    on_refused.assert_awaited_once_with("organization_spend_limit_exceeded")
    _down_for(application, 1.0)
    heard = await _press_and_release(application, monkeypatch)
    assert heard.sent == [_BEEP, pcm]
    played = heard.event("app.unavailable_played")
    assert played["reason"] == "refused:organization_spend_limit_exceeded"


async def test_a_dying_app_ignores_a_refusal(
    clip_app: tuple[Application, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a persona swap the old app must not speak on the shared server."""
    application, _ = clip_app
    on_refused = AsyncMock()
    monkeypatch.setattr(application.coordinator, "on_response_refused", on_refused)
    application._shutting_down = True
    try:
        await application._on_response_refused("insufficient_quota")
    finally:
        application._shutting_down = False

    on_refused.assert_not_awaited()
    assert application._refusal_code is None


async def test_the_clip_resolves_against_the_persona_dir_not_a_symlinked_data_dir(
    tmp_path: Path,
    make_app: Callable[[str | None], Coroutine[Any, Any, Application]],
) -> None:
    """`data/` may be a symlink to another disk; the clip lives beside
    persona.yaml."""
    persona_dir = tmp_path / "personas" / "p"
    elsewhere = tmp_path / "disk" / "data"
    elsewhere.mkdir(parents=True)
    (persona_dir / "data").rmdir()
    (persona_dir / "data").symlink_to(elsewhere)
    pcm = _write_wav(persona_dir / "sounds" / "unavailable.wav")

    application = await make_app("sounds/unavailable.wav")

    assert application._unavailable_sound == pcm


# --- path B: a refused turn ----------------------------------------------------


_CLIP = b"\x22\x00" * 4800  # 200 ms


@pytest.fixture
def provider() -> StubVoiceProvider:
    p = StubVoiceProvider()
    p._connected = True
    return p


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


@pytest.fixture
def mocks(provider: StubVoiceProvider) -> dict[str, Any]:
    return {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "provider": provider,
        "dispatch_tool": AsyncMock(return_value=ToolResult(output="{}")),
    }


def _coordinator(
    mocks: dict[str, Any], focus_manager: FocusManager, sound: bytes | None
) -> TurnCoordinator:
    coordinator = TurnCoordinator(**mocks, focus_manager=focus_manager, unavailable_sound=sound)
    coordinator._session_down_since = None  # the stub is connected
    return coordinator


async def _commit(coordinator: TurnCoordinator) -> None:
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_bytes = 300 * 48  # 300 ms
    await coordinator.on_ptt_stop()
    assert coordinator.current_turn is not None
    assert coordinator.current_turn.state is TurnState.COMMITTING


async def test_a_refused_turn_ends_with_the_clip_and_the_next_press_works(
    mocks: dict[str, Any], focus_manager: FocusManager, provider: StubVoiceProvider
) -> None:
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await _commit(coordinator)
    assert coordinator._wait_cue.armed
    mocks["send_audio_clear"].reset_mock()

    await coordinator.on_response_refused("insufficient_quota")

    assert coordinator.current_turn is None
    assert not coordinator._wait_cue.armed
    mocks["send_audio_clear"].assert_awaited_once()
    mocks["send_audio"].assert_awaited_once_with(_CLIP)
    # A late response.done of the refused turn can't end the next one.
    assert provider.sent[-1] == ("cancel_current_response",)

    provider.sent.clear()
    await _commit(coordinator)
    assert provider.sent == [("commit_and_request_response",)]


async def test_a_refused_turn_without_a_clip_beeps(
    mocks: dict[str, Any], focus_manager: FocusManager
) -> None:
    coordinator = _coordinator(mocks, focus_manager, None)
    await _commit(coordinator)

    await coordinator.on_response_refused("billing_hard_limit_reached")

    assert coordinator.current_turn is None
    mocks["send_audio"].assert_awaited_once_with(no_signal_tone_pcm())


async def test_a_refusal_mid_answer_clears_the_speaker_flag(
    mocks: dict[str, Any], focus_manager: FocusManager
) -> None:
    """The model had started speaking (a chained turn): the walkie drops
    audio while model_speaking is true, so the flag must drop before the
    clip goes out."""
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await _commit(coordinator)
    await coordinator.on_audio_delta(b"\x01\x00" * 480)
    assert coordinator._speaking_state.is_speaking
    order: list[str] = []
    mocks["send_model_speaking"].side_effect = lambda v: order.append(f"speaking={v}")
    mocks["send_audio"].side_effect = lambda pcm: order.append("clip" if pcm == _CLIP else "audio")

    await coordinator.on_response_refused("insufficient_quota")

    assert order == ["speaking=False", "clip"]


async def test_a_late_refusal_leaves_a_new_press_alone(
    mocks: dict[str, Any], focus_manager: FocusManager
) -> None:
    """A press that is still LISTENING asked for nothing: a refusal
    arriving now belongs to the turn it replaced."""
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await coordinator.on_ptt_start()
    turn = coordinator.current_turn

    await coordinator.on_response_refused("insufficient_quota")

    assert coordinator.current_turn is turn
    mocks["send_audio"].assert_not_awaited()


async def test_a_refusal_with_no_turn_does_nothing(
    mocks: dict[str, Any], focus_manager: FocusManager
) -> None:
    coordinator = _coordinator(mocks, focus_manager, _CLIP)

    await coordinator.on_response_refused("insufficient_quota")

    mocks["send_audio"].assert_not_awaited()
    mocks["send_audio_clear"].assert_not_awaited()


async def test_a_refused_reminder_is_silent_and_waits_at_the_head_of_the_queue(
    mocks: dict[str, Any], focus_manager: FocusManager, provider: StubVoiceProvider
) -> None:
    """Nobody pressed: a reminder (or a timer at 3 a.m.) refused must not
    wake the user to say "unavailable". It goes back to the head of the
    queue, its attempt given back, DIALOG released so what it parked comes
    back; no drain fires it straight into the same refusal."""
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await coordinator.inject_turn("Recuerda la pastilla", dedup_key="pill")
    assert coordinator.current_turn is not None
    assert coordinator._dialog_interface_name is not None
    assert focus_manager._stacks[Channel.DIALOG]
    provider.sent.clear()

    await coordinator.on_response_refused("insufficient_quota")
    await focus_manager.wait_drained()

    assert coordinator.current_turn is None
    mocks["send_audio"].assert_not_awaited()
    mocks["send_audio_clear"].assert_not_awaited()  # an earlier answer's tail plays on
    (request,) = coordinator._injected_queue
    assert request.dedup_key == "pill"
    assert request.attempts == 0
    assert coordinator._inflight is None
    assert coordinator._dialog_interface_name is None
    assert focus_manager._stacks[Channel.DIALOG] == []
    assert ("request_response",) not in provider.sent


async def test_a_refused_reminder_retried_later_has_no_repeat_note(
    mocks: dict[str, Any], focus_manager: FocusManager, provider: StubVoiceProvider
) -> None:
    """The next drain (here another skill's inject) retries it. Nothing was
    said the first time, so it isn't introduced as "say it again"."""
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await coordinator.inject_turn("Recuerda la pastilla", dedup_key="pill")
    await coordinator.on_response_refused("insufficient_quota")
    provider.sent.clear()

    await coordinator.inject_turn("Tu temporizador", dedup_key="timer")

    messages = [m[1] for m in provider.sent if m[0] == "send_conversation_message"]
    assert messages == ["Recuerda la pastilla"]
    assert [r.dedup_key for r in coordinator._injected_queue] == ["timer"]


async def test_a_press_during_the_cancel_takes_over_and_no_sound_plays(
    mocks: dict[str, Any], focus_manager: FocusManager, provider: StubVoiceProvider
) -> None:
    """The press interrupts the refused turn itself; the user is talking,
    so nothing may play into the open mic, and the press's turn stays."""
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await _commit(coordinator)
    stub_cancel = provider.cancel_current_response
    pressed = False

    async def cancel_then_press() -> None:
        nonlocal pressed
        await stub_cancel()
        if not pressed:
            pressed = True
            await coordinator.on_ptt_start()

    provider.cancel_current_response = cancel_then_press  # type: ignore[method-assign]

    await coordinator.on_response_refused("insufficient_quota")

    assert pressed
    assert coordinator.current_turn is not None
    assert coordinator.current_turn.state is TurnState.LISTENING
    mocks["send_audio"].assert_not_awaited()


async def test_a_press_after_the_detach_keeps_its_turn_and_gets_no_sound(
    mocks: dict[str, Any], focus_manager: FocusManager
) -> None:
    """Before, the refusal's trailing `current_turn = None` ended the new
    press's turn, and the clip went out while the button was held."""
    coordinator = _coordinator(mocks, focus_manager, _CLIP)
    await _commit(coordinator)
    pressed = False

    async def clear_then_press() -> None:
        nonlocal pressed
        if not pressed:
            pressed = True
            await coordinator.on_ptt_start()

    mocks["send_audio_clear"].side_effect = clear_then_press
    mocks["send_status"].reset_mock()

    await coordinator.on_response_refused("insufficient_quota")

    assert pressed
    assert coordinator.current_turn is not None
    assert coordinator.current_turn.state is TurnState.LISTENING
    mocks["send_audio"].assert_not_awaited()
    statuses = [c.args[0] for c in mocks["send_status"].await_args_list]
    assert statuses[-1] == coordinator._status["listening"]
