"""End-to-end scenario tests via JSONL fixture replay.

Drives a real `OpenAIRealtimeProvider` through a recorded sequence of
OpenAI server events and asserts observable downstream behavior. The
behavior surface includes:

- Callbacks invoked in the right order with the right arguments
- Transcript lines accumulated for summary on disconnect
- Cost tracker recorded per response.done usage
- Audio bytes decoded and forwarded

These tests are coarse-grained on purpose — the unit tests in
`test_openai_realtime_event_handler.py` cover each event type
exhaustively. These tests prove the *composition* survives.

See docs/triage.md T2.3.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from huxley.cost import CostTracker
from huxley.voice.openai_realtime import OpenAIRealtimeProvider
from huxley.voice.provider import VoiceProviderCallbacks
from huxley_sdk import SkillRegistry

from .replay import load_session, replay

if TYPE_CHECKING:
    from huxley.config import Settings
    from huxley.persona import PersonaSpec
    from huxley.storage.db import Storage


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _full_callbacks() -> tuple[VoiceProviderCallbacks, dict[str, AsyncMock]]:
    mocks = {
        "on_audio_delta": AsyncMock(),
        "on_tool_call": AsyncMock(),
        "on_response_done": AsyncMock(),
        "on_audio_done": AsyncMock(),
        "on_commit_failed": AsyncMock(),
        "on_session_end": AsyncMock(),
        "on_transcript": AsyncMock(),
    }
    cb = VoiceProviderCallbacks(
        on_audio_delta=mocks["on_audio_delta"],
        on_tool_call=mocks["on_tool_call"],
        on_response_done=mocks["on_response_done"],
        on_audio_done=mocks["on_audio_done"],
        on_commit_failed=mocks["on_commit_failed"],
        on_session_end=mocks["on_session_end"],
        on_transcript=mocks["on_transcript"],
    )
    return cb, mocks


class TestAudiobookPlayBasic:
    """Replay `audiobook_play_basic.jsonl` and assert the full chain."""

    async def test_replay_drives_full_callback_sequence(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, mocks = _full_callbacks()
        tracker = CostTracker(storage=storage, model="gpt-realtime-2.1-mini")
        prov = OpenAIRealtimeProvider(
            config=settings,
            persona=persona,
            skill_registry=SkillRegistry(),
            storage=storage,
            callbacks=cb,
            cost_tracker=tracker,
        )

        session = load_session(FIXTURES_DIR / "audiobook_play_basic.jsonl")
        await replay(prov, session)

        # Transcripts: one user line, one assistant line.
        transcript_calls = mocks["on_transcript"].await_args_list
        assert len(transcript_calls) == 2
        assert transcript_calls[0].args == (
            "user",
            "ponme un libro de garcia marquez",
        )
        assert transcript_calls[1].args == ("assistant", "Ahí va, don.")
        # Both lines accumulated for summary-on-disconnect.
        assert prov._transcript_lines == [
            "ponme un libro de garcia marquez",
            "Ahí va, don.",
        ]

        # Audio: two chunks, decoded from base64.
        assert mocks["on_audio_delta"].await_count == 2
        chunk1 = mocks["on_audio_delta"].await_args_list[0].args[0]
        assert chunk1 == base64.b64decode("AAECAw==")

        # Audio done + response done fire exactly once each.
        mocks["on_audio_done"].assert_awaited_once()
        mocks["on_response_done"].assert_awaited_once()

        # Tool call delivered with parsed args.
        mocks["on_tool_call"].assert_awaited_once_with(
            "call_1",
            "play_audiobook",
            {"book_id": "garcia-marquez-cien-anos"},
        )

        # No spurious calls.
        mocks["on_commit_failed"].assert_not_awaited()
        mocks["on_session_end"].assert_not_awaited()

    async def test_replay_records_cost_for_response_done_usage(
        self, settings: Settings, persona: PersonaSpec, storage: Storage
    ) -> None:
        cb, _ = _full_callbacks()
        tracker = CostTracker(storage=storage, model="gpt-realtime-2.1-mini")
        prov = OpenAIRealtimeProvider(
            config=settings,
            persona=persona,
            skill_registry=SkillRegistry(),
            storage=storage,
            callbacks=cb,
            cost_tracker=tracker,
        )

        session = load_session(FIXTURES_DIR / "audiobook_play_basic.jsonl")
        await replay(prov, session)

        # Fixture usage: 1500 text in + 30 audio in + 8 text out + 60 audio out
        # mini: 1500*0.60/1M + 30*10/1M + 8*2.40/1M + 60*20/1M
        #     = 0.0009 + 0.0003 + 0.0000192 + 0.0012 = 0.0024192 USD = 2419 micro-USD
        # Under the old whole-cent storage this rounded to 0 and the test
        # asserted 0; the sub-cent response now counts.
        keys: list[str] = []
        cursor = await storage._conn.execute(
            "SELECT key FROM settings WHERE key LIKE 'cost:%:micro_usd'"
        )
        async for row in cursor:
            keys.append(row[0])
        assert len(keys) == 1
        stored = await storage.get_setting(keys[0])
        assert stored is not None
        assert int(stored) == 2419


class TestLoaderHandlesCommentsAndBlankLines:
    """Direct test of the JSONL loader's comment/blank-line skipping."""

    def test_skips_comments_and_blank_lines(self, tmp_path: Path) -> None:
        fixture = tmp_path / "demo.jsonl"
        fixture.write_text(
            "// header comment\n"
            "\n"
            '{"type": "a"}\n'
            "// inline comment between events\n"
            '{"type": "b"}\n'
            "\n",
            encoding="utf-8",
        )

        session = load_session(fixture)

        assert session.name == "demo"
        assert session.events == [{"type": "a"}, {"type": "b"}]
