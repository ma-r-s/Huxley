"""Tests for the audiobooks skill (v3 factory pattern).

With the turn coordinator in place, `_play` / `_control` no longer call
`player.load(...)` imperatively — they return a `ToolResult.audio_factory`
closure that wraps `player.stream(path, start_position)`. The coordinator
invokes that closure at the turn's terminal barrier. These tests exercise
the skill by:

- asserting `result.audio_factory is not None` for side-effect tools
- invoking the factory and checking `player.stream` was called with the
  expected path + start_position
- verifying the factory's `finally` block persists position to storage
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from abuel_os.skills.audiobooks import LAST_BOOK_SETTING, AudiobooksSkill, _fuzzy_score

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from abuel_os.storage.db import Storage


# ---------------------------------------------------------------------------
# Helpers


async def _drain(factory: Any) -> int:
    """Invoke a factory, iterate its generator to completion, return chunk count.

    Triggers the generator's `finally` block so tests can assert that
    storage writes happen on natural EOF / cancel.
    """
    count = 0
    async for _chunk in factory():
        count += 1
    return count


def _make_player_mock(chunks: list[bytes] | None = None) -> MagicMock:
    """Build a mock AudiobookPlayer.

    `stream(path, start_position)` returns a fresh async generator yielding
    the given chunks, then raises `StopAsyncIteration` (natural EOF). The
    mock tracks call args so tests can verify path/start_position.
    """
    player = MagicMock()

    default_chunks = chunks if chunks is not None else [b"chunk1", b"chunk2"]

    def stream_impl(
        path: Any,
        start_position: float = 0.0,
    ) -> AsyncIterator[bytes]:
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


@pytest.fixture
async def audiobooks_skill(library_path: Path, storage: Storage) -> AudiobooksSkill:
    player = _make_player_mock()
    skill = AudiobooksSkill(
        library_path=library_path,
        player=player,
        storage=storage,
    )
    await skill.setup()
    return skill


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

    async def test_empty_library(self, tmp_path: Path, storage: Storage) -> None:
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            player=_make_player_mock(),
            storage=storage,
        )
        await skill.setup()
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
        """Search is an info tool — should not carry an audio factory."""
        result = await audiobooks_skill.handle("search_audiobooks", {"query": "coronel"})
        assert result.audio_factory is None

    async def test_search_empty_library(self, tmp_path: Path, storage: Storage) -> None:
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            player=_make_player_mock(),
            storage=storage,
        )
        await skill.setup()
        result = await skill.handle("search_audiobooks", {"query": "anything"})
        data = json.loads(result.output)
        assert "biblioteca está vacía" in data["message"]


class TestPlayback:
    async def test_play_returns_audio_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        book_id = audiobooks_skill._catalog[0]["id"]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book_id})
        assert result.audio_factory is not None
        data = json.loads(result.output)
        assert data["playing"] is True
        assert "message" in data
        assert len(data["message"]) > 0

    async def test_play_factory_streams_from_correct_position(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        book = audiobooks_skill._catalog[0]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert result.audio_factory is not None

        await _drain(result.audio_factory)

        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_play_factory_uses_saved_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.5)

        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert result.audio_factory is not None
        await _drain(result.audio_factory)

        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=120.5)

    async def test_play_from_beginning_ignores_saved_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.5)

        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": book["id"], "from_beginning": True}
        )
        assert result.audio_factory is not None
        await _drain(result.audio_factory)

        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_play_unknown_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": "zzz_nothing_matches_this"}
        )
        data = json.loads(result.output)
        assert "message" in data
        assert data.get("playing") is False
        assert result.audio_factory is None

    async def test_play_by_title_fuzzy_match(self, audiobooks_skill: AudiobooksSkill) -> None:
        """LLM passes title (as seen in prompt context), skill resolves to real id."""
        book = next(b for b in audiobooks_skill._catalog if "coronel" in b["title"].lower())
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["title"]})
        assert result.audio_factory is not None
        await _drain(result.audio_factory)
        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_play_by_author_substring(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": "García Márquez"})
        data = json.loads(result.output)
        assert data.get("playing") is True
        assert result.audio_factory is not None

    async def test_play_persists_last_book_id(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        saved = await storage.get_setting(LAST_BOOK_SETTING)
        assert saved == book["id"]

    async def test_play_factory_saves_position_on_natural_end(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        """When the factory drains to EOF, its finally saves the final position.

        The mock player yields 2 chunks of 6 bytes each, so
        bytes_read = 12, elapsed = 12 / 48_000 ≈ 0.00025s. With
        start_position = 0.0, the persisted position is ~0.00025.
        """
        book = audiobooks_skill._catalog[0]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert result.audio_factory is not None
        await _drain(result.audio_factory)

        saved = await storage.get_audiobook_position(book["id"])
        assert saved > 0  # some position was written by the factory's finally


class TestResumeLast:
    async def test_resume_last_no_saved_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("resume_last", {})
        data = json.loads(result.output)
        assert data["resumed"] is False
        assert "No tiene ningún libro a medias" in data["message"]
        assert result.audio_factory is None

    async def test_resume_last_picks_up_previously_played_book(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        await storage.save_audiobook_position(book["id"], 250.0)
        audiobooks_skill._player.stream.reset_mock()

        result = await audiobooks_skill.handle("resume_last", {})

        assert result.audio_factory is not None
        await _drain(result.audio_factory)
        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=250.0)
        data = json.loads(result.output)
        assert data["playing"] is True


class TestControl:
    """Rewind/forward/resume build new factories with updated start_position.

    Pause/stop are info-only responses (`audio_factory=None`) — the actual
    stopping happens via the coordinator's interrupt when the user pressed
    PTT to speak to the model. The prior media task's finally already saved
    the terminal position by the time `_control` runs.
    """

    async def test_pause_returns_no_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("audiobook_control", {"action": "pause"})
        data = json.loads(result.output)
        assert data["paused"] is True
        assert result.audio_factory is None

    async def test_stop_returns_no_factory(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("audiobook_control", {"action": "stop"})
        data = json.loads(result.output)
        assert data["stopped"] is True
        assert result.audio_factory is None

    async def test_rewind_does_not_eagerly_persist_new_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        """Closure-captured atomicity: storage is untouched when `_control`
        returns. The new position only lands when the factory actually runs.
        """
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.0)
        await storage.set_setting(LAST_BOOK_SETTING, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )

        # Storage still at 120.0 — not updated yet.
        assert await storage.get_audiobook_position(book["id"]) == 120.0
        assert result.audio_factory is not None

    async def test_rewind_factory_streams_from_new_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.0)
        await storage.set_setting(LAST_BOOK_SETTING, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )

        assert result.audio_factory is not None
        await _drain(result.audio_factory)
        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=110.0)

    async def test_rewind_clamps_at_zero(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 5.0)
        await storage.set_setting(LAST_BOOK_SETTING, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 60}
        )

        assert result.audio_factory is not None
        await _drain(result.audio_factory)
        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=0.0)

    async def test_forward_streams_from_new_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 50.0)
        await storage.set_setting(LAST_BOOK_SETTING, book["id"])

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "forward", "seconds": 30}
        )

        assert result.audio_factory is not None
        await _drain(result.audio_factory)
        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=80.0)

    async def test_resume_streams_last_book_from_saved_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 75.0)
        await storage.set_setting(LAST_BOOK_SETTING, book["id"])

        result = await audiobooks_skill.handle("audiobook_control", {"action": "resume"})

        assert result.audio_factory is not None
        await _drain(result.audio_factory)
        audiobooks_skill._player.stream.assert_called_once_with(book["path"], start_position=75.0)

    async def test_rewind_with_no_book_returns_friendly_message(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "message" in data
        assert result.audio_factory is None


class TestFactoryCancelPersistsPosition:
    """Cancelling mid-stream still persists the position via the finally block."""

    async def test_cancellation_persists_position(
        self, library_path: Path, storage: Storage
    ) -> None:
        import asyncio

        # Player that yields forever — so we can cancel mid-stream
        async def infinite_stream(
            _path: Any,
            start_position: float = 0.0,
        ) -> AsyncIterator[bytes]:
            while True:
                yield b"x" * 480  # ~5 ms of audio
                await asyncio.sleep(0.001)

        player = MagicMock()
        player.stream = MagicMock(side_effect=lambda *a, **kw: infinite_stream(*a, **kw))

        async def probe_ok(_path: Any) -> dict[str, Any]:
            return {"format": {"duration": "1000.0"}}

        player.probe = probe_ok

        skill = AudiobooksSkill(library_path=library_path, player=player, storage=storage)
        await skill.setup()

        book = skill._catalog[0]
        result = await skill.handle("play_audiobook", {"book_id": book["id"]})
        assert result.audio_factory is not None

        # Spawn the factory consumer as a task; cancel it after a few chunks.
        async def consume() -> None:
            async for _chunk in result.audio_factory():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        saved = await storage.get_audiobook_position(book["id"])
        # Some positive position was persisted by the finally block.
        assert saved > 0


class TestPromptContext:
    def test_lists_all_books(self, audiobooks_skill: AudiobooksSkill) -> None:
        ctx = audiobooks_skill.prompt_context()
        assert "Biblioteca de audiolibros disponibles" in ctx
        assert "Cien años de soledad" in ctx
        assert "Gabriel García Márquez" in ctx
        assert "María" in ctx
        assert "Jorge Isaacs" in ctx

    def test_empty_library_returns_empty_string(self, tmp_path: Path, storage: Storage) -> None:
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            player=_make_player_mock(),
            storage=storage,
        )
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
