# Issue triage — second critic review (2026-04-17)

Root cause analysis and solution proposals for every issue raised in the second
independent code review. Issues are ordered: blockers first, real concerns second,
nitpicks last.

---

## B1 — `pause` and `stop` do not cancel playback

**Symptom.** `audiobook_control(action="pause")` and `audiobook_control(action="stop")`
return a `ToolResult` with no side effect. The coordinator sees a plain result,
sets `needs_follow_up=True` so the model can narrate the confirmation, and continues.
The `current_media_task` (the live ffmpeg stream) is never touched. The user hears
"Okay, pausing" while the book keeps playing.

**Root cause.** The `SideEffect` vocabulary only has one kind: `AudioStream` (start
something). There is no "stop the running stream" kind. The coordinator's
`on_tool_call` path has two branches: got an `AudioStream` → latch it for the
terminal barrier; got nothing → set `needs_follow_up`. There is no third branch for
"cancel whatever is currently running."

The `current_media_task` is owned exclusively by the coordinator. Skills have no
handle to it, and `ToolResult` has no field that maps to it. The design gap is
architectural: the `SideEffect` abstraction was designed for "start" actions only.

**Proposed solution.** Add a `CancelMedia(SideEffect)` subclass to `huxley_sdk/types.py`:

```python
@dataclass(frozen=True, slots=True)
class CancelMedia(SideEffect):
    """Side effect: cancel the currently running media task, if any."""
    kind: ClassVar[str] = "cancel_media"
```

In `coordinator.py` `on_tool_call`, add a third branch:

```python
elif isinstance(result.side_effect, CancelMedia):
    await self._stop_current_media_task()
    self.current_turn.needs_follow_up = True  # model narrates confirmation
```

The cancellation happens immediately when the tool call is processed — not deferred
to the terminal barrier — so the stream stops before the model's narration plays.
`needs_follow_up=True` is correct: the model receives the tool output
(`{"paused": true}`) and generates a spoken confirmation. The blind user always
gets an audio acknowledgement.

In the audiobooks skill `_control` method, `pause` and `stop` return:

```python
case "pause":
    return ToolResult(
        output=json.dumps({"paused": True}),
        side_effect=CancelMedia(),
    )
case "stop":
    return ToolResult(
        output=json.dumps({"stopped": True}),
        side_effect=CancelMedia(),
    )
```

**Test gap.** `TestPauseRequestsFollowUp` only checks that a follow-up response is
requested. It must also assert `coordinator.current_media_task is None` after the
tool call. A new test should simulate a running task and verify it is cancelled
before the follow-up response fires.

**Effort.** Small. Three files touched: `types.py` (new class), `coordinator.py`
(new branch in `on_tool_call`), `skill.py` (two return statements). The existing
test is updated; one new test added.

---

## B2 — Log file handle has no `atexit` registration

**Symptom.** If the process is killed (SIGKILL, kernel OOM, hard power-off), any
lines buffered in `_file_handle` but not yet written to disk are lost. Since the
debugging workflow is logging-first — a remote collaborator reads the log to
diagnose what happened — losing the last lines on a crash is exactly when the log
matters most.

**Root cause.** `setup_logging()` opens `_file_handle` as a local variable, which
is then captured by the `_TeeProcessor` instance. Python's garbage collector will
close it at shutdown in the normal case. But `atexit` handlers do not run on
SIGKILL, and they do run on normal interpreter exit, `sys.exit()`, and unhandled
exceptions — so the gap is specifically hard crashes. The `flush()` call after
every line (line 146) ensures no _line-level_ data loss during normal operation,
but the last partial internal-buffer write is at risk on crash.

**Proposed solution.** Store the handle at module level and register an `atexit`
handler:

```python
_log_file_handle: IO[str] | None = None

def setup_logging(...):
    global _log_file_handle
    ...
    _log_file_handle = log_file.open("w", encoding="utf-8")
    import atexit
    atexit.register(_close_log_file)
    ...

def _close_log_file() -> None:
    global _log_file_handle
    if _log_file_handle is not None:
        try:
            _log_file_handle.flush()
            _log_file_handle.close()
        except OSError:
            pass
        _log_file_handle = None
```

This also prevents handle leaks if `setup_logging` is called more than once
(e.g., in tests): the previous handle is closed before the new one is opened.

**Effort.** Minimal. One file, a handful of lines.

---

## C1 — `openai_api_key` defaults to `""` instead of `None`

