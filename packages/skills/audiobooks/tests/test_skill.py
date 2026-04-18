"""Tests for the audiobooks skill (side-effect pattern + namespaced KV storage).

The skill exposes its behavior through `setup(ctx) → handle(tool, args)`
returning a `ToolResult`. For playback tools the result carries an
`AudioStream` side effect whose `factory` the `TurnCoordinator` invokes
at the turn's terminal barrier.

Storage layout (per-skill namespaced KV via `huxley_sdk.SkillStorage`):
- `last_id`            → most-recently-played book id
- `position:<book_id>` → float seconds for that book

Tests stub `AudiobookPlayer` via the keyword-only `player=` test injection
on the skill's constructor, then drive `setup(ctx)` to wire in the
SDK-provided in-memory storage.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from huxley_sdk import AudioStream
from huxley_sdk.testing import make_test_context
from huxley_skill_audiobooks.skill import (
    LAST_BOOK_KEY,
    RESUME_REWIND_SECONDS,
    AudiobooksSkill,
    _fuzzy_score,
    _position_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from huxley_sdk import SkillContext, SkillStorage


# ---------------------------------------------------------------------------
# Helpers


async def _drain(factory: Any) -> int:
    count = 0
    async for _chunk in factory():
        count += 1
    return count


def _make_player_mock(chunks: list[bytes] | None = None) -> MagicMock:
    """Build a mock AudiobookPlayer."""
    player = MagicMock()
    default_chunks = chunks if chunks is not None else [b"chunk1", b"chunk2"]

    def stream_impl(path: Any, start_position: float = 0.0) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for c in default_chunks:
                yield c

        return gen()

    player.stream = MagicMock(side_effect=stream_impl)

    async def probe_ok(_path: Any) -> dict[str, Any]:
        return {"format": {"duration": "1000.0"}}

    player.probe = probe_ok
    return player


@pytest.fixture
def library_path(tmp_path: Path) -> Path:
    """Create a fake audiobook library."""
    lib = tmp_path / "audiobooks"

    garcia = lib / "Gabriel García Márquez"
    garcia.mkdir(parents=True)
    (garcia / "El coronel no tiene quien le escriba.mp3").write_bytes(b"fake")
    (garcia / "Cien años de soledad.mp3").write_bytes(b"fake")

    isaacs = lib / "Jorge Isaacs"
    isaacs.mkdir()
    (isaacs / "María.mp3").write_bytes(b"fake")

    (lib / "Un libro suelto.mp3").write_bytes(b"fake")

    return lib


def _make_ctx(library_path: Path) -> SkillContext:
    return make_test_context(
        config={"library": str(library_path)},
        persona_data_dir=library_path.parent,
    )


@pytest.fixture
def player_mock() -> MagicMock:
    return _make_player_mock()


@pytest.fixture
async def audiobooks_skill(library_path: Path, player_mock: MagicMock) -> AudiobooksSkill:
    skill = AudiobooksSkill(player=player_mock)
    await skill.setup(_make_ctx(library_path))
    return skill


@pytest.fixture
def storage(audiobooks_skill: AudiobooksSkill) -> SkillStorage:
    """Skill's bound SkillStorage — same instance the skill writes through."""
    assert audiobooks_skill._storage is not None
    return audiobooks_skill._storage


# ---------------------------------------------------------------------------


class TestFuzzyScore:
    def test_exact_match(self) -> None:
        assert _fuzzy_score("hello", "hello") == 1.0

    def test_case_insensitive(self) -> None:
        assert _fuzzy_score("Hello", "hello") == 1.0

    def test_partial_match(self) -> None:
        score = _fuzzy_score("coronel", "El coronel no tiene quien le escriba")
        assert score > 0.3

    def test_no_match(self) -> None:
        score = _fuzzy_score("xyz123", "Gabriel García Márquez")
        assert score < 0.3


