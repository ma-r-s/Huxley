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

from huxley_sdk.catalog import Catalog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
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


class InjectPriority(Enum):
    """Priority tier for `SkillContext.inject_turn`.

    - `NORMAL` (default) — respectful scheduling. If idle, fires
      immediately (preempts any content stream, since the framework
      assumes the skill wouldn't have fired without a reason). If a
      user or synthetic turn is in progress, queues and drains at the
      next quiet turn-end (a turn ending WITHOUT a pending content
      stream). Right for social reminders, routine chatter.
    - `PREEMPT` — urgent. If idle, fires immediately. If a turn is in
      progress, still queues (barging in on a user mid-speech is
      hostile). But at turn-end, PREEMPT fires even if the turn
      spawned a content stream — the queue doesn't wait for a "quiet
      moment" that might never come during a long audiobook session.
      The content stream request is dropped (user has to re-ask).
      Right for time-critical events: medication reminders, safety
      alerts, inbound calls you can't miss.

    Only two tiers today; the AVS-style 4-tier model
    (AMBIENT/CHIME_DEFER/INTERRUPT/CRITICAL) from the original
    io-plane spec was dropped at the focus-management pivot. Stage 1d.2
    may grow this enum when TTL + outcome handle arrive.
    """

    NORMAL = "normal"
    PREEMPT = "preempt"