**Symptom.** A developer who sets `HUXLEY_OPENAI_API_KEY=` (explicitly empty) in
their shell gets past the `__main__.py` guard (which checks `if not config.openai_api_key`)
and sees an obscure 401 error from OpenAI rather than a clean startup failure with
a useful message.

**Root cause.** `config.py` line 37: `openai_api_key: str = ""`. The empty string
is falsy in Python, so the guard catches it, but the type annotation says `str` and
masks the intent. A cleaner type is `str | None = None`, which makes "not configured"
unambiguous at the type level.

**Proposed solution.** Straightforward:

```python
openai_api_key: str | None = None
```

Update `__main__.py` guard:

```python
if not config.openai_api_key:
    logger.error("HUXLEY_OPENAI_API_KEY is required — set it in .env")
    raise SystemExit(1)
```

And update the type annotation in `OpenAIRealtimeProvider.__init__` to handle
`str | None` (assert or raise before the connect call).

**Effort.** Trivial. Two files.

---

## C2 — Concurrent tool calls within one response serialize

**Symptom.** If the model issues two tool calls in one response (OpenAI Realtime
sends two `response.function_call_arguments.done` events in sequence), the second
waits for the first `_dispatch_tool` to complete. Skills that do I/O (ffprobe
subprocess, DB write) add that latency to the second tool's execution serially.

**Root cause.** `on_tool_call` in `coordinator.py` awaits `_dispatch_tool` inline.
The receive loop processes one WebSocket message at a time; the coroutine for the
second tool call cannot start until `on_tool_call` for the first one returns.

**Discussion.** This is not strictly a bug for Huxley's current use case. The OpenAI
Realtime API sends tool call results one at a time, and the model can't generate the
next response until all tool outputs for the current response are submitted. So
serializing tool dispatch is observable only when two tools are in the same response
and the first tool is slow. AbuelOS's tools are fast (time query: DB read; audiobook:
DB read + ffprobe), so the serialization is invisible in practice.

**Proposed solution.** Document it explicitly rather than fix it now. The correct
future fix — if it ever matters — is to collect all tool calls for a response into
a list, then `asyncio.gather` their dispatch and send all outputs at once. This
requires buffering until `response.done`, which is a non-trivial coordinator
change. Flag on the roadmap under "multi-tool parallelism." Add a comment in
`on_tool_call`:

```python
# Tool calls within a response are dispatched serially. If a future persona
# needs parallel dispatch (multiple I/O-heavy tools in one response), collect
# them and asyncio.gather before sending outputs. See docs/triage.md C2.
```

**Effort.** Zero to document; medium to fix (coordinator restructuring).

---

## C3 — Audiobook position under-counts what the user actually heard

**Symptom.** When the user interrupts playback, the saved resume position can be
ahead of what they actually heard. Under event loop pressure (heavy tool calls,
slow WebSocket), chunks pile up in asyncio's read buffer from ffmpeg before
`send_audio` is awaited. Those bytes are counted in `bytes_read` but not yet
delivered to the speaker. The user resumes and hears a small jump forward.

**Root cause.** `skill.py` `_build_factory` line 413: `bytes_read += len(chunk)` is
incremented when the chunk is read from ffmpeg stdout, not when it is sent to the
client. The `yield chunk` returns immediately; the `_consume_audio_stream` loop then
calls `await self._send_audio(chunk)`. The bytes have been consumed from ffmpeg
before they have been delivered to the client.

**Discussion.** The discrepancy is bounded by the asyncio event loop cycle time and
the WebSocket send buffer. In practice, on a local network, this is well under one
second — a minor nuisance, not a correctness failure. The truly correct solution
would track `bytes_sent` at the consumer side (`_consume_audio_stream`), but this
requires threading a position callback back into the generator or restructuring the
`AudioStream` API. That is disproportionate complexity for the problem size.

**Proposed solution.** Accept the current behavior, document the known limitation
and its bound, and add a comment in `_build_factory`:

```python
# `bytes_read` counts bytes from ffmpeg stdout, not bytes delivered to the
# client. Under event loop pressure, position can be ahead of what was heard
# by at most one asyncio event loop cycle (~1-5ms of audio). Acceptable for
# a resume-position UX; not acceptable for frame-accurate seeking.
```

If this ever becomes a real problem (e.g., for a future seek-by-timestamp feature),
the fix is to pass a `position_callback: Callable[[float], Awaitable[None]]` into
the `AudioStream` dataclass and call it from `_consume_audio_stream` after each
`send_audio`. That keeps the position tracking at the consumer (coordinator) where
the delivered-bytes count is exact.

