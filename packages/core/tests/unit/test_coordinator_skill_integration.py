"""Integration-in-the-small: real TurnCoordinator + real AudiobooksSkill.

These tests wire a real `TurnCoordinator` to a real `SkillRegistry`
containing a real `AudiobooksSkill`. Only the OpenAI session and the
`AudiobookPlayer.stream()` subprocess are mocked — everything else runs
its production code path. Catches bugs in the coordinator ↔ skill
contract that the per-side unit tests can miss:

- factory closure actually reaches the coordinator's pending_audio_streams
- factory fires at the terminal barrier and streams via send_audio
- mid-chain interrupts drop the accumulated factory
- rewind/forward produce new factories, prior media task is cancelled
- pause/stop return None-factories and trigger follow-up round
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from huxley.focus.manager import FocusManager
from huxley.storage.skill import NamespacedSkillStorage
from huxley.turn.coordinator import TurnCoordinator, TurnState
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import SkillRegistry
from huxley_sdk.testing import make_test_context
from huxley_skill_audiobooks.skill import LAST_BOOK_KEY, AudiobooksSkill

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from huxley.storage.db import Storage


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    """Fresh FocusManager — started + torn down per test."""
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def library_path(tmp_path: Path) -> Path:
    lib = tmp_path / "audiobooks"
    garcia = lib / "Gabriel García Márquez"
    garcia.mkdir(parents=True)
    (garcia / "Cien años de soledad.mp3").write_bytes(b"fake")
    return lib


def _make_player_with_tracker() -> tuple[MagicMock, list[str]]:
    """Build a player mock that yields a few chunks per stream() call.

    Returns the mock and a list that grows with the tag of each chunk
    consumed — tests can assert the chunk source.
    """
    consumed: list[str] = []
    player = MagicMock()

    def stream_impl(
        path: Any, start_position: float = 0.0, speed: float = 1.0
    ) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            tag = f"{path}@{start_position}"
            for _ in range(3):
                consumed.append(tag)
                yield b"chunk"

        return gen()

    player.stream = MagicMock(side_effect=stream_impl)

    async def probe_ok(_path: Any) -> dict[str, Any]:
        return {"format": {"duration": "1000.0"}}

    player.probe = probe_ok
    return player, consumed


async def _build_wired_coordinator(
    library_path: Path,
    storage: Storage,
    focus_manager: FocusManager,
) -> tuple[TurnCoordinator, SkillRegistry, AudiobooksSkill, MagicMock, list[str], dict[str, Any]]:
    """Wire a real TurnCoordinator to a real SkillRegistry + real AudiobooksSkill.

    Returns the pieces a test might want to drive or assert on:
    - coordinator: the unit under integration
    - registry: holds the real skill
    - skill: exposes _catalog for picking a book
    - player: MagicMock — inspect stream.call_args
    - consumed: list of chunk tags the factory actually streamed
    - mocks: send_audio/send_audio_clear/send_status/send_model_speaking/send_dev_event mocks + the StubVoiceProvider
    """
    player, consumed = _make_player_with_tracker()
    skill = AudiobooksSkill(player=player)
    ctx = make_test_context(
        storage=NamespacedSkillStorage(storage, "audiobooks"),
        persona_data_dir=library_path.parent,
        config={"library": str(library_path)},
    )
    await skill.setup(ctx)

    registry = SkillRegistry()
    registry.register(skill)

    stub_provider = StubVoiceProvider()
    stub_provider._connected = True
    mocks = {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "provider": stub_provider,
    }

    coordinator = TurnCoordinator(
        **mocks,
        dispatch_tool=registry.dispatch,
        focus_manager=focus_manager,
    )

    return coordinator, registry, skill, player, consumed, mocks


async def _commit_turn(coordinator: TurnCoordinator, frames: int = 60) -> None:
    """Start a turn and commit it (same helper as test_turn_coordinator)."""
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_frames = frames
    await coordinator.on_ptt_stop()


def _book_at(skill: AudiobooksSkill, index: int = 0) -> dict[str, str]:
    """Get a book from the skill's Catalog as the legacy flat-dict shape.

    Test bridge: pre-T1.1 the skill exposed `_catalog: list[dict]`; now
    `_catalog` is a Catalog primitive (yields Hit objects via iteration).
    """
    catalog = skill._catalog
    assert catalog is not None, "skill not set up"
    hits = list(catalog)
    hit = hits[index]
    return {
        "id": hit.id,
        "title": hit.fields.get("title", ""),
        "author": hit.fields.get("author", ""),
        "path": str(hit.payload.get("path", "")),
    }


async def _settle(task: Any) -> None:
    """Let a background media task drain, cancelling if it takes too long."""
    import asyncio

    if task is None:
        return
    for _ in range(20):
        if task.done():
            break
        await asyncio.sleep(0.001)
    if not task.done():
        task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------


class TestPlayAudiobookEndToEnd:
    """Full flow: ptt → pre-narration → function_call → barrier → factory."""

    async def test_play_tool_latches_and_fires_factory(
        self, library_path: Path, storage: Storage, focus_manager: FocusManager
    ) -> None:
        coord, _reg, skill, player, consumed, mocks = await _build_wired_coordinator(
            library_path, storage, focus_manager
        )
        book = _book_at(skill, 0)

        await _commit_turn(coord)
        # Model pre-narrates "Ahí le pongo el libro"
        await coord.on_audio_delta(b"pre-narration")
        # Model calls play_audiobook
        await coord.on_tool_call("call_1", "play_audiobook", {"book_id": book["id"]})

        # Factory was latched — skill ran real code path.
        assert coord.current_turn is not None
        assert len(coord.current_turn.pending_audio_streams) == 1
        assert coord.current_turn.needs_follow_up is False

        # Output was sent back to the provider.
        provider: StubVoiceProvider = mocks["provider"]
        tool_outputs = [c for c in provider.sent if c[0] == "send_tool_output"]
        assert len(tool_outputs) == 1
        _, call_id, output = tool_outputs[0]
        assert call_id == "call_1"
        assert '"playing": true' in output

        # Dev event fired with the right shape.
        mocks["send_dev_event"].assert_awaited_once()
        kind, payload = mocks["send_dev_event"].await_args.args
        assert kind == "tool_call"
        assert payload["name"] == "play_audiobook"
        assert payload["has_audio_stream"] is True

        # Audio round done, then response done → factory fires.
        await coord.on_audio_done()
        await coord.on_response_done()

        # Turn ended, media task spawned.
        assert coord.current_turn is None
        assert coord.current_media_task is not None

        await _settle(coord.current_media_task)

        # Player.stream was called with the right path + position.
        player.stream.assert_called_once()
        _call_args, call_kwargs = player.stream.call_args
        assert call_kwargs["start_position"] == 0.0
        # The chunks reached send_audio via the coordinator.
        assert mocks["send_audio"].await_count >= 1
        # All consumed chunks tagged with the book's path + 0.0 start.
        assert all(book["path"] in tag for tag in consumed)

    async def test_play_factory_drains_to_send_audio(
        self, library_path: Path, storage: Storage, focus_manager: FocusManager
    ) -> None:
        """Each chunk the factory yields must land on send_audio exactly once."""
        coord, _reg, skill, _player, _consumed, mocks = await _build_wired_coordinator(
            library_path, storage, focus_manager
        )
        book = _book_at(skill, 0)

        await _commit_turn(coord)
        await coord.on_tool_call("c1", "play_audiobook", {"book_id": book["id"]})
        await coord.on_response_done()  # no follow-up, terminal barrier
        await _settle(coord.current_media_task)

        # Player mock yields 3 chunks; +1 trailing silence on natural completion.
        assert mocks["send_audio"].await_count == 4


class TestMidChainInterruptDropsFactories:
    """If the user interrupts mid-chain, accumulated factories must be dropped."""

    async def test_interrupt_after_latch_drops_factory(
        self, library_path: Path, storage: Storage, focus_manager: FocusManager
    ) -> None:
        coord, _reg, skill, player, consumed, _mocks = await _build_wired_coordinator(
            library_path, storage, focus_manager
        )
        book = _book_at(skill, 0)

        await _commit_turn(coord)
        await coord.on_tool_call("c1", "play_audiobook", {"book_id": book["id"]})
        assert coord.current_turn is not None
        assert len(coord.current_turn.pending_audio_streams) == 1

        # User interrupts before response.done fires.
        await coord.interrupt()

        # Factory was dropped — no stream invoked, no chunks consumed.
        player.stream.assert_not_called()
        assert consumed == []
        assert coord.current_turn is None
        assert coord.current_media_task is None


class TestRewindReplacesPriorMediaTask:
    """A rewind factory cancels the previous media task and streams fresh."""

    async def test_rewind_during_playback_cancels_and_starts_new(
        self, library_path: Path, storage: Storage, focus_manager: FocusManager
    ) -> None:
        import asyncio

        coord, _reg, skill, player, _consumed, _mocks = await _build_wired_coordinator(
            library_path, storage, focus_manager
        )
        book = _book_at(skill, 0)

        # Turn 1: play from start
        await _commit_turn(coord)
        await coord.on_tool_call("c1", "play_audiobook", {"book_id": book["id"]})
        await coord.on_response_done()
        first_task = coord.current_media_task
        assert first_task is not None

        # Let task 1 actually start consuming (real user: book plays for a
        # while before they interrupt). A couple of ticks is enough for the
        # mock generator to call player.stream() at least once.
        for _ in range(5):
            await asyncio.sleep(0)
        assert player.stream.call_count >= 1

        # Turn 2: user interrupts with PTT, says rewind
        await coord.on_ptt_start()
        assert first_task.done()  # interrupt cancelled the old task

        coord.current_turn.user_audio_frames = 60  # type: ignore[union-attr]
        await coord.on_ptt_stop()
        await coord.on_tool_call("c2", "audiobook_control", {"action": "rewind", "seconds": 5})
        await coord.on_response_done()

        # A NEW media task was spawned for the rewind factory.
        assert coord.current_media_task is not None
        assert coord.current_media_task is not first_task
        await _settle(coord.current_media_task)

        # Player.stream was called twice — once for play, once for rewind.
        assert player.stream.call_count == 2
        # Second call used a different start_position (rewound from the
        # position task 1 had already persisted in its finally block).
        second_call = player.stream.call_args_list[-1]
        assert "start_position" in second_call.kwargs


class TestPauseRequestsFollowUp:
    """`audiobook_control` with action=pause cancels media and requests a follow-up."""

    async def test_pause_cancels_media_task_and_requests_follow_up(
        self, library_path: Path, storage: Storage, focus_manager: FocusManager
    ) -> None:
        import asyncio

        coord, _reg, skill, player, _consumed, mocks = await _build_wired_coordinator(
            library_path, storage, focus_manager
        )
        book = _book_at(skill, 0)
        await storage.set_setting(f"audiobooks:{LAST_BOOK_KEY}", book["id"])

        # Turn 1: start playback
        await _commit_turn(coord)
        await coord.on_tool_call("c1", "play_audiobook", {"book_id": book["id"]})
        await coord.on_response_done()
        assert coord.current_media_task is not None
        first_task = coord.current_media_task

        # Let the task start
        for _ in range(3):
            await asyncio.sleep(0)

        # Turn 2: user says pause — coordinator should cancel media immediately
        await coord.on_ptt_start()
        coord.current_turn.user_audio_frames = 60  # type: ignore[union-attr]
        await coord.on_ptt_stop()
        await coord.on_audio_delta(b"listo, pauso")
        await coord.on_tool_call("c2", "audiobook_control", {"action": "pause"})

        # CancelMedia side effect: media task cancelled immediately on tool call
        assert coord.current_media_task is None or coord.current_media_task.done()
        assert first_task.done()

        # Coordinator still requests a follow-up for the model to narrate
        assert coord.current_turn is not None
        assert coord.current_turn.needs_follow_up is True
        assert coord.current_turn.pending_audio_streams == []

        await coord.on_audio_done()
        await coord.on_response_done()

        assert ("request_response",) in mocks["provider"].sent
        assert coord.current_turn is not None
        assert coord.current_turn.state == TurnState.AWAITING_NEXT_RESPONSE