class TestCatalogScan:
    async def test_finds_all_books(self, audiobooks_skill: AudiobooksSkill) -> None:
        assert len(audiobooks_skill._catalog) == 4

    async def test_parses_author_from_directory(self, audiobooks_skill: AudiobooksSkill) -> None:
        coronel = next(b for b in audiobooks_skill._catalog if "coronel" in b["title"].lower())
        assert coronel["author"] == "Gabriel García Márquez"

    async def test_root_level_book_has_unknown_author(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        suelto = next(b for b in audiobooks_skill._catalog if "suelto" in b["title"].lower())
        assert suelto["author"] == "Desconocido"

    async def test_empty_library(self, tmp_path: Path) -> None:
        skill = AudiobooksSkill(player=_make_player_mock())
        await skill.setup(_make_ctx(tmp_path / "empty"))
        assert len(skill._catalog) == 0


class TestSearch:
    async def test_search_by_title(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "coronel"})
        data = json.loads(result.output)
        assert data["count"] > 0
        titles = [r["title"] for r in data["results"]]
        assert any("coronel" in t.lower() for t in titles)

    async def test_search_by_author(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "García Márquez"})
        data = json.loads(result.output)
        assert data["count"] >= 2

    async def test_search_no_results(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "zzzzqqqq"})
        data = json.loads(result.output)
        assert data["count"] == 0

    async def test_search_returns_no_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "coronel"})
        assert not isinstance(result.side_effect, AudioStream)

    async def test_search_empty_library(self, tmp_path: Path) -> None:
        skill = AudiobooksSkill(player=_make_player_mock())
        await skill.setup(_make_ctx(tmp_path / "empty"))
        result = await skill.handle("search_audiobooks", {"query": "anything"})
        data = json.loads(result.output)
        assert "biblioteca está vacía" in data["message"]


