"""Tests for the SQLite storage layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huxley.storage.db import Storage


class TestAudiobookProgress:
    async def test_default_position_is_zero(self, storage: Storage) -> None:
        pos = await storage.get_audiobook_position("nonexistent")
        assert pos == 0.0

    async def test_save_and_retrieve_position(self, storage: Storage) -> None:
        await storage.save_audiobook_position("book_1", 123.45)
        pos = await storage.get_audiobook_position("book_1")
        assert pos == 123.45

    async def test_update_existing_position(self, storage: Storage) -> None:
        await storage.save_audiobook_position("book_1", 100.0)
        await storage.save_audiobook_position("book_1", 200.0)
        pos = await storage.get_audiobook_position("book_1")
        assert pos == 200.0

    async def test_multiple_books_independent(self, storage: Storage) -> None:
        await storage.save_audiobook_position("book_a", 10.0)
        await storage.save_audiobook_position("book_b", 20.0)
        assert await storage.get_audiobook_position("book_a") == 10.0
        assert await storage.get_audiobook_position("book_b") == 20.0


class TestConversationSummaries:
    async def test_no_summary_returns_none(self, storage: Storage) -> None:
        result = await storage.get_latest_summary()
        assert result is None

    async def test_save_and_retrieve_summary(self, storage: Storage) -> None:
        await storage.save_summary("User was asking about books")
        result = await storage.get_latest_summary()
        assert result == "User was asking about books"

    async def test_latest_summary_is_most_recent(self, storage: Storage) -> None:
        await storage.save_summary("First conversation")
        await storage.save_summary("Second conversation")
        result = await storage.get_latest_summary()
        assert result == "Second conversation"


class TestSettings:
    async def test_default_when_missing(self, storage: Storage) -> None:
        result = await storage.get_setting("missing_key", default="fallback")
        assert result == "fallback"

    async def test_none_when_missing_no_default(self, storage: Storage) -> None:
        result = await storage.get_setting("missing_key")
        assert result is None

    async def test_save_and_retrieve(self, storage: Storage) -> None:
        await storage.set_setting("volume", "80")
        result = await storage.get_setting("volume")
        assert result == "80"

    async def test_update_existing(self, storage: Storage) -> None:
        await storage.set_setting("volume", "80")
        await storage.set_setting("volume", "50")
        result = await storage.get_setting("volume")
        assert result == "50"
