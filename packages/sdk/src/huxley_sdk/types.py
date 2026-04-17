"""Core type definitions for the Huxley SDK.

Skill authors import from `huxley_sdk`:

    from huxley_sdk import Skill, ToolDefinition, ToolResult, SkillContext

Everything here is persona-agnostic and framework-independent. Huxley core
imports and implements these types, but a skill sees only this surface.

Design intent: a skill package depends ONLY on `huxley-sdk`. No other
runtime dep is required (no structlog, no pydantic, no openai). The
SDK uses Protocol types so concrete framework implementations satisfy
them structurally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path


class AppState(Enum):
    """Finite states for the application state machine.

    Owned by the framework; skills don't interact with this directly but it
    lives here so the `SessionManager`'s public protocol can reference it.
    """

    IDLE = auto()
    CONNECTING = auto()
    CONVERSING = auto()


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Maps to an OpenAI Realtime API tool schema.

    `parameters` must be a valid JSON Schema object describing the function's
    arguments. The `description` is in the persona's language — the LLM uses
    it to decide when to call the tool.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def to_api_format(self) -> dict[str, Any]:
        """Convert to the format expected by OpenAI's session.update."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class SideEffect:
    """Marker base class for side effects carried by a `ToolResult`.

    A side effect is work the framework runs *after* the model finishes
    speaking (the turn's terminal barrier), separate from the model's
    own speech. Subclasses set `kind` as a `ClassVar[str]` for
    discriminating at dispatch time.

    Audio streaming (audiobook playback, long TTS) is the first and
    currently only kind; future kinds (notifications, state updates,
    image outputs) will reuse this shape. Keep this class deliberately
    small — it's just a typed tag.
    """

    __slots__ = ()
    kind: ClassVar[str]


@dataclass(frozen=True, slots=True)
class AudioStream(SideEffect):
    """Side effect: a PCM byte stream the framework pipes to the audio channel.

    `factory` is a zero-arg callable returning an async iterator of PCM
    chunks. The framework invokes it at the turn's terminal barrier, so
    the skill's `handle()` can return quickly and the audio lives in
    the coordinator's media task lifecycle.
    """

    kind: ClassVar[str] = "audio_stream"
    factory: Callable[[], AsyncIterator[bytes]]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of a skill handling a tool call.

    `output` is JSON-serialized and sent back to the Realtime API as the
    function call output. `side_effect`, if present, is a `SideEffect`
    the framework runs after the model's final speech — today only
    `AudioStream` (audiobook playback).
    """

    output: str
    side_effect: SideEffect | None = None


class InvalidTransitionError(Exception):
    """Raised when a state machine transition is not allowed."""


# --- Skill contract ---


@runtime_checkable
class SkillStorage(Protocol):
    """Per-skill storage façade the framework hands to each skill.

    Setting keys are namespaced automatically with the skill's name
    (so `audiobooks` and another skill can both store a `last_id` without
    collision). Concrete implementation lives in Huxley core.

    Two methods is enough: skills that need richer structure can use
    composite keys like `position:<book_id>` and parse the value
    themselves. If a real skill ever needs table-style queries, this
    protocol grows; speculative additions are out of scope.
    """

    async def get_setting(self, key: str) -> str | None: ...
    async def set_setting(self, key: str, value: str) -> None: ...


class SkillLogger(Protocol):
    """Async logger interface the framework hands to each skill.

    Satisfied structurally by structlog's BoundLogger. Skills depend on
    this protocol (declared here in the SDK) and never need to import
    structlog directly.

    Skills emit events using the convention `<skill_name>.<event>` (see
    `docs/observability.md`). The framework auto-injects the current turn
    ID into all skill log lines via the bound logger.
    """

    async def ainfo(self, event: str, **kwargs: Any) -> None: ...
    async def adebug(self, event: str, **kwargs: Any) -> None: ...
    async def awarning(self, event: str, **kwargs: Any) -> None: ...
    async def aerror(self, event: str, **kwargs: Any) -> None: ...
    async def aexception(self, event: str, **kwargs: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class SkillContext:
    """Dependencies the framework injects into each skill at `setup` time.

    A skill's `__init__` takes no positional arguments (the framework
    discovers and instantiates skills via entry points); all per-skill
    configuration and infrastructure arrive here.

    - `logger`: a logger pre-tagged with `skill=<name>`. Use
      `ctx.logger.ainfo(...)` for events your skill emits.
    - `storage`: namespaced key-value storage scoped to this skill.
    - `persona_data_dir`: absolute path to the persona's data directory.
      Resolve your skill's file paths against this, not against CWD.
    - `config`: the per-skill config dict from `persona.yaml`'s
      `skills.<name>:` section.
    """

    logger: SkillLogger
    storage: SkillStorage
    persona_data_dir: Path
    config: dict[str, Any]


@runtime_checkable
class WakeWordDetectorProtocol(Protocol):
    """Structural protocol for wake word detectors (framework-internal)."""

    on_detected: Callable[[], Awaitable[None]] | None

    @property
    def enabled(self) -> bool: ...

    @enabled.setter
    def enabled(self, value: bool) -> None: ...

    async def setup(self) -> None: ...

    async def process_frame(self, pcm_16k: bytes) -> None: ...


@runtime_checkable
class Skill(Protocol):
    """Protocol for extensible skills.

    A skill declares tools (OpenAI function schemas) and handles their
    invocations. The framework discovers skills via entry points, constructs
    them with no positional arguments, then calls `setup(ctx)` with a
    `SkillContext` carrying the skill's logger, storage, persona data dir,
    and per-skill config from `persona.yaml`.

    Skills may optionally implement a `prompt_context(self) -> str` method
    that injects baseline awareness into the system prompt. It is not part
    of this Protocol — it is discovered by `getattr` in `SkillRegistry`.
    """

    @property
    def name(self) -> str: ...

    @property
    def tools(self) -> list[ToolDefinition]: ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Handle a tool call. Must return a ToolResult."""
        ...

    async def setup(self, ctx: SkillContext) -> None:
        """Called once at startup with the skill's context. Optional default no-op."""
        ...

    async def teardown(self) -> None:
        """Called on shutdown. Optional default no-op."""
        ...
