"""Tests for configuration loading."""

from __future__ import annotations

from typing import TYPE_CHECKING

from huxley.config import Settings

if TYPE_CHECKING:
    import pytest


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings(openai_api_key="test")
        assert s.openai_model == "gpt-realtime-2.1-mini"
        # openai_voice defaults to None — persona.voice is the source of truth.
        assert s.openai_voice is None
        assert s.server_host == "localhost"
        assert s.server_port == 8765
        assert s.log_level == "INFO"

    def test_persona_at_boot_defaults_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dev keeps lazy boot unless the deployment opts in."""
        monkeypatch.delenv("HUXLEY_PERSONA_AT_BOOT", raising=False)
        assert Settings(openai_api_key="test").persona_at_boot is False

    def test_persona_at_boot_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HUXLEY_PERSONA_AT_BOOT", "true")
        assert Settings(openai_api_key="test").persona_at_boot is True

    def test_ignores_unknown_env_vars(self) -> None:
        """Legacy env vars from pre-stage-4 .env files must not crash startup."""
        s = Settings(
            openai_api_key="test",
            db_path="data/abuel_os.db",  # type: ignore[call-arg]
            system_prompt="something",  # type: ignore[call-arg]
        )
        assert s.openai_api_key == "test"
