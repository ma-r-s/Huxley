"""Test utilities for Huxley SDK consumers.

`FakeSkill` is the minimal Skill-protocol implementation used across the
test suite (registry tests, coordinator tests, integration tests).
`make_test_context` builds a no-op `SkillContext` for testing skill
`setup()` paths. Importing from `huxley_sdk.testing` is intentional —
these helpers are not part of the runtime SDK surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from huxley_sdk.types import SkillContext, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from huxley_sdk.types import SkillStorage


class _NoopSkillStorage:
    """In-memory SkillStorage for tests. Implements the protocol structurally."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key, default)

    async def set_setting(self, key: str, value: str) -> None:
        self._data[key] = value

    async def list_settings(self, prefix: str = "") -> list[tuple[str, str]]:
        return sorted((k, v) for k, v in self._data.items() if k.startswith(prefix))

    async def delete_setting(self, key: str) -> None:
        self._data.pop(key, None)


def make_test_context(
    *,
    name: str = "test",
    config: dict[str, Any] | None = None,
    persona_data_dir: Path | None = None,
    storage: SkillStorage | None = None,
    language: str | None = None,
) -> SkillContext:
    """Build a SkillContext for unit-testing a skill's setup() and handle().

    Defaults to:
    - `logger`: an AsyncMock with the SkillLogger methods
    - `storage`: an in-memory `_NoopSkillStorage` (overrideable)
    - `persona_data_dir`: `/tmp` (override for path-resolution tests)
    - `config`: `{}` (override per skill's expected keys)
    - `language`: derived from `config["_language"]` or
      `config["language_code"]` if present, else `"en"`. Override to test
      i18n-specific paths without touching skill config.

    Use `storage=` to inject a populated mock for tests that read pre-existing
    state. Use `config=` to inject the keys your skill expects from
    `persona.yaml`.
    """
    logger = MagicMock()
    for method in ("ainfo", "adebug", "awarning", "aerror", "aexception"):
        setattr(logger, method, AsyncMock())
    cfg = config or {}
    if language is None:
        hint = cfg.get("_language") or cfg.get("language_code")
        language = str(hint).lower() if isinstance(hint, str) and hint else "en"
    return SkillContext(
        logger=logger,
        storage=storage if storage is not None else _NoopSkillStorage(),
        persona_data_dir=persona_data_dir or Path("/tmp"),
        config=cfg,
        language=language,
    )


class FakeSkill:
    """A minimal Skill-protocol implementation for testing.

    Tracks `setup_called`, `teardown_called`, and `handle_calls` for
    assertions. Defaults to a single tool `fake_tool` and a successful
    `ToolResult`. Override via constructor args for custom shapes.

    `result` can be a single `ToolResult` (returned for every tool call) or a
    `dict[str, ToolResult]` mapping tool names to results. Using a dict raises
    a clear error if an unregistered tool is called, which catches tests that
    accidentally dispatch to the wrong tool name.
    """

    def __init__(
        self,
        name: str = "fake",
        tools: list[ToolDefinition] | None = None,
        result: ToolResult | dict[str, ToolResult] | None = None,
    ) -> None:
        self._name = name
        self._tools = tools or [
            ToolDefinition(
                name="fake_tool",
                description="A fake tool for testing",
                parameters={
                    "type": "object",
                    "properties": {"arg": {"type": "string"}},
                },
            )
        ]
        self._result: ToolResult | dict[str, ToolResult] = result or ToolResult(
            output='{"ok": true}'
        )
        self.handle_calls: list[tuple[str, dict[str, Any]]] = []
        self.setup_called = False
        self.setup_context: SkillContext | None = None
        self.teardown_called = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def tools(self) -> list[ToolDefinition]:
        return self._tools

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        self.handle_calls.append((tool_name, args))
        if isinstance(self._result, dict):
            if tool_name not in self._result:
                registered = list(self._result.keys())
                raise ValueError(
                    f"FakeSkill '{self._name}': no result for tool '{tool_name}'. "
                    f"Registered: {registered}"
                )
            return self._result[tool_name]
        return self._result

    async def setup(self, ctx: SkillContext) -> None:
        self.setup_called = True
        self.setup_context = ctx

    async def teardown(self) -> None:
        self.teardown_called = True