class ContentType(Enum):
    """How an audio stream behaves when a higher-priority speaker
    preempts it. Verbatim from AVS Focus Management.

    - `MIXABLE` — the stream plays through but is ducked (quieter)
      while another voice overlays. Appropriate for ambient / musical
      content where overlapping voices don't clash.
    - `NONMIXABLE` — the stream pauses entirely when preempted.
      Appropriate for spoken-word content (audiobooks, radio talk,
      narrated news) where overlapping voices would be unintelligible.

    Skills declare this per-stream via `AudioStream.content_type`.
    Defaults to `NONMIXABLE` because spoken word is the dominant
    case; MIXABLE-declaring streams (background music, ambience)
    must opt in explicitly.
    """

    MIXABLE = "mixable"
    NONMIXABLE = "nonmixable"


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

    `on_complete_prompt`: when the stream ends naturally (not cancelled), the
    coordinator sends this text as a user-role conversation item and triggers
    a model response. Use it for end-of-content announcements — e.g., when a
    book finishes, the model narrates the completion in the persona's tone.
    Leave `None` for streams where natural completion needs no follow-up.

    `completion_silence_ms`: when `on_complete_prompt` is set, the coordinator
    fires the request FIRST so the LLM starts generating, then sends this much
    silence (PCM16 24kHz mono) to the client. The silence overlaps with model
    generation latency, so by the time the client finishes playing it, the
    model's response audio is usually already in flight. 500-1000ms covers
    typical OpenAI Realtime first-token latency. Set to 0 to disable.

    `content_type`: drives framework behavior when a proactive speech
    event (e.g. `inject_turn`) preempts this stream. `NONMIXABLE`
    (default) → stream pauses immediately; `MIXABLE` → stream ducks
    (gain ramps to ~0.3 over 100ms) and keeps playing under the
    overlay. Pick `NONMIXABLE` for spoken content (audiobooks,
    narrated news, talk radio) and `MIXABLE` for ambient / musical
    content where two voices don't clash.
    """

    kind: ClassVar[str] = "audio_stream"
    factory: Callable[[], AsyncIterator[bytes]]
    on_complete_prompt: str | None = None
    completion_silence_ms: int = 0
    content_type: ContentType = ContentType.NONMIXABLE


@dataclass(frozen=True, slots=True)
class CancelMedia(SideEffect):
    """Side effect: cancel the currently running media task, if any.

    Used by skills that stop playback (pause, stop) to signal the coordinator
    to cancel `current_media_task` immediately when the tool call is processed
    — not deferred to the terminal barrier — so the stream halts before the
    model's confirmation speech plays.
    """

    kind: ClassVar[str] = "cancel_media"


@dataclass(frozen=True, slots=True)
class SetVolume(SideEffect):
    """Side effect: send a volume-control command to the audio client.

    The coordinator forwards the level to the connected WebSocket client via
    `send_set_volume`. The client owns the speaker — the server never touches
    audio hardware directly. Level is clamped to [0, 100] before this is
    constructed; the coordinator passes it through as-is.
    """

    kind: ClassVar[str] = "set_volume"
    level: int


@dataclass(frozen=True, slots=True)
class PlaySound(SideEffect):
    """Side effect: play a short pre-loaded PCM clip just before the model speaks.

    For info tools that want a brief sonic cue marking "I'm responding now"
    (e.g. a news-intro chime). The coordinator queues the bytes immediately
    after firing `request_response()` for the info tool's follow-up round, so
    they reach the WebSocket ahead of the model's audio deltas — the user
    hears: chime → model voice.

    Unlike `AudioStream`, this is a one-shot clip with no completion-prompt or
    silence-buffer mechanics. Mutually exclusive with `AudioStream` on a given
    `ToolResult` (`side_effect` is a single field). Skipped silently when the
    response is cancelled (PTT race).
    """

    kind: ClassVar[str] = "play_sound"
    pcm: bytes  # raw PCM16 24kHz mono


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

    async def get_setting(self, key: str, default: str | None = None) -> str | None: ...
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


async def _noop_inject_turn(_prompt: str, **_kwargs: Any) -> None:
    """Default `inject_turn` for `SkillContext` — used by test fixtures that
    don't wire a real coordinator. Framework-built contexts replace this
    with the real callable from the `TurnCoordinator`.

    Accepts **kwargs so that skills can pass Stage 1d arguments like
    `dedup_key=...` to test contexts without them raising.
    """
    return None


@dataclass(frozen=True, slots=True)
class BackgroundTaskHandle:
    """Handle to a supervised background task spawned via
    `SkillContext.background_task`.

    Skills typically don't need this — the framework's task supervisor
    cancels every supervised task at shutdown. Hold the handle only when
    your skill wants to cancel a specific task before shutdown
    (e.g., a `cancel_timer` tool that needs to stop a pending timer's
    sleep loop).
    """

    name: str
    _cancel: Callable[[], None]

    def cancel(self) -> None:
        """Cancel the underlying asyncio task. Idempotent — calling twice
        is harmless. The task's CancelledError propagates through its
        `finally` blocks per normal asyncio semantics.
        """
        self._cancel()


@dataclass(frozen=True, slots=True)
class PermanentFailure:
    """Passed to a `background_task`'s `on_permanent_failure` callback
    when its restart budget is exhausted.

    `last_exception_class` / `last_exception_message` are strings (not the
    exception object) so the dataclass is hashable and serializable for
    `dev_event` payloads.
    """

    name: str
    last_exception_class: str
    last_exception_message: str
    restart_count: int
    elapsed_s: float


def _default_background_task(
    name: str,
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    **_kwargs: Any,
) -> BackgroundTaskHandle:
    """Default `background_task` for `SkillContext` — used by test fixtures.

    Spawns the coroutine via `asyncio.create_task` with no supervision
    (no restart, no rate limiting, no permanent-failure handling). The
    framework's `Application` replaces this with the supervised version
    backed by `huxley.background.TaskSupervisor`. Returning a real
    handle (not `None`) lets skills cancel even in test contexts so
    teardown semantics match production.
    """
    import asyncio  # local import — keeps types.py runtime-import minimal

    task: asyncio.Task[None] = asyncio.create_task(coro_factory(), name=f"bg-unsupervised:{name}")

    def _cancel() -> None:
        task.cancel()

    return BackgroundTaskHandle(name=name, _cancel=_cancel)


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
    - `inject_turn(prompt, *, dedup_key=None, priority=NORMAL)`:
      speak proactively — framework synthesizes a DIALOG turn that
      narrates `prompt` in the persona's voice. If idle, fires
      immediately (preempts any playing content stream). If a user
      or synthetic turn is active, the request is **queued**.
      `priority` controls drain behavior at turn-end: `NORMAL`
      waits for a quiet turn (one ending without content), `PREEMPT`
      fires even over a pending content stream (right for medication
      reminders and safety events). `dedup_key` (optional, opaque
      string): replaces a same-key entry in the queue
      (last-writer-wins); drops silently if a same-key request is
      currently firing. `expires_after` and an outcome-tracking
      handle are deferred to a later stage. See `docs/skills/README.md`
      for usage pattern.
    - `background_task(name, coro_factory, *, restart_on_crash=True,
      max_restarts_per_hour=10, on_permanent_failure=None) ->
      BackgroundTaskHandle`: spawn a long-running supervised task.
      Use this instead of `asyncio.create_task` so the framework
      sees crashes (logged via `aexception`), restarts within budget,
      and cancels everything at shutdown. One-shot timer-style tasks
      pass `restart_on_crash=False`.
    """

    logger: SkillLogger
    storage: SkillStorage
    persona_data_dir: Path
    config: dict[str, Any]
    inject_turn: Callable[..., Awaitable[None]] = _noop_inject_turn
    background_task: Callable[..., BackgroundTaskHandle] = _default_background_task

    def catalog(self, name: str = "default") -> Catalog:
        """Construct a fresh `Catalog` for this skill's personal-content data.

        Call once from `setup()` and keep the reference on your skill —
        each call returns an independent Catalog (the framework does NOT
        cache by name). The `name` parameter is purely for skill-side
        bookkeeping when a single skill maintains multiple catalogs (e.g.
        a music skill with both `tracks` and `playlists`).

        See `huxley_sdk.catalog` for the full API and usage example.
        """
        # name reserved for future per-name caching when a skill needs it;
        # today every call yields a fresh in-memory Catalog and the skill
        # owns the lifetime.
        del name
        return Catalog()


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

    Optional methods (`prompt_context`, `setup`, `teardown`) have empty
    defaults so skill authors only implement what they need. The defaults
    are inherited by Protocol subtyping — a class with `name`, `tools`,
    and `handle` is a valid Skill.
    """

    @property
    def name(self) -> str: ...

    @property
    def tools(self) -> list[ToolDefinition]: ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Handle a tool call. Must return a ToolResult."""
        ...

    async def setup(self, ctx: SkillContext) -> None:  # pragma: no cover - default no-op
        """Called once at startup with the skill's context. Default no-op."""
        return None

    async def teardown(self) -> None:  # pragma: no cover - default no-op
        """Called on shutdown. Default no-op."""
        return None

    def prompt_context(self) -> str:  # pragma: no cover - default empty
        """Text injected into the system prompt at session connect time.

        Skills override this to give the LLM baseline awareness without
        forcing a tool call (e.g. the audiobooks skill lists its catalog
        so the model can resolve title fuzzy-matches in one shot).

        Default: empty string — no contribution. The framework filters
        empty contributions, so subclasses that have nothing to add do
        not need to override.
        """
        return ""
