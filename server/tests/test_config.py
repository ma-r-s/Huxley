"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

from abuel_os.config import Settings


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings(openai_api_key="test")
        assert s.openai_model == "gpt-4o-mini-realtime-preview"
        assert s.openai_voice == "coral"
        assert s.server_host == "localhost"
        assert s.server_port == 8765
        assert s.ffmpeg_path == "ffmpeg"
        assert s.ffprobe_path == "ffprobe"
        assert s.log_level == "INFO"

    def test_paths_are_pathlib(self) -> None:
        s = Settings(openai_api_key="test")
        assert isinstance(s.db_path, Path)
        assert isinstance(s.audiobook_library_path, Path)

    def test_system_prompt_in_spanish(self) -> None:
        s = Settings(openai_api_key="test")
        assert "español" in s.system_prompt
