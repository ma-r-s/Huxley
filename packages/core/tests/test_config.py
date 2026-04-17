"""Tests for configuration loading."""

from __future__ import annotations

from huxley.config import Settings


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings(openai_api_key="test")
        assert s.openai_model == "gpt-4o-mini-realtime-preview"
        # openai_voice defaults to None — persona.voice is the source of truth.
        assert s.openai_voice is None
        assert s.server_host == "localhost"
        assert s.server_port == 8765
        assert s.log_level == "INFO"

    def test_ignores_unknown_env_vars(self) -> None:
        """Legacy env vars from pre-stage-4 .env files must not crash startup."""
        s = Settings(
            openai_api_key="test",
            db_path="data/abuel_os.db",  # type: ignore[call-arg]
            system_prompt="something",  # type: ignore[call-arg]
        )
        assert s.openai_api_key == "test"