**Effort.** Zero to document; medium if the callback approach is taken later.

---

## C4 — `SkillStorage` protocol missing the `default` parameter

**Symptom.** Skills cannot use `await ctx.storage.get_setting("key", default="x")`
even though the underlying `Storage.get_setting` supports it. Skill authors who
look at `NamespacedSkillStorage` and try the default kwarg get a `TypeError`.

**Root cause.** The `SkillStorage` protocol in `types.py` line 129 declares
`get_setting(self, key: str) -> str | None` without a `default` parameter.
`NamespacedSkillStorage.get_setting` passes through to `Storage.get_setting` but
also omits the `default`. The protocol and the adapter are both incomplete.

**Proposed solution.** Add `default` to both:

In `types.py`:

```python
async def get_setting(self, key: str, default: str | None = None) -> str | None: ...
```

In `storage/skill.py`:

```python
async def get_setting(self, key: str, default: str | None = None) -> str | None:
    return await self._storage.get_setting(f"{self._ns}:{key}", default)
```

**Effort.** Trivial. Two files, one line each.

---

## C5 — `FakeSkill` ignores `tool_name`; all tools return the same result

**Symptom.** `FakeSkill(name="x", result=ToolResult(...))` returns the same
`ToolResult` no matter which tool is called. Tests that register a multi-tool skill
via `FakeSkill` cannot assert different outcomes per tool, and a test that
accidentally calls the wrong tool gets a success result with no signal.

**Root cause.** `testing.py` `FakeSkill.handle()` ignores its `tool_name` argument
and always returns `self._result`. The class was written for single-tool test
scenarios and never extended.

**Proposed solution.** Make `result` accept either a single result or a per-tool
dict. Backward-compatible:

```python
@dataclass
class FakeSkill:
    name: str
    result: ToolResult | dict[str, ToolResult]
    ...
    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if isinstance(self.result, dict):
            if tool_name not in self.result:
                raise ValueError(
                    f"FakeSkill '{self.name}': no result registered for tool '{tool_name}'. "
                    f"Registered: {list(self.result.keys())}"
                )
            return self.result[tool_name]
        return self.result
```

All existing tests continue to work (they pass a single `ToolResult`). New tests
can pass `result={"play": ToolResult(...), "pause": ToolResult(...)}`.

**Effort.** Trivial. One file.

---

## C6 — `flush()` on every log line causes syscall pressure at DEBUG

**Symptom.** At `HUXLEY_LOG_LEVEL=DEBUG`, every audio delta frame and every chunk
forwarded to `send_audio` generates a structlog event. Each event calls
`self._fh.flush()` in `_TeeProcessor.__call__`. On a Raspberry Pi or any
resource-constrained device, this adds a syscall to every audio frame's hot path
and will cause noticeable audio jitter.

**Root cause.** `logging.py` line 146: `self._fh.flush()` is unconditional. At
INFO+ this is benign (few events per second). At DEBUG on an audio path this is
high-frequency (hundreds of events per second during playback).

**Proposed solution.** Flush only at WARNING+ to guarantee those lines reach disk
promptly; rely on the `atexit` flush (see B2) for lower-severity events:

```python
if method in ("warning", "error", "critical"):
    self._fh.flush()
```

This keeps crash-adjacent log lines durable without adding a syscall to every
audio frame. The `atexit` handler (once B2 is fixed) ensures INFO/DEBUG lines are
not lost on normal exit.

**Effort.** Trivial. One line change. Depends on B2 being fixed first.

---

## N1 — `assert` as runtime guards in skill code

**Symptom.** `packages/skills/audiobooks/src/huxley_skill_audiobooks/skill.py`
contains 11 guards of the form `assert self._storage is not None`. Python strips
`assert` statements when running with `python -O` (optimized mode), so these guards
disappear in production builds.

**Root cause.** Defensive checks written during development. The intent is correct
— guard against calling `handle()` before `setup()` — but `assert` is the wrong
mechanism.

**Proposed solution.** Replace with explicit checks:

```python
if self._storage is None:
    raise RuntimeError(f"{self.name}: handle() called before setup()")
```

Or — more Pythonically — use a private property that raises on unset access:

```python
@property
def _storage_required(self) -> NamespacedSkillStorage:
    if self._storage is None:
        raise RuntimeError(f"{self.name}: not set up")
    return self._storage
```

