"""Core type definitions for AbuelOS.

All shared types live here to avoid circular imports. Components import from this
module, never from each other for type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


class AppState(Enum):
    """Finite states for the application state machine."""

    IDLE = auto()
    CONNECTING = auto()
    CONVERSING = auto()
    PLAYING = auto()


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Maps to an OpenAI Realtime API tool schema.

    The `parameters` dict must be a valid JSON Schema object describing
    the function's arguments.
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


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of a skill handling a tool call.

    `output` is JSON-serialized and sent back to the Realtime API as the
    function call output. `audio_factory`, if present, is a callable the
    `TurnCoordinator` invokes after the model's final speech to produce
    a media stream (e.g. an audiobook). See `docs/turns.md` for the full
    design.
    """

    output: str
    audio_factory: Callable[[], AsyncIterator[bytes]] | None = None


class InvalidTransitionError(Exception):
    """Raised when a state machine transition is not allowed."""


@runtime_checkable
class WakeWordDetectorProtocol(Protocol):
    """Structural protocol for wake word detectors.

    Both the real openWakeWord detector and the dev-mode keyboard trigger
    implement this interface so they are interchangeable in Application and
    AudioRouter.
    """

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

    Skills declare tools (OpenAI function schemas) and handle calls.
    The Application discovers tools from all registered skills and routes
    incoming tool calls to the appropriate handler.
    """

    @property
    def name(self) -> str: ...

    @property
    def tools(self) -> list[ToolDefinition]: ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Handle a tool call. Must return a ToolResult."""
        ...

    async def setup(self) -> None:
        """Called once at startup. Optional — default is a no-op."""
        ...

    async def teardown(self) -> None:
        """Called on shutdown. Optional — default is a no-op."""
        ...
