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

Extracted from `All Wii BIOS Sounds.aiff` (83.5s compilation) by `scripts/extract_sounds.py`. Pipeline:

1. `silencedetect` at `-55dB` (catches the actual silence floor — `-40dB` cut reverb tails mid-decay) with min gap `0.5s` to find boundaries between sounds.
2. Each non-silent segment + `0.5s` of tail padding (capped at half the following gap so it can't bleed into the next sound's onset).
3. Convert to PCM16 / 24kHz / mono. **No level normalization** — earlier `dynaudnorm` pass artificially boosted what little reverb survived the aggressive cut.

To re-run: `python3 scripts/extract_sounds.py`. Files land in `personas/abuelos/sounds/raw/` (gitignored). Sequential names (`s00.wav`, `s01.wav`, …); no semantic naming until you've listened.

| File   | Src window   | Out dur | Peak     |
| ------ | ------------ | ------- | -------- |
| s00    | 0.00–1.94s   | 1.94s   | -19.7 dB |
| s01    | 3.01–6.84s   | 3.83s   | -5.9 dB  |
| s02    | 7.79–10.65s  | 2.86s   | -8.0 dB  |
| s03    | 11.07–18.32s | 7.24s   | -4.6 dB  |
| s04    | 18.71–24.00s | 5.28s   | -6.0 dB  |
| s05    | 24.39–25.57s | 1.19s   | -7.7 dB  |
| s06    | 25.96–28.10s | 2.14s   | -5.8 dB  |
| s07    | 28.54–30.69s | 2.15s   | -6.3 dB  |
| s08    | 31.17–32.49s | 1.31s   | -4.8 dB  |
| s09    | 32.85–38.33s | 5.49s   | -4.9 dB  |
| s10–11 | 38.80–40.04s | <1s     | quiet    |
| s12    | 40.41–42.10s | 1.68s   | -6.4 dB  |
| s13–17 | 42.63–48.62s | <1.5s   | varied   |
| s18    | 49.32–50.64s | 1.32s   | -6.4 dB  |
| s19    | 51.26–52.95s | 1.69s   | -8.1 dB  |
| s20    | 53.66–55.04s | 1.38s   | -10.8 dB |
| s21–33 | 55.60–75.19s | <1.1s   | varied   |
| s34    | 76.54–78.08s | 1.54s   | -17.7 dB |
| s35–36 | 79.26–81.51s | ~0.5s   | varied   |

37 segments total. `extract_sounds.py` prints the full per-file table on each run (it's auto-detected, so the source-of-truth catalog is the script's stdout, not this doc).

**To finalize**: `afplay personas/abuelos/sounds/raw/*.wav` to audition. The Wii startup chime is most likely in the early sounds (s01 through s09 range — durations 1.3–5.5s with -4 to -8dB peaks fit a "welcome" chime profile). Pick two and copy:

```bash
cp personas/abuelos/sounds/raw/sNN.wav personas/abuelos/sounds/book_start.wav
cp personas/abuelos/sounds/raw/sMM.wav personas/abuelos/sounds/book_end.wav
```

Skill picks them up on next server start.

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

The model receives this as a user utterance, applies the persona constraints (`never_say_no`, the warm tone), and produces a natural spoken response — something like _"¡Ya terminó el libro! ¿Quiere que le ponga otro?"_

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

### Stage A — Extract and catalog sounds ⚠️ partial

- [x] `scripts/extract_sounds.py` auto-detects silence boundaries at `-55dB` (with `0.5s` minimum gap) and extracts each non-silent segment + `0.5s` of tail padding so reverb decays naturally
- [x] 37 segments produced as PCM16 / 24kHz / mono WAV (`s00.wav` … `s36.wav`)
- [x] Catalog table + per-file durations/peak levels generated (this document; full table printed by the script on each run)
- [ ] Listen to candidates and copy winners to `personas/abuelos/sounds/{book_start,book_end}.wav`. Until done, the skill loads an empty palette and runs without earcons (warning logged at startup). Sounds in the s01–s09 range are the most likely Wii startup-chime candidates (longer durations, healthy peak levels).

