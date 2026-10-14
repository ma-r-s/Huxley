# Sound UX Architecture

How Huxley uses non-speech audio — earcons, tones, chimes — to communicate state to users who cannot see a screen. This document covers the architecture, the injection mechanism, the persona sound palette, and the implementation stages.

For the research foundation (Brewster's rules, earcon design principles, the "never silent" requirement), see [`research/sonic-ux.md`](./research/sonic-ux.md).

---

## Why sounds matter for Abuelo

Don Grandpa is blind. When a book ends, he doesn't see a progress bar stop. He hears silence — and silence for a blind user means one of three things: the book ended, the device crashed, or the network dropped. There is no visual affordance to disambiguate.

The "dead air is a bug" rule from the Abuelo persona spec (`never_say_no` constraint #4) applies here too. Every state transition that matters must produce audio. The two most critical:

1. **Book starts** — signal that playback is beginning, so he doesn't wonder if his command was understood.
2. **Book ends** — signal that the book finished (not crashed), and offer what to do next.

Secondary transitions (error, thinking) are important but not part of v1. See [Stage D](#stage-d-client-side-thinking-tone).

---

## Transport: server-side PCM injection

All sound effects are raw PCM16 24 kHz mono bytes injected server-side into the existing audio stream. They travel through the same WebSocket `audio` message channel the model's voice uses (for a client on [Opus](./protocol.md#opus-audio-audioopus), the same binary Opus packets). No new protocol messages. No client changes.

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
- **Container**: WAV. The shared loader (`huxley_sdk.audio.load_pcm_palette`) opens each file with the standard `wave` module and reads the raw PCM frames — handles non-44-byte WAV headers (LIST/INFO chunks, metadata edits) without breaking.

The synthesis pipeline in [`scripts/synth_sounds.py`](../scripts/synth_sounds.py) produces WAV files in this exact format. Skills load them once at `setup()` time and cache the raw PCM bytes in memory.

---

## Loudness

Everything the client plays sits at one level (T1.47), so the listener sets the volume once. Before this, the voice played at -27 LUFS and the chimes at -15 to -20: turned up for the voice, every chime jumped out.

### The standard

| What                                        | Target                              | Measured as                    | Set by                                                                                                  |
| ------------------------------------------- | ----------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| The voice (model audio)                     | -20 LUFS                            | integrated                     | a fixed gain and a limiter, live (below)                                                                |
| Content streams (radio, Spotify)            | -20 LUFS                            | integrated, kept by a leveler  | `StreamLeveler`, live, for streams with `AudioStream.loudness_key`                                     |
| Spoken clips (`unavailable.wav`)            | -20 LUFS                            | integrated                     | [`scripts/render_persona_clip.py`](../scripts/render_persona_clip.py)                                   |
| Earcons and tones                           | -19 LUFS momentary max (-23 integrated) | the loudest 400 ms         | [`scripts/synth_sounds.py`](../scripts/synth_sounds.py), `reconnect.no_signal_tone_pcm`                 |
| The waiting blip (T1.41)                    | -26 LUFS momentary max              | the loudest 400 ms             | `turn/wait_cue.py` `BLIP_TARGET_LUFS`                                                                   |
| The walkie's own prompts (firmware)         | -20 LUFS                            | integrated                     | owned by the firmware; today about -26, to be re-rendered there                                         |
| Peaks                                       | at most -1.5 dBFS true peak, decoded on the walkie | sample peak on the wire | `PeakLimiter`: -3.0 dBFS on the wire and the voice, -3.5 for streams                         |

All of it is measured with ITU-R BS.1770 (K-weighting, 400 ms blocks, -70 LUFS absolute and -10 LU relative gates) by [`huxley.loudness`](../server/runtime/src/huxley/loudness.py), the one implementation the runtime, the scripts and the tests share. It reads within 0.2 LU of ffmpeg's `ebur128` on every test signal and every shipped sound.

**Why cues are measured by their loudest moment.** A chime is heard at its strike. Its integrated value depends on how long its reverb tail is (a longer tail reads quieter, so normalizing it would make the strike louder), and a clip shorter than 400 ms has none. The loudest 400 ms has neither problem. At -19 it lands this palette at -22.4 to -23.8 LUFS integrated: 3 dB under the voice by the measure the problem was stated in. A clip under 400 ms is measured as if padded with silence to 400 ms.

**Why the waiting blip is quieter.** It repeats every 1.5 s while the user waits, under everything else. -26 keeps the place T1.41 tuned it to (about 11 dB under the voice's loudest moments) now that the voice is 7 dB louder.

**Why the wire ceiling is -3 dBFS, not -1.5.** The walkie gets Opus at 32 kbit/s, which adds peak of its own. Measured through the server's own encoder and decoder: speech limited at -3.0 decodes to at most -2.6 dBTP (-2.0 limits to -1.65); dense radio and music gain up to 1.8 dB even unlimited (Caracol at -5.8 dBTP decodes to -4.6), so streams are limited at -3.5 and decode to at most -1.7.

### The gain stages, and why each lives where it does

1. **The voice: a fixed gain and a limiter**, in `TurnCoordinator.on_audio_delta`, on model audio only (not a `PlaySound`, not a stream, not the waiting cue). The model's level is steady (-24.8 to -28.4 LUFS over 14 answers, median -27.1), so a fixed gain calibrates it; nothing about the voice needs a leveler. The gain is the voice provider's calibration (`huxley.voice.openai_realtime.OUTPUT_GAIN_DB`, +7 dB, measured on coral with gpt-realtime-2.1-mini) unless the persona sets `audio.voice_gain_db` (0 turns the stage off). With +7, the same 14 answers measure -20.0 LUFS together; the limiter touches a few milliseconds per answer (5 dB at most, on the loudest). The limiter resets at each response's first audio, so a cut-off answer leaves nothing behind. The recorder taps Opus packets as they're written to the socket, so its `model` files are what the walkie got: post-gain.
2. **Content streams: a `StreamLeveler`**, in `ContentStreamObserver`, for streams whose skill sets `AudioStream.loudness_key`. In the framework, because every stream skill would otherwise re-implement the same thing (what a resume keeps, what a new stream re-learns, the log). Opt-in per stream, because only the skill knows whether its content's level is under its control (an audiobook file mastered by the skill author is; a radio station isn't). Before the duck envelope, so a duck is never measured, nor made up for. The first `preroll_ms` the stream ever yields (its start earcon, already at the cue level) passes as is.
3. **The wire: one `PeakLimiter`** in `AudioServer.send_audio`, the ceiling for everything sent, whatever its source: a call's audio, a persona's own sound, a third-party skill's. Sources level themselves; this only catches what goes over (its cuts show as `limited_ms` on `server.tx.audio_stats`).

### The limiter

`PeakLimiter` adds no latency: the chunk in hand is its own lookahead. Per 1 ms block it computes the gain that keeps the block's peak at the ceiling; the envelope at each block boundary is the lower of that block's and the next one's, so the linear ramp across a block never lets a sample over. After a cut it holds 15 ms, then releases over 150 ms (a release with no hold ripples at the voice's pitch period, heard as distortion). The one thing a chunk can't see is a peak in its own first millisecond while the previous chunk ended unlimited: that block ramps down rather than stepping (a step clicks), and the few samples it lets over are clamped. Nothing over the ceiling ever leaves it. With nothing over, it returns the chunk untouched (or just the gain applied).

### The stream leveler

Causal, no added latency, about 1.5 % of one Orange Pi core per stream (pure Python: the production box has no numpy).

| Step                | Value                                                                                                   | Why                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Measure             | K-weighted energy per 100 ms; a 400 ms window                                                           | BS.1770's momentary loudness                                                              |
| Silence gate        | a window under -45 LUFS changes nothing                                                                 | pauses, dropouts and Spotify's silent seeks never pump the gain up                        |
| Relative gate       | a window 10 LU under the program estimate changes nothing                                               | a quiet passage isn't ridden up                                                           |
| Program estimate    | seeded by the first window, a running mean for 30 s, then an exponential average (time constant 30 s) | long, so a song's quiet verse moves it about 2 dB at most, not 5 (critic, 10 s rejected)  |
| Gain                | target minus estimate, within -20 and +6 dB                                                             | +6 is plenty (Blu, the quietest station, needs +3.3) and caps any pumping                 |
| Speed               | at most 6 dB/s down, 2 dB/s up, ramped across each chunk                                                | a loud start comes down fast; nothing comes up fast                                       |
| Start               | the gain the key last settled at; a key never heard starts at -6 dB                                     | most sources are louder than -20, so a new one starts quiet, never loud                   |
| Memory              | learned gains in the persona's settings (`audio.level_gain_db:<key>`), saved as each stream stops     | a restart doesn't make every source start wrong                                           |
| Last                | the `PeakLimiter`, at -3.5 dBFS                                                                         | a boosted stream never clips                                                              |

A press holds the stream (the pump stops; `factory()` runs again on resume): the leveler belongs to the stream, so the resume carries on with what it measured. A new stream for the same key re-learns its estimate but starts at the learned gain. Every pump start and stop, and every 30 s of audio, logs `audio.level` (see [`observability.md`](./observability.md#loudness-events)): over time that is the real level of each station and of Spotify.

Keys used: radio `radio:<station id>`; Spotify `spotify:book:<uri>` for a book (a narrator's level holds across sessions), `spotify:music` for everything else. ffmpeg's `loudnorm` was rejected: in its dynamic mode it adds about 3 s of latency.

### Before and after (ffmpeg `ebur128`, 2026-10-13)

| Sound                 | Before: LUFS     | Before: true peak | After: LUFS                  | After: true peak |
| --------------------- | ---------------- | ----------------- | ---------------------------- | ---------------- |
| Voice (14 answers)    | -26.9 (-24.8 to -28.4) | -4.7        | -20.0                        | -2.6 (-2.4 decoded) |
| `unavailable.wav`     | -23.0            | -5.9              | -20.2                        | -3.0             |
| `book_end`            | -20.5            | -3.0              | -23.6 (M max -19.0)          | -6.1             |
| `news_start`          | -19.1            | -2.3              | -22.7 (M max -19.0)          | -5.8             |
| `search_start`        | -17.7            | +0.3 (clipped)    | -22.5 (M max -19.0)          | -4.4             |
| `book_start`          | -16.8            | -3.0              | -23.8 (M max -19.0)          | -10.0            |
| `radio_start`         | -15.2            | -2.1              | -22.6 (M max -19.0)          | -9.6             |
| `persona_swap`        | -15.2            | -2.0              | -22.4 (M max -19.0)          | -9.1             |
| no-signal tone        | M max -20.9      | -14.7             | M max -19.0                  | -12.9            |
| waiting blip          | M max -32.9      | -16.0             | M max -26.0                  | -9.1             |
| Radio: Blu            | -23.3            | -11.2             | -20.4 (last 15 s -19.9)      | -8.1             |
| Radio: Caracol        | -17.4            | -5.8              | -20.1 (last 15 s -19.7)      | -8.2             |
| Radio: La W           | -18.1            | -5.9              | -20.7 (last 15 s -20.7)      | -8.0             |
| Radio: Javeriana      | -15.2            | -1.2              | -19.2 (last 15 s -18.6)      | -4.6             |

Radio rows: 25 s of each station fetched on the production box, leveled offline in 100 ms chunks as the radio skill reads them, starting as a key never heard (-6 dB). A second play of the same key starts at the learned gain and measures -19.9, -20.0, -20.6 and -19.1. Javeriana got louder through the sample; the leveler follows it at its slow pace by design.

---

## The shared earcon palette

All earcons live at **`server/personas/_shared/sounds/`** as `<role>.wav` (PCM16 / 24 kHz / mono). The palette is shared across personas; any persona may override individual sounds by pointing its skill's `sounds_path` at a private directory and dropping its own `<role>.wav` files there.

Every WAV in the palette is **synthesized from scratch** by [`scripts/synth_sounds.py`](../scripts/synth_sounds.py). No third-party samples, no derivative content, no licensing constraints — the script and the rendered WAVs are 100% original Huxley work. The script is the source of truth; the WAVs are committed so the runtime never depends on the synthesis toolchain.

### Current palette

| Role           | Used by    | Pitch material                                          | Duration | Reverb wet |
| -------------- | ---------- | ------------------------------------------------------- | -------- | ---------- |
| `book_start`   | audiobooks | C6 + G6 perfect fifth, 70 ms stagger                    | ~2.6 s   | 0.40       |
| `book_end`     | audiobooks | G6 + B6 major third, simultaneous (above vocal band)    | ~1.7 s   | 0.32       |
| `news_start`   | news       | G6 + A6 + D7 sus2, brief alert                          | ~1.0 s   | 0.28       |
| `radio_start`  | radio      | C6 → G6 → C7 ascending FM + Risset accent on C7 apex    | ~1.6 s   | 0.30       |
| `search_start` | search     | E7 single FM pluck + Risset shimmer, drier "quick ping" | ~0.8 s   | 0.20       |

All sounds normalize to the cue level, **-19 LUFS momentary max** (about -23 LUFS integrated, 3 dB under the voice; see [Loudness](#loudness)), under a -1.5 dBFS true-peak ceiling, deterministically (no Limiter: pedalboard's Limiter has a hard clipper at 0 dBFS, so the chain ends with a manual loudness normalize instead). Until T1.47 they were peak-normalized to -3 dBFS, which left them -15 to -20 LUFS: louder than the voice by up to 12 dB.

**Vocal-band note**: `book_end`, `news_start`, and `search_start` play immediately before LLM speech, so their named fundamentals sit above 1.5 kHz to clear the 200 Hz – 4 kHz vocal band. The Risset bell's canonical 0.56× partial sits below the named fundamental (e.g. 0.56 × G6 = 878 Hz), so absolute spectral energy in the vocal band isn't zero — but the chime's reverb tail decays well below threshold by the time the audiobooks skill's 500 ms trailing silence + the model's first-token latency elapse, so masking is mitigated in practice.

## The synthesis pipeline

Pure Python. Three primitives layered through one shared post-processing chain.

### Toolchain

`numpy` (oscillators, envelopes, mixing) + `scipy` (WAV I/O) + `pedalboard` (Spotify's effects host — Freeverb-derivative reverb, compressor, highpass). Declared as the `synth` dependency group in [`server/runtime/pyproject.toml`](../server/runtime/pyproject.toml). The runtime never imports any of them — the rendered WAVs are committed and the dep group is opt-in via `--group synth`.

### Synthesis primitives

- **`risset_bell(freq, duration_s, brightness)`** — Jean-Claude Risset's 11-partial additive bell (1969). Inharmonic frequency multipliers (0.56, 0.92, 1.19, 1.70, 2.0, 2.74, 3.0, 3.76, 4.07) with paired detuned partials at 0.56 and 0.92 — the slow beating between the detuned pairs is the characteristic shimmer that gives bells their organic feel. Per-partial decay times decrease with frequency: high partials die first, leaving a warm low-mid hum. This is the **body** of every chime.
- **`fm_bell(freq, duration_s, mod_ratio=1.4, mod_index_peak=6.0)`** — Chowning frequency modulation (1973). Carrier-modulator pair at non-integer ratio: 1:1.4 produces an inharmonic spectrum (bell, glass), 1:integer would produce a harmonic spectrum (organ, voice). The modulation index decays exponentially **faster** than the amplitude, so the bell brightens at strike and dulls as it rings — same dynamic an acoustic bell has. This is the **shimmer** layer.
- **`adsr` / `exp_decay`** — curved (exponential) envelopes only. Linear envelopes sound robotic; the `1 − e⁻ᵏˣ` shape matches how acoustic instruments behave.
- **`chord([voices], stagger_ms)`** — sums voices with optional inter-voice stagger (a 60–80 ms stagger reads as a single chime with subtle arpeggio rather than two crashes).
- **`mix(*(gain, signal))`** — gain-mixes signals of different lengths (zero-pads the shorter), used to layer body + shimmer of different durations.

### Post-processing chain

`HighpassFilter(80 Hz)` → `Reverb(room_size, damping, wet/dry)` → `Compressor(−18 dB, 2.5:1)` → normalize to -19 LUFS momentary max, measured with `huxley.loudness` on the PCM16 that gets written (if a peak would then pass -1.5 dBTP or the wire limiter's -3 dBFS, the sound is scaled down to fit and the script says so).

Notes on level: pedalboard's `Limiter` is documented as "two compressors and a hard clipper at 0 dB" — its `threshold_db` controls where compression starts, not where the ceiling sits. Using it as a brick-wall produces clipping artifacts. The pipeline drops the limiter and normalizes deterministically after the chain instead.

The reverb defaults (`room_size=0.85`, `damping=0.4`, `wet_level=0.30`, `dry_level=0.75`, pre-delay implicit in Freeverb's network) target a small concert hall: ~1.8 s RT60, audibly wet without smearing the transient. Damping rolls off the high frequencies in the tail, mimicking how real rooms eat the brightness of bells.

### Aesthetic constraints (per [`research/sonic-ux.md`](./research/sonic-ux.md))

- **Pitch register**: chimes that play immediately before model voice (`book_end`, `news_start`, `search_start`) sit above 1.5 kHz so their partials don't mask the 200 Hz – 4 kHz vocal band. Welcome chimes (`book_start`, `radio_start`) play before content audio (book / radio stream), not before model voice, so they can occupy C6 (1047 Hz) and feel warmer.
- **Voicings**: major intervals + sus chords only. No minor, no tritone, no semitone. The soft-Japanese / Wii / Totaka tradition is unambiguously pleasant.
- **Envelopes**: 3–15 ms curved attack (instant attack = click; linear ramp = stock asset), exponential decay 200–800 ms, no sustain stage (bells don't sustain).
- **Reverb is non-negotiable**: 25–35 % wet, hall character. Without it, even the cleanest synthesis sounds like Windows 95.

### Adding a new earcon

1. Open [`scripts/synth_sounds.py`](../scripts/synth_sounds.py) and add a function returning `F32`:

   ```python
   def my_chime() -> F32:
       body = chord(
           [
               risset_bell(note("G6"), duration_s=0.7, brightness=0.85),
               risset_bell(note("D7"), duration_s=0.7, brightness=0.85),
           ],
           stagger_ms=0.0,
       )
       shimmer = fm_bell(note("D7"), duration_s=0.6, mod_ratio=1.4, mod_index_peak=3.0)
       return post(mix((0.7, body), (0.35, shimmer)), reverb_wet=0.28)
   ```

2. Add `"my_chime": my_chime` to the `PALETTE` dict at the bottom of the script.
3. Run the renderer:

   ```bash
   uv run --package huxley --group synth python scripts/synth_sounds.py
   ```

4. Update the palette table in this doc.
5. Reference the role from a skill's persona config (`start_sound: my_chime` or hardcoded in a skill's `_KNOWN_SOUND_ROLES`).

### Adding silence padding after a chime

Some chimes lead directly into model speech (book_end → "the book just finished" narration). To prevent an abrupt chime → voice transition:

- The stream factory appends **500 ms of silence** after the earcon (before the stream completes).
- This covers the typical model first-token latency (200–800 ms) so the transition feels deliberate.

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

When the stream ends naturally (not cancelled), the coordinator sends `on_complete_prompt` as a user-role conversation item and triggers a model response. The coordinator must (a) skip the prompt if a PTT-induced cancel raced the trailing silence or there is no voice session (a reconnect: the turn would never end), and (b) create a synthetic IN_RESPONSE turn so the incoming model reply (deltas, tool calls, response_done) is handled by the existing turn-aware paths instead of being silently dropped:

```python
# server/runtime/src/huxley/turn/coordinator.py

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
# server/runtime/src/huxley/voice/openai_realtime.py

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

Each skill that uses sounds reads its palette directory from a per-skill `sounds_path` config key. Abuelo points all four sound-using skills (`audiobooks`, `news`, `radio`, `search`) at the shared palette:

```yaml
skills:
  audiobooks:
    library: audiobooks
    ffmpeg: ffmpeg
    ffprobe: ffprobe
    sounds_path: ../../_shared/sounds # framework-shared palette by default
    sounds_enabled: true # master toggle — set false to silence everything
    silence_ms: 500 # silence after firing request_response
    on_complete_prompt: | # localized per-persona
      El libro ha llegado a su fin. Felicita al usuario...

  news:
    sounds_path: ../../_shared/sounds
    start_sound: news_start # role name; matches `<role>.wav` in sounds_path
```

`sounds_path` is resolved relative to the persona's `data_dir` (so `../../_shared/sounds` from Abuelo lands at `server/personas/_shared/sounds/`). Absolute paths are honored as-is. To override the shared palette for a single persona, point `sounds_path` at a per-persona directory and drop your own `<role>.wav` files there — the SDK loader takes one directory at a time, so an override replaces the whole palette for that skill (no per-sound overlay yet).

If `sounds_enabled: false` (audiobooks only), the skill loads no palette and `silence_ms` is forced to 0 — the persona behaves as if no sounds were configured.

The shared loader lives in the SDK:

```python
# huxley_sdk.audio
def load_pcm_palette(directory: Path, roles: Iterable[str]) -> dict[str, bytes]:
    """Load PCM16/24kHz/mono WAVs at <directory>/<role>.wav for each role."""
```

It opens each WAV with the standard `wave` module, validates channels/rate/sample-width, and returns `{role: raw_pcm_bytes}`. Wrong-format files, missing files, and unreadable files are silently skipped — the caller decides what to do with a partial palette (audiobooks logs a warning, news/radio/search log a warning, all keep running without that earcon).

If `sounds_path` doesn't exist or has no matching `.wav` files, the skill runs silently — no errors. Earcons are optional enhancements; correctness never depends on them.

---

## Three-layer architecture (current and future)

### Layer 1 — Inline PCM injection (this stage)

Sounds that are tightly coupled to a specific audio stream: book_start and book_end. Lives inside the stream factory. These are the sounds we build now.

**Trade-off**: The book_start earcon yields before any positioning logic can fail. If `player.stream()` raises on the first chunk, the user has already heard the start chime. Acceptable: the error handler will produce a spoken error message anyway.

### Layer 2 — `PlaySound(SideEffect)` ✅

Shipped 2026-04-18 alongside the news skill (the second skill that needed a chime). The framework-level primitive:

```python
@dataclass(frozen=True, slots=True)
class PlaySound(SideEffect):
    kind: ClassVar[str] = "play_sound"
    pcm: bytes  # raw PCM16 24kHz mono
```

How the coordinator wires it: when a tool call returns `ToolResult(side_effect=PlaySound(pcm))`, the coordinator latches the bytes on the current Turn (`pending_play_sound`) and marks `needs_follow_up = True`. After `on_response_done` fires `request_response()` for the follow-up round, the chime PCM is sent via `_send_audio` immediately — landing on the WebSocket ahead of the model's audio deltas (FIFO ordering). User hears: chime → model voice. Mutually exclusive with `AudioStream` on a given `ToolResult` (`side_effect` is one field). Cleared on interrupt; skipped silently if `response_cancelled` flips before dispatch.

The shared WAV-loading helper landed alongside it as `huxley_sdk.audio.load_pcm_palette(directory, roles)` — used by both audiobooks and news.

Used by: news skill (`news_start` chime, persona-opt-in via `start_sound` config).

### Layer 3 — Framework-level state machine sounds (later)

Session-connect chime, disconnect tone, "thinking" audio. These aren't tied to any skill — they come from the framework. Will need a separate `SoundRegistry` or config block at the persona level. Far future.

---

## Client-side thinking tone (Stage D)

The current thinking tone (`clients/pwa/src/routes/+page.svelte`) generates a 440Hz sine wave on the client. For Abuelo:

1. **Frequency**: 440Hz sits in the 200Hz–4kHz vocal band. Correct target: below 200Hz (a low drone, like an old telephone hold tone — non-intrusive, clearly non-speech).
2. **Silence timeout**: currently 400ms. For an elderly blind user expecting responses, 1500ms is a better threshold before the tone starts — long enough that normal LLM latency doesn't trigger it, short enough that actual silence gets flagged.
3. **Error tone**: currently no distinct error audio. When `state: IDLE` after an error (OpenAI drops the session), a short descending two-tone chime signals the problem. The PWA plays it only if the session is still down 5 s later: the server refreshes the voice session every ~50 minutes (T1.35) and is back within ~3 s, which isn't worth an alarm over the music.

These are client changes only. They do not affect the server or the WebSocket protocol. Ship after the server-side earcon work is stable.

---

## Implementation stages

### Stage A — Synthesized earcon palette ✅

- [x] [`scripts/synth_sounds.py`](../scripts/synth_sounds.py) renders the full palette from layered Risset additive bells + Chowning FM bells through a hall reverb chain.
- [x] 5 earcons produced (book_start, book_end, news_start, radio_start, search_start) at PCM16 / 24 kHz / mono, peak −3 dBFS (since T1.47: -19 LUFS momentary max, see [Loudness](#loudness)).
- [x] Palette lives at `server/personas/_shared/sounds/`; Abuelo picks them up via `sounds_path: ../../_shared/sounds`.
- [x] Synthesis is deterministic and original — no third-party audio, no licensing concerns. The script is committed alongside the WAVs so any sound is reproducible from source.
- [x] Optional `synth` dependency group in `server/runtime/pyproject.toml` (`numpy`, `scipy`, `pedalboard`) — the runtime never imports them; the rendered WAVs are committed and shipped as-is.

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

### Stage D — Client-side thinking tone ✅ (superseded 2026-10-12 by the server's waiting cue, T1.41: see `turns.md` "The waiting cue")

- [x] `clients/pwa/src/lib/audio/playback.ts`: thinking tone frequency 440Hz → 120Hz (below the 200Hz–4kHz vocal band so it can't mask incoming speech)
- [x] `clients/pwa/src/lib/ws.svelte.ts`: `SILENCE_TIMEOUT_MS` 400ms → 1500ms (was over-triggering on every normal model first-token gap, teaching the user to ignore it)
- [x] `clients/pwa/src/lib/audio/playback.ts`: new `playErrorTone()` — descending 660Hz → 330Hz two-tone chime (~280ms total). Falling intervals universally read as "negative outcome" (Brewster).
- [x] `clients/pwa/src/routes/+page.svelte`: `$effect` watches state transitions; `(CONNECTING|CONVERSING) → IDLE` plays the error chime so a session drop is audibly distinguishable from a normal end.

`bun run check` clean. Browser-tested: hold PTT with no audio → 1.5s of silence → low drone fills until model speaks; kill the server mid-session → descending error chime fires.

### Open follow-up

- [x] Replace Wii BIOS earcons with original synthesized sounds — shipped 2026-04-29 via `scripts/synth_sounds.py`. The shared `_shared/sounds/` palette is 100% Huxley-original, no third-party samples.
- [x] Extract WAV-loading + PCM-injection into a framework `PlaySound` primitive — shipped 2026-04-18 with news skill (`huxley_sdk.audio.load_pcm_palette`, `huxley_sdk.PlaySound`).
- [ ] Per-sound persona overlay (let a persona override one earcon without supplying the whole palette). Today's `load_pcm_palette` takes one directory; a future enhancement would accept a fallback chain (per-persona dir → shared dir).

---

## Testing the earcons without running the full stack

Quick sanity check for any `.wav` file in the shared palette:

```bash
# Re-render the entire palette from source.
uv run --package huxley --group synth python scripts/synth_sounds.py

# Audition through the system speaker (macOS).
afplay server/personas/_shared/sounds/book_start.wav

# Confirm format (PCM16, 24 kHz, mono) — required for the WebSocket audio channel.
ffprobe -v error -show_streams server/personas/_shared/sounds/book_start.wav 2>&1 | \
  grep -E "codec_name|sample_rate|channels|bits_per_sample"

# Measure loudness: every chime sits at -19 LUFS momentary max (M), true peak under -1.5.
ffmpeg -hide_banner -nostats -i server/personas/_shared/sounds/book_start.wav \
  -af ebur128=peak=true -f null - 2>&1 | tail -12
```

---

## Failure modes and fallbacks

| Failure                                              | Behavior                                                                                              |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `sounds_path` doesn't exist                          | Skill runs silently — no earcons, no error                                                            |
| `.wav` file is wrong format                          | `load_pcm_palette` validates channels/rate/sample-width via `wave.open()` and skips the file silently |
| `on_complete_prompt` fails to trigger model response | Book ends silently; user may re-engage via PTT                                                        |
| Earcon yields but book stream immediately fails      | User hears start chime + model error narration — acceptable                                           |

The degradation path is intentional: earcons enhance UX but are not load-bearing for correctness. A deployment without sound files works; it just has silent transitions.
