"""Tests for the audiobooks skill."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from abuel_os.skills.audiobooks import AudiobooksSkill, _fuzzy_score
from abuel_os.types import ToolAction

if TYPE_CHECKING:
    from pathlib import Path

    from abuel_os.storage.db import Storage


@pytest.fixture
def library_path(tmp_path: Path) -> Path:
    """Create a fake audiobook library."""
    lib = tmp_path / "audiobooks"

    # Author directory with books
    garcia = lib / "Gabriel García Márquez"
    garcia.mkdir(parents=True)
    (garcia / "El coronel no tiene quien le escriba.mp3").write_bytes(b"fake")
    (garcia / "Cien años de soledad.mp3").write_bytes(b"fake")

    # Another author
    isaacs = lib / "Jorge Isaacs"
    isaacs.mkdir()
    (isaacs / "María.mp3").write_bytes(b"fake")

    # Root-level book (no author)
    (lib / "Un libro suelto.mp3").write_bytes(b"fake")

    return lib


@pytest.fixture
async def audiobooks_skill(library_path: Path, storage: Storage) -> AudiobooksSkill:
    mpv = AsyncMock()
    skill = AudiobooksSkill(
        library_path=library_path,
        mpv=mpv,
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
        mpv = AsyncMock()
        skill = AudiobooksSkill(
            library_path=tmp_path / "empty",
            mpv=mpv,
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
        mpv = AsyncMock()
        skill = AudiobooksSkill(library_path=tmp_path / "empty", mpv=mpv, storage=storage)
        await skill.setup()
        result = await skill.handle("search_audiobooks", {"query": "anything"})
        data = json.loads(result.output)
        assert "No hay audiolibros" in data["message"]


class TestPlayback:
    async def test_play_returns_start_playback_action(
        self, audiobooks_skill: AudiobooksSkill
    ) -> None:
        book_id = audiobooks_skill._catalog[0]["id"]
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": book_id})
        assert result.action is ToolAction.START_PLAYBACK
        data = json.loads(result.output)
        assert data["playing"] is True

    async def test_play_calls_mpv_loadfile(self, audiobooks_skill: AudiobooksSkill) -> None:
        book = audiobooks_skill._catalog[0]
        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})
        audiobooks_skill._mpv.loadfile.assert_awaited_once_with(book["path"])

    async def test_play_resumes_from_saved_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.5)

        await audiobooks_skill.handle("play_audiobook", {"book_id": book["id"]})

        audiobooks_skill._mpv.seek_absolute.assert_awaited_once_with(120.5)

    async def test_play_from_beginning_ignores_position(
        self, audiobooks_skill: AudiobooksSkill, storage: Storage
    ) -> None:
        book = audiobooks_skill._catalog[0]
        await storage.save_audiobook_position(book["id"], 120.5)

        await audiobooks_skill.handle(
            "play_audiobook", {"book_id": book["id"], "from_beginning": True}
        )

        audiobooks_skill._mpv.seek_absolute.assert_not_awaited()

    async def test_play_unknown_book(self, audiobooks_skill: AudiobooksSkill) -> None:
        result = await audiobooks_skill.handle("play_audiobook", {"book_id": "nonexistent"})
        data = json.loads(result.output)
        assert "error" in data


class TestControl:
    async def test_pause(self, audiobooks_skill: AudiobooksSkill) -> None:
        await audiobooks_skill.handle("audiobook_control", {"action": "pause"})
        audiobooks_skill._mpv.pause.assert_awaited_once()

    async def test_resume(self, audiobooks_skill: AudiobooksSkill) -> None:
        await audiobooks_skill.handle("audiobook_control", {"action": "resume"})
        audiobooks_skill._mpv.resume.assert_awaited_once()

    async def test_rewind(self, audiobooks_skill: AudiobooksSkill) -> None:
        await audiobooks_skill.handle("audiobook_control", {"action": "rewind", "seconds": 15})
        audiobooks_skill._mpv.seek.assert_awaited_once_with(-15)

    async def test_forward(self, audiobooks_skill: AudiobooksSkill) -> None:
        await audiobooks_skill.handle("audiobook_control", {"action": "forward", "seconds": 60})
        audiobooks_skill._mpv.seek.assert_awaited_once_with(60)
