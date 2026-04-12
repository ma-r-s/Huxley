"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

from abuel_os.config import Settings


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings(openai_api_key="test")
        assert s.openai_model == "gpt-4o-realtime-preview"
        assert s.openai_voice == "coral"
        assert s.audio_sample_rate == 24_000
        assert s.silence_timeout_seconds == 30
        assert s.log_level == "INFO"

    def test_paths_are_pathlib(self) -> None:
        s = Settings(openai_api_key="test")
        assert isinstance(s.db_path, Path)
        assert isinstance(s.audiobook_library_path, Path)

    def test_system_prompt_in_spanish(self) -> None:
        s = Settings(openai_api_key="test")
        assert "español" in s.system_prompt