Then call `self._storage_required.get_setting(...)` instead.

**Effort.** Small. One file, 11 sites. Mechanical but not risky.

---

## N2 — Spanish UI strings hardcoded in framework code

**Symptom.** `coordinator.py` lines 144, 163, 196, 396 contain Spanish status
strings (`"Escuchando… (suelta para enviar)"`, `"Muy corto — mantén el botón
mientras hablas"`, `"Listo — mantén el botón para responder"`). These are sent to
the web client for display. They are in the `TurnCoordinator` — framework code that
is supposed to be persona-agnostic.

**Root cause.** The strings were written when AbuelOS was the only persona and
extracted from app logic without being lifted out of the framework layer.

**Discussion.** There are two design choices here:

_Option A (minimal):_ Move the strings to `app.py`, which already knows about the
persona. Pass them to `TurnCoordinator` at construction as a `status_strings: dict`
argument with English defaults. `persona.yaml` grows a `ui_strings:` section. No
SDK change required.

_Option B (proper):_ Add a `ui_strings` mapping to `PersonaSpec` with keys like
`listening`, `too_short`, `ready`. The coordinator takes a `StatusStrings` dataclass
at construction. This is the right abstraction but requires a bit more YAML surface
and a new type.

Option A is the right call now — Huxley only has one persona and the change is
mechanical. Option B is worth revisiting when a second persona exists that needs
different strings.

**Effort.** Small. `coordinator.py`, `app.py`, `persona.yaml`, `persona.py`.

---

## N3 — `Turn.response_ids` field is never populated

**Symptom.** `coordinator.py` line 71: `response_ids: list[str] = field(default_factory=list)`.
No code anywhere appends to this list. It is initialized empty and stays empty for
the lifetime of every `Turn`.

**Root cause.** Leftover from an earlier design where response IDs were tracked for
cancellation correlation. The cancellation mechanism changed and this field was
never wired up or removed.

**Proposed solution.** Delete the field. Verify with a grep that nothing references
it (the field is on a private dataclass; nothing outside `coordinator.py` could
reasonably use it).

**Effort.** Trivial.

---

## N4 — `import copy` inside a hot `__call__` path

**Symptom.** `logging.py` line 141: `import copy` is inside `_TeeProcessor.__call__`.
Python caches imports after the first call so there is no measurable overhead, but
it is non-idiomatic and confusing to readers who expect to find imports at the top
of the module.

**Root cause.** The import was added late (possibly after `from __future__ import
annotations` was in place) and placed where it was needed without being hoisted.

**Proposed solution.** Move `import copy` to the top of `logging.py`.

**Effort.** Trivial.

---

## N5 — `CLAUDE.md` references `server/` paths that no longer exist

**Symptom.** `CLAUDE.md` "Definition of Done" section (line ~106) references
`cd server && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest
tests/unit/` and "Config defaults assume the server runs from `server/`". The
`server/` directory does not exist — it was renamed to `packages/core/` in Stage 1
of the refactor.

**Root cause.** The definition-of-done section was not updated when the package
layout changed. The rest of CLAUDE.md (the Commands section at the top) is correct;
the DoD section at the bottom was missed.

**Proposed solution.** Update the stale DoD section to match the Commands section.
The correct commands are already documented at the top of CLAUDE.md.

**Effort.** Trivial.

---

## Priority order

| ID  | What                                | Effort  | When        |
| --- | ----------------------------------- | ------- | ----------- |
| B1  | pause/stop don't cancel playback    | Small   | Next commit |
| B2  | Log handle no atexit                | Minimal | Next commit |
| C4  | SkillStorage missing `default`      | Trivial | Next commit |
| C5  | FakeSkill ignores tool name         | Trivial | Next commit |
| N3  | `response_ids` dead field           | Trivial | Next commit |
| N4  | `import copy` in hot path           | Trivial | Next commit |
| N5  | CLAUDE.md stale paths               | Trivial | Next commit |
| C1  | `openai_api_key` defaults to `""`   | Trivial | Next commit |
| C6  | flush() pressure at DEBUG           | Trivial | After B2    |
| N1  | `assert` as runtime guards          | Small   | Next sprint |
| N2  | Spanish strings in framework code   | Small   | Next sprint |
| C2  | Concurrent tool dispatch serializes | Doc now | Next sprint |
| C3  | Position off-by-one on cancellation | Doc now | Future      |