class TestPlayback:
    async def test_play_returns_audio_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        book_id = audiobooks_skill._catalog[0]["id"]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book_id})
        assert isinstance(result.side_effect, AudioStream)
        data = json.loads(result.output)
        assert data["playing"] is True
        assert "title" in data
        assert "position_label" in data

    async def test_play_factory_streams_from_correct_position(
        self, audiobooks_skill: AudiobooksSkill, player_mock: MagicMock
    ) -> None:
        book = audiobooks_skill._catalog[0]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert isinstance(result.side_effect, AudioStream)

        await _drain(result.side_effect.factory)

        player_mock.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_play_factory_uses_saved_position(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "120.5")

        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)

        player_mock.stream.assert_called_once_with(
            book["path"], start_position=120.5 - RESUME_REWIND_SECONDS
        )

    async def test_play_from_beginning_ignores_saved_position(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "120.5")

        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": book["id"], "from_beginning": True}
        )
        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)

        player_mock.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_play_unknown_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": "zzz_nothing_matches_this"}
        )
        data = json.loads(result.output)
        assert "message" in data
        assert data.get("playing") is False
        assert not isinstance(result.side_effect, AudioStream)

    async def test_play_by_title_fuzzy_match(
        self, audiobooks_skill: AudiobooksSkill, player_mock: MagicMock
    ) -> None:
        book = next(b for b in audiobooks_skill._catalog if "coronel" in b["title"].lower())
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["title"]})
        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        player_mock.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_play_by_author_substring(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": "García Márquez"})
        data = json.loads(result.output)
        assert data.get("playing") is True
        assert isinstance(result.side_effect, AudioStream)

    async def test_play_persists_last_book_id(
        self, audiobooks_skill: AudiobooksSkill, storage: SkillStorage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        saved = await storage.get_setting(LAST_BOOK_KEY)
        assert saved == book["id"]

    async def test_play_factory_saves_position_on_natural_end(
        self, audiobooks_skill: AudiobooksSkill, storage: SkillStorage
    ) -> None:
        """When the factory drains to EOF, its finally saves the final position."""
        book = audiobooks_skill._catalog[0]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)

        saved_raw = await storage.get_setting(_position_key(book["id"]))
        assert saved_raw is not None
        assert float(saved_raw) == 0.0  # natural completion resets to beginning


class TestResumeLast:
    async def test_resume_last_no_saved_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("resume_last", {})
        data = json.loads(result.output)
        assert data["resumed"] is False
        assert "No tiene ningún libro a medias" in data["message"]
        assert not isinstance(result.side_effect, AudioStream)

    async def test_resume_last_picks_up_previously_played_book(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        await storage.set_setting(_position_key(book["id"]), "250.0")
        player_mock.stream.reset_mock()

        result = await audiobooks_skill.handle("resume_last", {})

        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        player_mock.stream.assert_called_once_with(
            book["path"], start_position=250.0 - RESUME_REWIND_SECONDS
        )
        data = json.loads(result.output)
        assert data["playing"] is True


class TestControl:
    """Rewind/forward/resume build new factories with updated start_position."""

    async def test_pause_returns_no_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("audiobook_control", {"action": "pause"})
        data = json.loads(result.output)
        assert data["paused"] is True
        assert not isinstance(result.side_effect, AudioStream)

    async def test_stop_returns_no_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("audiobook_control", {"action": "stop"})
        data = json.loads(result.output)
        assert data["stopped"] is True
        assert not isinstance(result.side_effect, AudioStream)

    async def test_rewind_does_not_eagerly_persist_new_position(
        self, audiobooks_skill: AudiobooksSkill, storage: SkillStorage
    ) -> None:
        """Storage is untouched when `_control` returns; only the factory writes."""
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "120.0")
        await storage.set_setting(LAST_BOOK_KEY, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )

        # Storage still at 120.0 — not updated yet.
        assert await storage.get_setting(_position_key(book["id"])) == "120.0"
        assert isinstance(result.side_effect, AudioStream)

    async def test_rewind_factory_streams_from_new_position(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "120.0")
        await storage.set_setting(LAST_BOOK_KEY, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )

        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        player_mock.stream.assert_called_once_with(book["path"], start_position=110.0)

    async def test_rewind_clamps_at_zero(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "5.0")
        await storage.set_setting(LAST_BOOK_KEY, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 60}
        )

        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        player_mock.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_forward_streams_from_new_position(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "50.0")
        await storage.set_setting(LAST_BOOK_KEY, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "forward", "seconds": 30}
        )

        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        player_mock.stream.assert_called_once_with(book["path"], start_position=80.0)

    async def test_resume_streams_last_book_from_saved_position(
        self,
        audiobooks_skill: AudiobooksSkill,
        storage: SkillStorage,
        player_mock: MagicMock,
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(_position_key(book["id"]), "75.0")
        await storage.set_setting(LAST_BOOK_KEY, book["id"])

        result = await audiobooks_skill.handle("audiobook_control", {"action": "resume"})

        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        player_mock.stream.assert_called_once_with(
            book["path"], start_position=75.0 - RESUME_REWIND_SECONDS
        )

    async def test_rewind_with_no_book_returns_friendly_message(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "message" in data
        assert not isinstance(result.side_effect, AudioStream)


class TestFactoryCancelPersistsPosition:
    """Cancelling mid-stream still persists the position via the finally block."""

    async def test_cancellation_persists_position(self, library_path: Path) -> None:
        import asyncio

        async def infinite_stream(
            _path: Any,
            start_position: float = 0.0,
        ) -> AsyncIterator[bytes]:
            while True:
                yield b"x" * 480
                await asyncio.sleep(0.001)

        player = MagicMock()
        player.stream = MagicMock(side_effect=lambda *a, **kw: infinite_stream(*a, **kw))

        async def probe_ok(_path: Any) -> dict[str, Any]:
            return {"format": {"duration": "1000.0"}}

        player.probe = probe_ok

        skill = AudiobooksSkill(player=player)
        await skill.setup(_make_ctx(library_path))

        book = skill._catalog[0]
        result = await skill.handle("play_audiobook", {"book_id": book["id"]})
        assert isinstance(result.side_effect, AudioStream)
        factory = result.side_effect.factory

        async def consume() -> None:
            async for _chunk in factory():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert skill._storage is not None
        saved_raw = await skill._storage.get_setting(_position_key(book["id"]))
        assert saved_raw is not None
        assert float(saved_raw) > 0


class TestPromptContext:
    def test_lists_all_books(self, audiobooks_skill: AudiobooksSkill) -> None:
        ctx = audiobooks_skill.prompt_context()
        assert "Biblioteca de audiolibros disponibles" in ctx
        assert "Cien años de soledad" in ctx
        assert "Gabriel García Márquez" in ctx
        assert "María" in ctx
        assert "Jorge Isaacs" in ctx

    def test_empty_library_returns_empty_string(self, tmp_path: Path) -> None:
        skill = AudiobooksSkill(player=_make_player_mock())
        # Skill not set up: catalog empty → prompt context empty.
        assert skill.prompt_context() == ""


class TestEmptySearch:
    async def test_empty_query_returns_full_catalog(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": ""})
        data = json.loads(result.output)
        assert data["count"] == len(audiobooks_skill._catalog)
        assert data["total"] == len(audiobooks_skill._catalog)
        assert "Éstos son los libros" in data["message"]

    async def test_whitespace_query_returns_full_catalog(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "   "})
        data = json.loads(result.output)
        assert data["count"] == len(audiobooks_skill._catalog)

    async def test_single_char_query_returns_full_catalog(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "a"})
        data = json.loads(result.output)
        assert data["count"] == len(audiobooks_skill._catalog)

    async def test_no_results_returns_friendly_message(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "zzzqqqxxx"})
        data = json.loads(result.output)
        assert data["count"] == 0
        assert "¿Quiere que le diga qué tengo?" in data["message"]


