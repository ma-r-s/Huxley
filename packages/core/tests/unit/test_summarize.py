"""Tests for the conversation summarizer.

The OpenAI client is mocked at the module level (`huxley.summarize.AsyncOpenAI`)
so no real network or API key is needed.

See docs/triage.md T1.5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from huxley import summarize as summarize_module
from huxley.summarize import (
    DEFAULT_MAX_LINES,
    SUMMARY_MODEL,
    summarize_transcript,
)

if TYPE_CHECKING:
    import pytest


def _mock_openai_response(content: str | None) -> Any:
    """Build a minimal AsyncOpenAI response shape with the given content."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice] if content is not None else []
    return response


def _patch_openai(monkeypatch: pytest.MonkeyPatch, response: Any) -> MagicMock:
    """Replace huxley.summarize.AsyncOpenAI with a controllable mock.

    Returns the create() mock so tests can assert call args.
    """
    create = AsyncMock(return_value=response)
    chat = MagicMock()
    chat.completions.create = create
    client = MagicMock()
    client.chat = chat

    factory = MagicMock(return_value=client)
    monkeypatch.setattr(summarize_module, "AsyncOpenAI", factory)
    return create


def _patch_openai_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    create = AsyncMock(side_effect=exc)
    chat = MagicMock()
    chat.completions.create = create
    client = MagicMock()
    client.chat = chat

    factory = MagicMock(return_value=client)
    monkeypatch.setattr(summarize_module, "AsyncOpenAI", factory)


class TestSummarizeTranscript:
    async def test_returns_summary_text_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_openai(monkeypatch, _mock_openai_response("El usuario está escuchando un libro."))

        result = await summarize_transcript(
            ["user: pon el libro", "assistant: ahí va"], api_key="sk-test"
        )

        assert result == "El usuario está escuchando un libro."

    async def test_strips_whitespace_from_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_openai(monkeypatch, _mock_openai_response("  spaced summary  \n"))

        result = await summarize_transcript(["x"], api_key="sk-test")

        assert result == "spaced summary"

    async def test_returns_none_for_empty_transcript(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Should not even attempt the API call.
        create = _patch_openai(monkeypatch, _mock_openai_response("ignored"))

        result = await summarize_transcript([], api_key="sk-test")

        assert result is None
        create.assert_not_awaited()

    async def test_returns_none_for_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        create = _patch_openai(monkeypatch, _mock_openai_response("ignored"))

        result = await summarize_transcript(["x"], api_key="")

        assert result is None
        create.assert_not_awaited()

    async def test_returns_none_when_api_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_openai_raising(monkeypatch, RuntimeError("network down"))

        result = await summarize_transcript(["x"], api_key="sk-test")

        assert result is None

    async def test_returns_none_when_choices_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_openai(monkeypatch, _mock_openai_response(None))

        result = await summarize_transcript(["x"], api_key="sk-test")

        assert result is None

    async def test_returns_none_when_content_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_openai(monkeypatch, _mock_openai_response(""))

        result = await summarize_transcript(["x"], api_key="sk-test")

        assert result is None

    async def test_caps_input_to_max_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        create = _patch_openai(monkeypatch, _mock_openai_response("ok"))

        # 100 lines, default cap is DEFAULT_MAX_LINES (60) — only the last
        # 60 should be sent.
        lines = [f"line {i}" for i in range(100)]
        await summarize_transcript(lines, api_key="sk-test")

        kwargs = create.await_args.kwargs
        user_msg = kwargs["messages"][1]["content"]
        sent_lines = user_msg.split("\n")
        assert len(sent_lines) == DEFAULT_MAX_LINES
        assert sent_lines[0] == f"line {100 - DEFAULT_MAX_LINES}"
        assert sent_lines[-1] == "line 99"

    async def test_uses_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        create = _patch_openai(monkeypatch, _mock_openai_response("ok"))

        await summarize_transcript(["x"], api_key="sk-test")

        assert create.await_args.kwargs["model"] == SUMMARY_MODEL

    async def test_includes_system_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        create = _patch_openai(monkeypatch, _mock_openai_response("ok"))

        await summarize_transcript(["x"], api_key="sk-test")

        messages = create.await_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "Resúmela en 3 frases" in messages[0]["content"]
