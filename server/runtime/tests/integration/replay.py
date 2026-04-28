"""Recorded-session replay harness for end-to-end coordinator tests.

Loads a JSONL fixture of OpenAI Realtime server events and feeds them
through `OpenAIRealtimeProvider._handle_server_event`, exercising the
full parse + dispatch + cost-track + skill-dispatch path without any
WebSocket. Used by `test_session_replay.py` to assert observable end-to-
end behavior — the safety net for behavior-preserving refactors of the
coordinator (T1.3).

Fixture format: one JSON event per line, in the same shape OpenAI's
Realtime API sends. Comments (lines starting with `//`) and blank lines
are skipped so fixtures can be authored readably. Example:

```jsonl
// User says "ponme un libro"
{"type": "conversation.item.input_audio_transcription.completed", "transcript": "ponme un libro"}
// Model speaks
{"type": "response.audio_transcript.done", "transcript": "Ahí va"}
{"type": "response.audio.delta", "delta": "AAAA"}
// Tool call
{"type": "response.function_call_arguments.done", "call_id": "c1", "name": "play_audiobook", "arguments": "{\"book_id\": \"x\"}"}
// Done
{"type": "response.audio.done"}
{"type": "response.done", "response": {"id": "r1", "usage": {"input_token_details": {"text_tokens": 100}}}}
```

See docs/triage.md T2.3.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from huxley.voice.openai_realtime import OpenAIRealtimeProvider


@dataclass(frozen=True, slots=True)
class RecordedSession:
    """A list of OpenAI Realtime server-event dicts loaded from a fixture."""

    name: str
    events: list[dict[str, Any]]


def load_session(path: Path) -> RecordedSession:
    """Parse a JSONL fixture into a RecordedSession.

    Lines starting with `//` are treated as comments and skipped. Blank
    lines also skipped.
    """
    events: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        events.append(json.loads(line))
    return RecordedSession(name=path.stem, events=events)


async def replay(provider: OpenAIRealtimeProvider, session: RecordedSession) -> None:
    """Feed each event from `session` through the provider's event handler.

    No inter-event delay — the coordinator's ordering invariants are
    independent of wall-clock timing. If a future test needs timing
    realism (e.g. interrupt mid-replay), insert `asyncio.sleep` between
    `replay()` calls in the test itself.
    """
    for event in session.events:
        await provider._handle_server_event(event)