class TestToolsExposed:
    def test_resume_last_tool_is_exposed(self, audiobooks_skill: AudiobooksSkill) -> None:
        names = [t.name for t in audiobooks_skill.tools]
        assert "resume_last" in names


class TestSoundPalette:
    """Sound loading + warning behavior."""

    async def test_missing_sounds_path_is_silent(
        self, library_path: Path, player_mock: MagicMock
    ) -> None:
        """No sounds_path config + no default 'sounds' dir → empty palette, no error."""
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(_make_ctx(library_path))
        assert skill._sounds == {}

    async def test_existing_empty_sounds_dir_logs_warning(
        self, tmp_path: Path, library_path: Path, player_mock: MagicMock
    ) -> None:
        """sounds_path exists but contains no valid wavs → warn, don't crash."""
        sounds_dir = tmp_path / "empty_sounds"
        sounds_dir.mkdir()
        ctx = make_test_context(
            config={"library": str(library_path), "sounds_path": str(sounds_dir)},
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(ctx)
        assert skill._sounds == {}
        # logger.awarning is an AsyncMock; check it was called with sounds_empty
        warning_calls = [
            call
            for call in ctx.logger.awarning.await_args_list
            if call.args and call.args[0] == "audiobooks.sounds_empty"
        ]
        assert warning_calls, (
            f"sounds_empty warning not fired. Got: {ctx.logger.awarning.await_args_list}"
        )

    async def test_valid_wav_loads_into_palette(
        self, tmp_path: Path, library_path: Path, player_mock: MagicMock
    ) -> None:
        """A correctly-formatted PCM16/24kHz/mono WAV loads as raw bytes."""
        import wave

        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        wav_path = sounds_dir / "book_start.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x01" * 100)  # 100 samples of fake PCM

        ctx = make_test_context(
            config={"library": str(library_path), "sounds_path": str(sounds_dir)},
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(ctx)
        assert "book_start" in skill._sounds
        assert skill._sounds["book_start"] == b"\x00\x01" * 100

    async def test_wrong_format_wav_is_skipped(
        self, tmp_path: Path, library_path: Path, player_mock: MagicMock
    ) -> None:
        """A 44.1kHz stereo WAV is silently skipped — wouldn't play correctly anyway."""
        import wave

        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        wav_path = sounds_dir / "wrong_format.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(2)  # stereo, not mono
            wf.setsampwidth(2)
            wf.setframerate(44100)  # not 24kHz
            wf.writeframes(b"\x00\x01\x00\x01" * 100)

        ctx = make_test_context(
            config={"library": str(library_path), "sounds_path": str(sounds_dir)},
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(ctx)
        assert skill._sounds == {}


class TestSoundsEnabledToggle:
    """`sounds_enabled: false` opts a persona out of all earcons + silence buffer."""

    async def test_sounds_disabled_skips_palette_load(
        self, tmp_path: Path, library_path: Path, player_mock: MagicMock
    ) -> None:
        import wave

        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        with wave.open(str(sounds_dir / "book_start.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x01" * 100)

        ctx = make_test_context(
            config={
                "library": str(library_path),
                "sounds_path": str(sounds_dir),
                "sounds_enabled": False,
            },
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(ctx)
        assert skill._sounds == {}
        assert skill._silence_ms == 0


class TestOnCompletePromptFromConfig:
    """`on_complete_prompt` can be overridden per-persona via persona.yaml."""

    async def test_default_prompt_used_when_unset(self, audiobooks_skill: AudiobooksSkill) -> None:
        assert "libro" in audiobooks_skill._on_complete_prompt.lower()

    async def test_persona_can_override_prompt(
        self, library_path: Path, player_mock: MagicMock
    ) -> None:
        ctx = make_test_context(
            config={
                "library": str(library_path),
                "on_complete_prompt": "Custom completion message.",
            },
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(ctx)
        assert skill._on_complete_prompt == "Custom completion message."
        result = await skill.handle("play_audiobook", {"book_id": skill._catalog[0]["id"]})
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.on_complete_prompt == "Custom completion message."


class TestCompletionSilenceMs:
    """Skill passes silence_ms to AudioStream.completion_silence_ms — the
    coordinator owns the actual silence injection (it fires AFTER request_response
    so model latency overlaps with silence playback)."""

    async def test_silence_ms_propagates_to_audiostream(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": audiobooks_skill._catalog[0]["id"]}
        )
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.completion_silence_ms == 500

    async def test_persona_can_override_silence_ms(
        self, library_path: Path, player_mock: MagicMock
    ) -> None:
        ctx = make_test_context(
            config={"library": str(library_path), "silence_ms": 1200},
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player_mock)
        await skill.setup(ctx)
        result = await skill.handle("play_audiobook", {"book_id": skill._catalog[0]["id"]})
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.completion_silence_ms == 1200


class TestOnCompletePromptOnAllPaths:
    """Every audiobook AudioStream must carry on_complete_prompt — the dead-air rule.

    Otherwise a book ending after a seek/forward/rewind goes silent and the user
    can't tell if the device crashed.
    """

    async def test_play_carries_on_complete_prompt(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": audiobooks_skill._catalog[0]["id"]}
        )
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.on_complete_prompt is not None
        assert "libro" in result.side_effect.on_complete_prompt.lower()

    async def test_resume_last_carries_on_complete_prompt(
        self, audiobooks_skill: AudiobooksSkill, storage: SkillStorage
    ) -> None:
        await storage.set_setting(LAST_BOOK_KEY, audiobooks_skill._catalog[0]["id"])
        await storage.set_setting(_position_key(audiobooks_skill._catalog[0]["id"]), "100.0")
        result = await audiobooks_skill.handle("resume_last", {})
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.on_complete_prompt is not None

    async def test_seek_rewind_carries_on_complete_prompt(
        self, audiobooks_skill: AudiobooksSkill, storage: SkillStorage
    ) -> None:
        """Regression for #6: rewinding near book end must still announce completion."""
        book_id = audiobooks_skill._catalog[0]["id"]
        await storage.set_setting(LAST_BOOK_KEY, book_id)
        await storage.set_setting(_position_key(book_id), "200.0")
        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 30}
        )
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.on_complete_prompt is not None

    async def test_seek_forward_carries_on_complete_prompt(
        self, audiobooks_skill: AudiobooksSkill, storage: SkillStorage
    ) -> None:
        book_id = audiobooks_skill._catalog[0]["id"]
        await storage.set_setting(LAST_BOOK_KEY, book_id)
        await storage.set_setting(_position_key(book_id), "200.0")
        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "forward", "seconds": 30}
        )
        assert isinstance(result.side_effect, AudioStream)
        assert result.side_effect.on_complete_prompt is not None


class TestEarconCompletionTiming:
    """Regression #3: position must save as 0.0 if user interrupts during the
    trailing book_end + silence (book itself is done — chime is decoration)."""

    async def test_completed_set_before_trailing_chime(
        self, tmp_path: Path, library_path: Path
    ) -> None:
        """If we cancel during the trailing chime, position should still be 0.0."""
        import wave

        # Create a sounds dir with book_end so the chime path runs
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        wav_path = sounds_dir / "book_end.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x00" * 24000)  # 1s of silence as the "chime"

        # Player yields one chunk then completes immediately
        player = _make_player_mock(chunks=[b"book_chunk"])
        ctx = make_test_context(
            config={"library": str(library_path), "sounds_path": str(sounds_dir)},
            persona_data_dir=library_path.parent,
        )
        skill = AudiobooksSkill(player=player)
        await skill.setup(ctx)
        book_id = skill._catalog[0]["id"]

        # Start streaming the book; pause inside the book_end yield by cancelling
        # right after the player chunks finish but before the chime drains.
        result = await skill.handle("play_audiobook", {"book_id": book_id})
        assert isinstance(result.side_effect, AudioStream)
        gen = result.side_effect.factory()
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
            if len(chunks) >= 2:  # got book_chunk + at least 1 chime byte
                await gen.aclose()
                break

        # After interrupt during chime, position should be 0.0 (book completed)
        pos_str = await skill._storage_req.get_setting(_position_key(book_id))
        assert pos_str is not None
        assert float(pos_str) == 0.0