**Caveat**: candidates derive from copyrighted Nintendo audio. Personal-use only — replace with CC0 / generated chimes before distributing the AbuelOS persona publicly.

**Earlier extraction bug (fixed 2026-04-17)**: the first pass used `silencedetect` at `-40dB` with no tail padding + `dynaudnorm` post-processing. This cut reverb tails mid-decay (chimes sounded clipped/dry), then artificially boosted what little tail survived. Re-extraction at `-55dB` + 500ms tail pad + no normalization gives natural decays. If you previously tried these sounds, re-run `extract_sounds.py` to get the fixed versions.

### Stage B — SDK + coordinator wiring ✅

- [x] `AudioStream.on_complete_prompt: str | None = None` (SDK)
- [x] `AudioStream.completion_silence_ms: int = 0` (SDK; coordinator owns the silence injection so it can fire `request_response` first)
- [x] `VoiceProvider.send_conversation_message(text)` + `OpenAIRealtimeProvider` impl
- [x] `_consume_audio_stream` sets `model_speaking=True` for factory audio (#9)
- [x] `_maybe_fire_completion_prompt` creates a synthetic IN_RESPONSE turn (#2), bails on `response_cancelled` (#4), fires `request_response` BEFORE silence (#8)
- [x] `_apply_side_effects` clears `current_turn = None` BEFORE spawning the media task (race fix for #2)
- [x] Coordinator + SDK tests for natural-end, cancellation, race, model_speaking, silence ordering

### Stage C — Audiobooks skill wiring ✅

- [x] `_load_sound_palette()` uses `wave.open()` (handles non-44-byte WAV headers, skips wrong-format files) (#10)
- [x] `setup()` loads palette from `ctx.config["sounds_path"]` (relative to persona data_dir, or absolute)
- [x] `setup()` warns when sounds_dir exists but palette is empty / book_start / book_end missing (#1)
- [x] `_build_factory` yields `book_start_pcm` first, `book_end_pcm` after natural completion; `completed = True` set BEFORE the trailing chime so PTT during decoration still records the book as done (#3)
- [x] `_play` and seek/control paths all carry `on_complete_prompt` + `completion_silence_ms` (#6)
- [x] `sounds_enabled` toggle disables palette + silence atomically (#11)
- [x] Persona-overridable `on_complete_prompt` via `skills.audiobooks.on_complete_prompt` (#5)

### Stage D — Client-side thinking tone ✅

- [x] `web/src/lib/audio/playback.ts`: thinking tone frequency 440Hz → 120Hz (below the 200Hz–4kHz vocal band so it can't mask incoming speech)
- [x] `web/src/lib/ws.svelte.ts`: `SILENCE_TIMEOUT_MS` 400ms → 1500ms (was over-triggering on every normal model first-token gap, teaching the user to ignore it)
- [x] `web/src/lib/audio/playback.ts`: new `playErrorTone()` — descending 660Hz → 330Hz two-tone chime (~280ms total). Falling intervals universally read as "negative outcome" (Brewster).
- [x] `web/src/routes/+page.svelte`: `$effect` watches state transitions; `(CONNECTING|CONVERSING) → IDLE` plays the error chime so a session drop is audibly distinguishable from a normal end.

`bun run check` clean. Browser-tested: hold PTT with no audio → 1.5s of silence → low drone fills until model speaks; kill the server mid-session → descending error chime fires.

### Open follow-up

- [ ] Replace Wii BIOS earcons with CC0 / generated sounds (synth via numpy or download from freesound.org). Personal-use only until done.
- [ ] Extract WAV-loading + PCM-injection into a framework `PlaySound` primitive when a second skill needs chimes (deferred — premature today).

---

## Testing the earcons without running the full stack

Quick sanity check for any `.wav` file in the sounds directory:

```bash
# Play through system speaker (macOS) — audition all extracted candidates
afplay personas/abuelos/sounds/raw/s01.wav

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
