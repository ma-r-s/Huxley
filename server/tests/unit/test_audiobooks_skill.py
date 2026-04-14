"""Tests for the audiobooks skill."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from abuel_os.skills.audiobooks import LAST_BOOK_SETTING, AudiobooksSkill, _fuzzy_score
from abuel_os.types import ToolAction

if TYPE_CHECKING:
    from pathlib import Path

    from abuel_os.storage.db import Storage


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


def _make_player_mock() -> AsyncMock:
    """Build a mock AudiobookPlayer with default state."""
    player = AsyncMock()
    # Non-awaitable attribute properties — tests can override them.
    player.position = 0.0
    player.duration = 1000.0
    player.is_playing = False
    player.current_path = None
    return player


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
        player = _make_player_mock()
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            player=player,
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

    async def test_search_empty_library(self, tmp_path: Path, storage: Storage) -> None:
        player = _make_player_mock()
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            player=player,
            storage=storage,
        )
        await skill.setup()
        result = await skill.handle("search_audiobooks", {"query": "anything"})
        data = json.loads(result.output)
        assert "biblioteca está vacía" in data["message"]


class TestPlayback:
    async def test_play_returns_start_playback_action(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        book_id = audiobooks_skill._catalog[0]["id"]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book_id})
        assert result.action is ToolAction.START_PLAYBACK
        data = json.loads(result.output)
        assert data["playing"] is True
        # Crucial: the result MUST carry a verbal ack instruction so the
        # model narrates something before the book starts.
        assert "message" in data
        assert len(data["message"]) > 0

    async def test_play_calls_player_load_paused(self, audiobooks_skill: AudiobooksSkill) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=0.0, paused=True
        )

    async def test_play_resumes_from_saved_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.5)

        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})

        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=120.5, paused=True
        )

    async def test_play_from_beginning_ignores_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.5)

        await audiobooks_skill.handle(
            "play_audiobook", {"book_id": book["id"], "from_beginning": True}
        )

        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=0.0, paused=True
        )

    async def test_play_unknown_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle(
            "play_audiobook", {"book_id": "zzz_nothing_matches_this"}
        )
        data = json.loads(result.output)
        # Nunca-decir-no: must have a helpful message, not a bare error.
        assert "message" in data
        assert data.get("playing") is False

    async def test_play_by_title_fuzzy_match(self, audiobooks_skill: AudiobooksSkill) -> None:
        """LLM passes title (as seen in prompt context), skill resolves it to the real id."""
        book = next(b for b in audiobooks_skill._catalog if "coronel" in b["title"].lower())
        # Pass JUST the title — not the relative-path id.
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["title"]})
        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=0.0, paused=True
        )
        assert audiobooks_skill._current_book_id == book["id"]

    async def test_play_by_author_substring(self, audiobooks_skill: AudiobooksSkill) -> None:
        """LLM passes an author name — should find at least one matching book."""
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": "García Márquez"})
        data = json.loads(result.output)
        assert data.get("playing") is True
        audiobooks_skill._player.load.assert_awaited_once()

    async def test_play_tracks_current_book_id(self, audiobooks_skill: AudiobooksSkill) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        assert audiobooks_skill._current_book_id == book["id"]

    async def test_play_persists_last_book_id(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        saved = await storage.get_setting(LAST_BOOK_SETTING)
        assert saved == book["id"]


class TestResumeLast:
    async def test_resume_last_no_saved_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("resume_last", {})
        data = json.loads(result.output)
        assert data["resumed"] is False
        assert "No tiene ningún libro a medias" in data["message"]
        # No player.load should have been called.
        audiobooks_skill._player.load.assert_not_awaited()

    async def test_resume_last_picks_up_previously_played_book(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        # Simulate a prior play session
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        await storage.save_audiobook_position(book["id"], 250.0)
        audiobooks_skill._player.load.reset_mock()

        # Now grandpa says "sigue con el libro"
        result = await audiobooks_skill.handle("resume_last", {})

        assert result.action is ToolAction.START_PLAYBACK
        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=250.0, paused=True
        )
        data = json.loads(result.output)
        assert data["playing"] is True
        assert "message" in data


class TestControl:
    """Control actions funnel through `_play` for rewind/forward/resume so
    they share the pause-then-resume-on-state-transition flow. Otherwise
    ffmpeg would start streaming while the model is still narrating its
    verbal ack, and the two audios would overlap at the client.
    """

    async def test_pause_saves_and_acknowledges(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("audiobook_control", {"action": "pause"})
        data = json.loads(result.output)
        assert data["paused"] is True
        assert "message" in data
        # Pause does NOT transition state (no side effect) — the player was
        # already stopped by _exit_playing when the user pressed PTT.
        assert result.action.value == "none"

    async def test_stop_acknowledges(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("audiobook_control", {"action": "stop"})
        data = json.loads(result.output)
        assert data["stopped"] is True
        assert "message" in data
        audiobooks_skill._player.stop.assert_awaited_once()
        assert result.action.value == "none"

    async def test_rewind_delegates_to_play_at_new_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.0)
        audiobooks_skill._current_book_id = book["id"]

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )

        # New position persisted BEFORE delegating to _play
        saved = await storage.get_audiobook_position(book["id"])
        assert saved == 110.0
        # _play → player.load paused → returns START_PLAYBACK
        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=110.0, paused=True
        )
        assert result.action is ToolAction.START_PLAYBACK

    async def test_rewind_clamps_at_zero(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 5.0)
        audiobooks_skill._current_book_id = book["id"]

        await audiobooks_skill.handle("audiobook_control", {"action": "rewind", "seconds": 60})

        saved = await storage.get_audiobook_position(book["id"])
        assert saved == 0.0

    async def test_forward_delegates_to_play(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 50.0)
        audiobooks_skill._current_book_id = book["id"]

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "forward", "seconds": 30}
        )

        saved = await storage.get_audiobook_position(book["id"])
        assert saved == 80.0
        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=80.0, paused=True
        )
        assert result.action is ToolAction.START_PLAYBACK

    async def test_resume_delegates_to_play_current_book(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 75.0)
        audiobooks_skill._current_book_id = book["id"]

        result = await audiobooks_skill.handle("audiobook_control", {"action": "resume"})

        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=75.0, paused=True
        )
        assert result.action is ToolAction.START_PLAYBACK

    async def test_resume_falls_back_to_last_book_setting(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        """After a fresh restart `_current_book_id` is None but
        `last_audiobook_id` persists in storage — resume should still work.
        """
        book = audiobooks_skill._catalog[0]
        await storage.set_setting(LAST_BOOK_SETTING, book["id"])
        await storage.save_audiobook_position(book["id"], 42.0)
        # Explicitly clear the in-memory current book id.
        audiobooks_skill._current_book_id = None

        result = await audiobooks_skill.handle("audiobook_control", {"action": "resume"})

        audiobooks_skill._player.load.assert_awaited_once_with(
            book["path"], start_position=42.0, paused=True
        )
        assert result.action is ToolAction.START_PLAYBACK

    async def test_rewind_with_no_book_returns_friendly_message(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        # No current book, no last_audiobook_id setting.
        audiobooks_skill._current_book_id = None

        result = await audiobooks_skill.handle(
            "audiobook_control", {"action": "rewind", "seconds": 10}
        )
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "message" in data
        audiobooks_skill._player.load.assert_not_awaited()


class TestSaveCurrentPosition:
    async def test_saves_when_book_loaded(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        audiobooks_skill._player.position = 42.0

        await audiobooks_skill.save_current_position()

        saved = await storage.get_audiobook_position(book["id"])
        assert saved == 42.0

    async def test_no_save_when_no_book(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        audiobooks_skill._player.position = 42.0
        # No play → _current_book_id is None → nothing to save
        await audiobooks_skill.save_current_position()
        # Nothing saved, no exception — that's all we assert.


class TestPromptContext:
    def test_lists_all_books(self, audiobooks_skill: AudiobooksSkill) -> None:
        ctx = audiobooks_skill.prompt_context()
        assert "Biblioteca de audiolibros disponibles" in ctx
        assert "Cien años de soledad" in ctx
        assert "Gabriel García Márquez" in ctx
        assert "María" in ctx
        assert "Jorge Isaacs" in ctx

    def test_empty_library_returns_empty_string(self, tmp_path: Path, storage: Storage) -> None:
        player = _make_player_mock()
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            player=player,
            storage=storage,
        )
        # No setup call — catalog is empty
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
