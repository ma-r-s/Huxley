# Sound UX Architecture

How Huxley uses non-speech audio — earcons, tones, chimes — to communicate state to users who cannot see a screen. This document covers the architecture, the injection mechanism, the persona sound palette, and the implementation stages.

For the research foundation (Brewster's rules, earcon design principles, the "never silent" requirement), see [`research/sonic-ux.md`](./research/sonic-ux.md).

---

## Why sounds matter for AbuelOS

Don Grandpa is blind. When a book ends, he doesn't see a progress bar stop. He hears silence — and silence for a blind user means one of three things: the book ended, the device crashed, or the network dropped. There is no visual affordance to disambiguate.

The "dead air is a bug" rule from the AbuelOS persona spec (`never_say_no` constraint #4) applies here too. Every state transition that matters must produce audio. The two most critical:

1. **Book starts** — signal that playback is beginning, so he doesn't wonder if his command was understood.
2. **Book ends** — signal that the book finished (not crashed), and offer what to do next.

Secondary transitions (error, thinking) are important but not part of v1. See [Stage D](#stage-d-client-side-thinking-tone).

---

## Transport: server-side PCM injection

All sound effects are raw PCM16 24 kHz mono bytes injected server-side into the existing audio stream. They travel through the same WebSocket `audio` message channel the model's voice uses. No new protocol messages. No client changes.

```
Book audio (PCM chunks from ffmpeg)
    ↕
[AudioStream factory]
    ↕
TurnCoordinator._consume_audio_stream()
    ↕
server.send_audio(chunk)          ← same path as model voice
    ↕
WebSocket: { type: "audio", data: <base64 PCM16> }
    ↕
Client: play PCM on speaker
```

This works identically for the browser client and the future ESP32 client. The ESP32 already knows how to play audio chunks from the WebSocket — it doesn't care whether the bytes came from OpenAI's model or from an ffmpeg-decoded earcon.

---

## Sound file format

All sound files must be in the exact format the WebSocket audio channel expects:

- **Encoding**: PCM16 (signed 16-bit little-endian)
- **Sample rate**: 24,000 Hz
- **Channels**: 1 (mono)
- **Container**: raw bytes (no WAV header) OR WAV (the skill strips the 44-byte header at load time)

The extraction pipeline in `scripts/extract_sounds.py` produces WAV files. The skill reads each file, strips the WAV header, and caches the raw PCM bytes in memory at `setup()` time.

---

## Sound palette

The AbuelOS persona sound palette lives in `personas/abuelos/sounds/`. Sounds are WAV files in the format above. Raw extractions (with silence gaps) live in `sounds/raw/`; the curated production sounds live at `sounds/<name>.wav`.

### Catalog: extracted BIOS sounds

Extracted from `All Wii BIOS Sounds.aiff` (83.5s compilation) using silence detection (threshold: -40dB, min gap: 0.5s). All files at `personas/abuelos/sounds/raw/`.

| File                   | Raw dur | Peak     | Notes                                      |
| ---------------------- | ------- | -------- | ------------------------------------------ |
| s00_short_open.wav     | 0.91s   | -19.7 dB | quiet blip, likely pre-roll                |
| s01_long_chime.wav     | 2.66s   | -5.6 dB  | sustained chime — book_start candidate     |
| s02_chime.wav          | 1.94s   | -5.7 dB  | chime — **book_start candidate**           |
| s03_long_sequence.wav  | 3.53s   | -3.1 dB  | long sequence — too long for earcon        |
| s04_sequence.wav       | 2.43s   | -1.1 dB  | sequence — border-line long                |
| s05_long_sequence2.wav | 3.70s   | -2.5 dB  | long — too long                            |
| s06_short_click.wav    | 0.49s   | -3.5 dB  | short click — error/notification candidate |
| s07_click.wav          | 0.73s   | -4.3 dB  | click — navigation                         |
| s08_chime.wav          | 1.25s   | -2.3 dB  | chime — **book_end candidate**             |
| s09_chime.wav          | 1.49s   | -2.9 dB  | chime — book_end candidate                 |
| s10_short.wav          | 0.84s   | -1.5 dB  | short                                      |
| s11_long_music.wav     | 4.76s   | -1.8 dB  | full music segment — too long              |
| s12_short.wav          | 0.83s   | -3.2 dB  | short                                      |
| s13_short.wav          | 0.60s   | -7.7 dB  | short                                      |
| s14_short.wav          | 0.72s   | -17.8 dB | very quiet — likely filler                 |
| s15_short.wav          | 0.81s   | -8.1 dB  | short                                      |
| s16_short.wav          | 0.79s   | -2.9 dB  | short                                      |
| s17_short.wav          | 0.68s   | -4.8 dB  | short                                      |
| s18_short.wav          | 0.64s   | -7.7 dB  | short                                      |
| s19_tiny.wav           | 0.38s   | -4.4 dB  | tiny                                       |
| s20_short.wav          | 0.52s   | -3.0 dB  | short                                      |
| s21_chime.wav          | 1.03s   | -16.4 dB | quiet — likely filler                      |

**Selection for v1 production (subject to listening review):**

| Role         | Candidate         | Rationale                            |
| ------------ | ----------------- | ------------------------------------ |
| `book_start` | s02_chime (1.94s) | Good duration; -5.7dB — not too loud |
| `book_end`   | s08_chime (1.25s) | Conclusive feel; slightly louder     |

To finalize: listen to each candidate in the `raw/` directory and copy the winners to `sounds/book_start.wav` and `sounds/book_end.wav`.

### Adding silence padding

The book_end sound is followed by model speech. To prevent an abrupt chime→voice transition:

- The stream factory appends **500ms of silence** after the earcon (before the stream completes).
- This covers the typical model generation latency (200–800ms) so the transition feels deliberate.

### Target durations

| Role         | Target duration | Max  |
| ------------ | --------------- | ---- |
| `book_start` | 0.8–2.0s        | 2.5s |
| `book_end`   | 0.5–1.5s        | 2.0s |

---

## Stream injection pattern

The `AudiobooksSkill` injects earcons directly in the stream factory; the trailing silence buffer is owned by the **coordinator** (via `AudioStream.completion_silence_ms`) so the silence can be sent AFTER firing the request_response, overlapping with model first-token latency:

```python
def _build_factory(self, book_id, path, start_position):
    player = self._player
    set_position = self._set_position
    skill = self
    book_start_pcm = self._sounds.get("book_start", b"")
    book_end_pcm = self._sounds.get("book_end", b"")

    async def stream():
        skill._now_playing_id = book_id
        skill._now_playing_start_pos = start_position
        skill._now_playing_start_time = time.monotonic()
        bytes_read = 0
        completed = False
        try:
            if book_start_pcm:
                yield book_start_pcm

            async for chunk in player.stream(path, start_position=start_position):
                bytes_read += len(chunk)
                yield chunk

            # Book audio finished cleanly — mark completed BEFORE the trailing
            # chime so a PTT during decoration still records the book as
            # complete (position 0.0).
            completed = True

            if book_end_pcm:
                yield book_end_pcm
        finally:
            skill._now_playing_id = None
            elapsed = bytes_read / BYTES_PER_SECOND
            final_pos = 0.0 if completed else start_position + elapsed
            await set_position(book_id, final_pos)

    return stream

# In _play():
return ToolResult(
    output=...,
    side_effect=AudioStream(
        factory=factory,
        on_complete_prompt=self._on_complete_prompt,
        completion_silence_ms=self._silence_ms,  # coordinator handles it
    ),
)
```

**Interrupt safety**:

- PTT mid-book → generator cancelled with `completed = False` → position saves as `start_pos + elapsed`. Resume works.
- PTT during trailing chime → generator cancelled with `completed = True` (already set) → position saves as `0.0`. Book correctly recorded as finished even if the decoration was interrupted.
- The `book_start` earcon yields unconditionally as the first chunk — even on a play that the user immediately interrupts. Acceptable: tells the user the play command was understood.

---

## Natural completion → model voice

When a book ends naturally, the user needs to hear what happened. Rather than hardcoding a text-to-speech message, we let the LLM narrate — it already knows the book, the user, and the right tone.

### The `on_complete_prompt` field

`AudioStream` gains two optional fields:

```python
@dataclass(frozen=True, slots=True)
class AudioStream(SideEffect):
    kind: ClassVar[str] = "audio_stream"
    factory: Callable[[], AsyncIterator[bytes]]
    on_complete_prompt: str | None = None
    completion_silence_ms: int = 0
```

`on_complete_prompt`: when set and the stream ends naturally (not cancelled), the coordinator narrates this prompt via the LLM after the stream completes.

`completion_silence_ms`: the coordinator sends this much PCM16 silence to the client AFTER firing `request_response`. It overlaps with the LLM's first-token latency so the user hears _book → chime → silence → model voice_ with minimal dead air. 500–1000ms covers typical OpenAI Realtime latency. Set to 0 to disable.

When the stream ends naturally (not cancelled), the coordinator sends `on_complete_prompt` as a user-role conversation item and triggers a model response. The coordinator must (a) skip the prompt if a PTT-induced cancel raced the trailing silence, and (b) create a synthetic IN_RESPONSE turn so the incoming model reply (deltas, tool calls, response_done) is handled by the existing turn-aware paths instead of being silently dropped:

```python
# packages/core/src/huxley/turn/coordinator.py

async def _consume_audio_stream(self, stream: AudioStream, turn_id: str | None) -> None:
    try:
        async for chunk in stream.factory():
            await self._send_audio(chunk)
        await logger.ainfo("coord.audio_stream_ended", turn=turn_id, cancelled=False)
        await self._maybe_fire_completion_prompt(stream, turn_id)
    except asyncio.CancelledError:
        await logger.ainfo("coord.audio_stream_ended", turn=turn_id, cancelled=True)
        raise
    except Exception:
        await logger.aexception("coord.audio_stream_ended", turn=turn_id, error=True)

async def _maybe_fire_completion_prompt(self, stream, parent_turn_id):
    if not stream.on_complete_prompt:
        return
    # PTT during the trailing chime/silence — let the user's new turn proceed.
    if self.response_cancelled:
        return
    # The user already started a new turn (race won by PTT).
    if self.current_turn is not None:
        return
    # Synthetic turn so on_response_done / on_tool_call / status updates run.
    self.current_turn = Turn(state=TurnState.IN_RESPONSE)
    self._bind_turn()
    self.response_cancelled = False
    await self._provider.send_conversation_message(stream.on_complete_prompt)
    await self._provider.request_response()
```

For the synthetic turn to work, `_apply_side_effects` must clear `current_turn = None` BEFORE spawning the media task — otherwise the task can complete during the cleanup awaits and see `current_turn != None`, falsely concluding a new user turn started.

### The `send_conversation_message` provider method

```python
# packages/core/src/huxley/voice/openai_realtime.py

async def send_conversation_message(self, text: str) -> None:
    """Inject a user-role message into the conversation without audio input."""
    await self._ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }))
```

This goes into the `VoiceProvider` protocol as well so any future provider must implement it.

### What the prompt says

The AudiobooksSkill hardcodes the prompt in the persona's language:

```python
ON_COMPLETE_PROMPT = (
    "El libro ha llegado a su fin. "
    "Felicita al usuario por haber terminado el libro y pregunta "
    "si quiere que busque otro."
)
```

The model receives this as a user utterance, applies the persona constraints (`never_say_no`, the warm tone), and produces a natural spoken response — something like _"¡Ya terminó el libro! ¿Quiere que le ponga otro, don?"_

### Turn state machine implications

After natural completion:

1. `_consume_audio_stream` exits the try block (no CancelledError)
2. `send_conversation_message` + `request_response()` fire
3. OpenAI generates a new model response
4. The coordinator handles it as a new response cycle

The coordinator is already in the correct state after stream completion — it's waiting for the next event. The injected conversation item causes OpenAI to emit a new `response.created` event, and the coordinator handles it normally.

---

## Persona config interface

The sounds block lives under `skills.audiobooks` in `persona.yaml`:

```yaml
skills:
  audiobooks:
    library_path: data/audiobooks
    ffmpeg_path: ffmpeg
    ffprobe_path: ffprobe
    sounds_path: sounds # relative to persona data_dir
    sounds_enabled: true # master toggle — set false to silence everything
    silence_ms: 500 # silence after firing request_response
    on_complete_prompt: | # localized per-persona
      El libro ha llegado a su fin. Felicita al usuario...
```

If `sounds_enabled: false`, the skill loads no palette and `silence_ms` is forced to 0 — the persona behaves as if no sounds were configured.

The skill loads sounds at `setup()` time:

```python
async def setup(self, ctx: SkillContext) -> None:
    ...
    sounds_dir = Path(ctx.config.get("sounds_path", "sounds"))
    if not sounds_dir.is_absolute():
        sounds_dir = ctx.data_dir / sounds_dir
    self._sounds = _load_sound_palette(sounds_dir)
    self._silence_ms = int(ctx.config.get("silence_ms", 500))
```

```python
def _load_sound_palette(directory: Path) -> dict[str, bytes]:
    """Load *.wav files from directory; strip 44-byte WAV header; cache as raw PCM."""
    palette: dict[str, bytes] = {}
    if not directory.exists():
        return palette
    for wav in directory.glob("*.wav"):
        raw = wav.read_bytes()
        # Strip WAV header (44 bytes for standard PCM WAV)
        palette[wav.stem] = raw[44:]
    return palette
```

If `sounds_path` doesn't exist or has no `.wav` files, the skill runs silently — no earcons, but no errors. Earcons are optional enhancements.

---

## Three-layer architecture (current and future)

### Layer 1 — Inline PCM injection (this stage)

Sounds that are tightly coupled to a specific audio stream: book_start and book_end. Lives inside the stream factory. These are the sounds we build now.

**Trade-off**: The book_start earcon yields before any positioning logic can fail. If `player.stream()` raises on the first chunk, the user has already heard the start chime. Acceptable: the error handler will produce a spoken error message anyway.

### Layer 2 — `PlaySound(SideEffect)` (next)

A new `SideEffect` kind for turn-boundary sounds that aren't part of a stream:

```python
@dataclass(frozen=True, slots=True)
class PlaySound(SideEffect):
    kind: ClassVar[str] = "play_sound"
    pcm: bytes  # raw PCM16 24kHz mono
```

Use case: the `system` skill wants to play a notification chime when a timer fires. The coordinator handles `PlaySound` by yielding the bytes through `send_audio`, then asking for a follow-up response. Same pattern as `AudioStream` but no factory — just bytes.

Not built yet. Add when a skill actually needs it.

### Layer 3 — Framework-level state machine sounds (later)

Session-connect chime, disconnect tone, "thinking" audio. These aren't tied to any skill — they come from the framework. Will need a separate `SoundRegistry` or config block at the persona level. Far future.

---

## Client-side thinking tone (Stage D)

The current thinking tone (`web/src/routes/+page.svelte`) generates a 440Hz sine wave on the client. For AbuelOS:

1. **Frequency**: 440Hz sits in the 200Hz–4kHz vocal band. Correct target: below 200Hz (a low drone, like an old telephone hold tone — non-intrusive, clearly non-speech).
2. **Silence timeout**: currently 400ms. For an elderly blind user expecting responses, 1500ms is a better threshold before the tone starts — long enough that normal LLM latency doesn't trigger it, short enough that actual silence gets flagged.
3. **Error tone**: currently no distinct error audio. When `state: IDLE` after an error (OpenAI drops the session), a short descending two-tone chime signals the problem.

These are client changes only. They do not affect the server or the WebSocket protocol. Ship after the server-side earcon work is stable.

---

## Implementation stages

### Stage A — Extract and catalog sounds (done)

- [x] Run silence detection on `All Wii BIOS Sounds.aiff`
- [x] Extract 22 candidate segments as PCM16 24kHz WAV files (`scripts/extract_sounds.py`)
- [x] Build duration + peak level catalog (this document)
- [ ] Listen to each candidate; copy winners to `personas/abuelos/sounds/book_start.wav` and `book_end.wav`

**DoD**: `personas/abuelos/sounds/book_start.wav` and `book_end.wav` exist, are correct format, play correctly, are under 2s each.

### Stage B — SDK + coordinator changes

Files to change:

- `packages/sdk/src/huxley_sdk/types.py` — add `on_complete_prompt: str | None = None` to `AudioStream`
- `packages/core/src/huxley/voice/openai_realtime.py` — add `send_conversation_message(text: str)`
- `packages/core/src/huxley/voice/protocol.py` (or wherever `VoiceProvider` lives) — add method to protocol
- `packages/core/src/huxley/turn/coordinator.py` — check `stream.on_complete_prompt` after natural completion
- Tests: `test_turn_coordinator.py`, `test_coordinator_skill_integration.py`

**DoD**: Tests pass. Integration test (manual): after book ends naturally, model speaks the completion message.

### Stage C — Skill changes (earcon injection + on_complete_prompt)

Files to change:

- `packages/skills/audiobooks/src/huxley_skill_audiobooks/skill.py`:
  - `setup()`: load sounds palette from `ctx.config["sounds_path"]`
  - `_build_factory()`: inject leading/trailing PCM
  - `_play()`: pass `on_complete_prompt=ON_COMPLETE_PROMPT` on the `AudioStream`
- `personas/abuelos/persona.yaml`: add `sounds_path` and `silence_ms` under `skills.audiobooks`
- Tests: `test_skill.py` — assert factory yields earcon bytes first/last; assert `on_complete_prompt` set

**DoD**: Tests pass. Integration test (manual): start book → hear chime → book plays → book ends → chime → model speaks.

### Stage D — Client-side thinking tone (later)

- [ ] `web/src/routes/+page.svelte`: change thinking tone from 440Hz to ~120Hz
- [ ] Raise silence timeout 400ms → 1500ms
- [ ] Add descending two-tone error chime on `state: IDLE` after error

**DoD**: `bun run check` passes. Browser smoke test: hold PTT with no content → thinking tone starts at ~1.5s, is clearly non-speech frequency.

---

## Testing the earcons without running the full stack

Quick sanity check for any `.wav` file in the sounds directory:

```bash
# Play through system speaker (macOS)
afplay personas/abuelos/sounds/raw/s02_chime.wav

# Convert to PCM bytes and check size
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 \
  personas/abuelos/sounds/book_start.wav

# Verify it's correct format (PCM16, 24kHz, mono)
ffprobe -v error -show_streams personas/abuelos/sounds/book_start.wav 2>&1 | \
  grep -E "codec_name|sample_rate|channels"
```

---

## Failure modes and fallbacks

| Failure                                              | Behavior                                                                                           |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `sounds_path` doesn't exist                          | Skill runs silently — no earcons, no error                                                         |
| `.wav` file is wrong format                          | Bytes play as garbage audio — mitigated by the extraction script always producing 24kHz mono PCM16 |
| `on_complete_prompt` fails to trigger model response | Book ends silently; user may re-engage via PTT                                                     |
| Earcon yields but book stream immediately fails      | User hears start chime + model error narration — acceptable                                        |

The degradation path is intentional: earcons enhance UX but are not load-bearing for correctness. A deployment without sound files works; it just has silent transitions.
