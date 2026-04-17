# Sonic UX for Voice Agents — research notes

Working notes from research into sonic UI / auditory feedback design for voice agents, focused on what's codified vs. trade-craft. Synthesized into a concrete framework for the AbuelOS persona at the bottom.

This is reference material, not framework documentation — it informs design decisions when the time comes to add an audio-cue layer on top of the LLM voice.

## Field map

The umbrella term is **auditory display**, academic home is **ICAD** (International Conference on Auditory Display, running since 1992; proceedings open-access at Georgia Tech SMARTech). The sub-discipline you actually want is **sonic interaction design** — feedback during interaction, as opposed to data sonification.

Three foundational sound-type categories from the Sumikawa → Brewster lineage:

- **Auditory icons** (Gaver, 1986): real-world sounds with intuitive mapping (paper crumple = delete). Learnable in seconds. Use when a natural metaphor exists.
- **Earcons** (Blattner, 1989): abstract musical motifs. Require learning. Use when no metaphor exists or you need a hierarchical system (parent earcon + variation = child).
- **Spearcons** (sped-up speech, e.g., "inbox" compressed to ~0.2s). Brewster/Walker: as learnable as speech, far better than earcons.

Single most useful 25 pages: **The Sonification Handbook, Chapter 14 (Brewster & McGookin)** — free PDF at sonification.de.

## Reference points

- **Jim Reekes** (Mac startup chime): C-major chord on a Korg Wavestation, deliberately _unresolved_ — calm because Macs used to crash on him. Primary source: reekes.net.
- **Kazumi Totaka** (Wii system audio): warm, restrained, longer sounds (~4s loops, not bleeps). Listen to the Wii System Soundtrack on Internet Archive once before designing — tonal reference.
- **"Her" (2013)**: almost no earcons. The lesson is **restraint** — voice does the work; reserve sounds for moments that genuinely need a non-verbal signal.

## Honest assessment of the field

About **60% of this field is codified, 40% is trade-craft oral tradition.**

**Codified**:

- The academic earcon literature (Brewster, Sumikawa, ICAD)
- The audio-storytelling craft tradition (NPR, This American Life)
- Game audio middleware conventions (ducking attack/release values, vertical layering)

**Vibes-driven oral tradition**:

- Voice-assistant earcon catalogues from Amazon, Apple, Microsoft (no real published frameworks beyond single rules)
- Sonic-branding agency methodologies (process documents wrap pure taste)
- Game-show buzzer conventions (passed editor-to-editor on the job)

**"Sound design for AI agents" as a published 2026 discipline does not really exist yet.** Most current writing is about _vocal_ design (cloning, prosody). The non-verbal layer is wide open. Slightly ahead of the curve.

The richer literature for AbuelOS specifically is **accessibility research for blind users** (minimal feedback to avoid masking natural sounds, spatial audio for information density), not voice-AI design blogs.

## The codified rules worth knowing

### NPR / This American Life music rules

From Jonathan Menjivar (TAL) on Transom, NPR Training "Score! Best practices":

1. **Music is a flashlight, not mood lighting.** If you can't articulate why a cue is at this exact moment, cut it.
2. **Don't start music at the beginning of momentum** — start ~20–30s in. Starting too early wastes the build.
3. **Pull music _before_ a revelation**, not over it. Silence makes the revelation hit.
4. **Start music _after_ the punchline.** Music before a joke telegraphs it.
5. **Abrupt starts beat fades.** Decisive entry, decisive exit.
6. **Rest equals music.** If a cue ran 60s, the next ~60s should be silent.
7. **Leitmotif for recurring concepts** — same cue for same character/idea reduces cognitive load.
8. **No vocals or trumpets under speech** — they share frequencies with the human voice and create masking.
9. **Set the expectation early** — listeners need to be told within ~30s that this is a scored piece.
10. **Let music breathe in the clear for 5–10s** — gives the listener a beat to process.
11. **Ducking math**: dialogue ducks background ~10–15dB; gentler sounds 3–6dB. Attack 50–100ms, release 500ms–2s.

### Brewster's earcon construction rules

Lab-derived, authoritative (Brewster TOCHI '98, HCI '95):

- **Use musical timbres with multiple harmonics** — not pure tones (mask easily, fatigue).
- **Never use pitch alone for differentiation** — combine with rhythm or timbre.
- **Pitch range: 125Hz–5kHz**.
- **Notes ≥0.0825s**; up to 6 notes/sec; accent first note, extend last.
- **Intensity range: 10–20dB above background. Never use intensity alone to differentiate.**
- **Between sequential earcons, insert ≥0.1s gap.**
- **Two-to-three-octave separation** for absolute (not relative) pitch judgment.

### Karen Collins — diegesis × interactivity (Playing With Sound, MIT Press)

Every game sound sits on a 2D plane: (a) does it exist in the world the character inhabits? (b) did the player's input cause it? Decision rule:

- **Diegetic + caused-by-player** = action confirmation (footstep, gunshot). Always plays, varies slightly to avoid fatigue.
- **Non-diegetic + caused-by-player** = UI feedback (button click). Short, distinct, consistent — Brewster's rules apply.
- **Non-diegetic + system-driven** = music/score. Use TAL rules.
- **Diegetic + system-driven** = ambience. Always-on bed, never demands attention.

### Wwise/FMOD ducking conventions

Same numbers as NPR independently: **50–100ms attack, 500ms–2s release, 10–15dB duck for critical voice.** The cross-field convergence is the closest thing to a universal rule.

### NN/g — three-category VUI decision tree

Ranked by information density:

1. **Nonverbal sounds (earcons)** — narrow, repetitive contexts only
2. **Implicit verbal cues** ("sound good?") — for reversible actions
3. **Explicit verbal signifiers** — required for irreversible actions

**The rule: default to verbal; use earcons only where the verbal signifier would be more annoying than the earcon's learning cost.**

### Cognitive cost (when NOT to play)

- **Cognitive load suppresses auditory processing** (Frontiers, 2016) — under high load, users miss the sound and the sound also degrades the primary task. Don't play sounds when the user is mid-task on something else.
- **Auditory salience = loudness × spectral contrast × temporal contrast** — if your earcon shares a frequency with the speech that follows, it'll mask or be masked.
- **300ms is the conversational silence threshold** — below that, a thinking tone is unnecessary. Above ~2s, silence reads as broken. Filler tones live in the 300ms–2s window.
- **Two-strikes rule from broadcast**: if the same listener hears the same earcon twice in a session and didn't need it the first time, it's noise, not signal.

### Voice assistants — what's actually published

Surprisingly thin. The major ones converged on (observable, not documented):

- Wake confirmation tone (always — required for usability)
- End-of-turn listening tone (Google, Siri)
- Error/can't-help tone (Alexa "rejected")
- **Nothing else.** No tool-running tone, no thinking tone, no success/failure earcons in normal flow.

Google's one load-bearing rule: **"If you have to teach users what an earcon means, don't use one."**

## Synthesized framework for AbuelOS

Translating the above for a Spanish-speaking elderly blind user. The constraint that drives everything: **his only modality is sound, so every sound either carries information or steals from speech.** No visual fallback for a misfired tone, no quiet ambient channel — every cue plays into the same channel that carries his agent's voice.

Rules in order of confidence:

1. **Default to spoken words. Earcons are exceptions, not the system.** For a blind user with no learned visual vocabulary, an earcon must earn its place against "the persona could just say it."
2. **An earcon is justified only when the spoken alternative would be more annoying than the earcon's learning cost.** State transitions that happen 20+ times per session (start/end of listening) clear the bar. Tool-call confirmations don't.
3. **Three earcons, maximum, total.** Brewster: too many overwhelms. For a 90-year-old learning a new system, three is the ceiling.
4. **The three slots: (a) "I'm listening to you," (b) "I'm working on it" (only if latency >1.5s), (c) "Something broke."** Maps to universal voice-assistant convention plus an audio surface for failure.
5. **Wake/listening tone is highest priority.** Without it, a blind user can't tell if PTT engaged. Don't innovate — match Siri/Alexa convention.
6. **No thinking tone under ~1.5s latency.** Adding one trains the user to expect it; its absence on a fast turn then reads as a bug.
7. **The thinking tone must be a low-pitched bed, not a melodic earcon.** It has to coexist with anticipation of speech. Trumpets/vocals mask; low pads don't. Drone, not chime.
8. **Failure tone is descending, brief (~0.3s), always followed by spoken explanation.** Descending = negative is the one cross-cultural musical convention safe to lean on. Spoken explanation does the actual work.
9. **No success tone.** Success is communicated by the agent doing the thing he asked for. An additional "ding" steals attention budget.
10. **Duck the thinking bed by 12dB the moment speech begins, attack ≤50ms.** Standard NPR/Wwise number. He must never strain to hear the persona over an earcon.
11. **Earcons must not occupy the 200Hz–4kHz vocal band.** Either below 200Hz (sub-pad) or above 4kHz (chime) — anything in the speech band masks or gets masked by his TTS voice.
12. **Same earcon = same meaning, forever.** Brewster/Google consistency rule. Never seasonal, never contextual variants. Three sounds learned in a week if stable; zero if they drift.
13. **If a session ever ends with "I didn't know what was happening," the answer is more _spoken_ feedback, not more earcons.** AbuelOS-specific corollary to the logging-first principle: failures get _language_, not tones.
14. **Test exactly one variant at a time, with the actual user.** Sonic-branding research universally fails to predict elderly-blind-user reception. The frameworks narrow the search space from "infinite" to "three earcons in specific frequency bands at specific moments." User testing picks the survivor.

The reason "test with users" remains the answer despite all of the above: every framework cited assumes a sighted, attentive, headphone-wearing adult — none have data on a 90-year-old blind Spanish speaker in a noisy room with a smart speaker. The frameworks narrow the search space; user testing picks the survivor.

## Current state vs. target framework

What already exists in the codebase (as of 2026-04-17):

- **Ready-to-talk tone** — `AudioPlayback.playTone(880Hz, 90ms)` in `web/src/lib/audio/playback.ts`. Fires on PTT press to confirm mic is hot. 880Hz is above the vocal band. **Framework-compliant.**
- **Thinking tone** — `AudioPlayback.playThinkingTone()` same file. 440Hz sine pulse at 150ms on / 250ms off. **Violates framework rules 7 and 11:** 440Hz is inside the vocal band (200Hz–4kHz), so it masks/gets-masked by TTS; and it's a melodic pulse rather than a drone bed.
- **Error tone** — does not exist. Failures surface only as spoken text or silent drops. Violates framework rule 8.
- **Silence threshold** — `SILENCE_TIMEOUT_MS = 400` in `web/src/lib/ws.svelte.ts`. **Violates framework rule 6:** should be ~1500 ms. The 400 ms value was chosen before this research to match "dead air reads as broken device" intuition — the research says that intuition over-triggers the tone.

Concrete deltas when this work is picked up again:

1. Redesign `playThinkingTone()`: low-pitched drone bed (pick one: a ~80–120Hz sub-sine with slow amplitude modulation, OR a >4kHz airy pad — the research leans toward the sub).
2. Raise `SILENCE_TIMEOUT_MS` to 1500.
3. Add `playErrorTone()`: descending ~300ms sweep (e.g., C5 → G4 over the duration), followed by the model's spoken explanation. Requires a new server→client signal (`error` or a `dev_event` kind the client listens for).
4. Duck the thinking bed by 12dB attack ≤50ms the moment `audio` arrives (today it hard-stops — replace with a fade).
5. Keep the PTT ready tone as-is. Don't add a success tone (framework rule 9).

The above is a ~1-day wiring change plus however long it takes to choose three sounds. The wiring is easy; the sound choices need a human ear.

## Where to start your own research (pick 3, in this order)

You said you know nothing about this. Here's a curated path that builds the right ear in a weekend, not an entire discipline.

### Listen first (30 minutes, free)

1. **[Wii System Soundtrack](https://archive.org/details/wii-system-soundtrack-flac)** on Internet Archive. Play 5 minutes at low volume while you work. This is the tonal reference for "warm, restrained, not bleeps." Your thinking bed should sit in this world, not in the iPhone/Alexa world.
2. **Back-to-back comparison**: on YouTube, search for "Siri listening tone," "Alexa wake tone," and "Google Assistant listening tone" — listen to all three in one sitting. They converged on the same shape despite competing companies. That convergence is the signal.
3. **Browse Freesound with the framework in mind**: go to [freesound.org](https://freesound.org), filter to "Creative Commons 0," search for `ui` or `notification` or `interface`. Listen to 20 sounds. For each, ask: is it in the vocal band (will mask speech)? Is it melodic (would tire on the 200th hearing)? Is it obviously what it means (Google's rule)? You'll develop the filter in an evening.

### Read one thing (20 minutes, free)

[**NN/g — Audio Signifiers for Voice Interaction**](https://www.nngroup.com/articles/audio-signifiers-voice-interaction/). Single best intro piece for VUI specifically. Gives you the **verbal-vs-earcon decision rule** cold: default to spoken language; use earcons only where the verbal alternative would be more annoying than the earcon's learning cost. That rule alone will kill 80% of bad ideas before you build them.

Optional second read if you want the craft rules: [**Transom — Jonathan Menjivar (This American Life) on using music**](https://transom.org/2015/using-music-jonathan-menjivar-for-this-american-life/). The "music is a flashlight, not mood lighting" piece. 10 minutes. Shifts you from "more sounds = better" to "every sound must earn its place."

### Watch / listen one thing (1 hour)

[**That Real Blind Tech Show — interview with Jim Reekes**](https://blindtechshow.com/episode-27-a-conversation-with-jim-reekes-about-the-mac-start-up-chime-let-it-beep-sosumi-and-good-audio-design/). Reekes designed the Mac startup chime. The interviewer is blind. You'll hear the craft philosophy from the person who defined Apple's audio aesthetic, _through the ears of your actual user demographic_. Single highest-leverage hour you can spend.

### Do one thing (1 hour, hands-on)

Open Freesound. Find **three candidate sounds** with the framework in your head:

- **A listening-start chirp**: short (<150ms), above 4kHz or clearly un-voice-like (e.g., a muted bell, a soft click, a rising sine sweep).
- **A thinking bed**: 3–5 seconds of low drone that loops cleanly. Below 200Hz or airy/wind-like above 4kHz. Must sit _under_ speech without competing.
- **An error tone**: descending 200–400ms. Not harsh — think "oh no" soft, not "ERROR" harsh.

Download them as WAV. Stop there. Don't wire them up; just bring them and we'll A/B against the current implementation. The point is for you to hear whether you can pick three sounds that feel right — if yes, the curated-from-Freesound path works. If you can't find anything that feels right after an hour, that's signal that you want a freelance sound designer instead.

### When you come back

Re-read the "Synthesized framework for AbuelOS" section above, then the "Current state vs. target framework" section. Those two together tell you exactly what to change in the code.

## Practical implementation

**Python**: `sounddevice` + numpy for triggered playback. `pygame.mixer` if you want overlapping sounds with fades (e.g., a thinking bed under speech). `pyo` if you ever go procedural. Skip SuperCollider/PD — overkill.

**Sound sources**: freesound.org (CC0 filter), pixabay.com/sound-effects, Apple Loops if you have GarageBand. There is no curated "voice-assistant earcon pack" — that market hasn't materialized. Build the set by curating from Freesound.

**Three realistic paths in ascending craft**:

1. **Weekend, $0**: curate 3 sounds from Freesound (per the framework above — no more), single tonal palette (warm Wii-like, not iPhone-sharp). Wire via `pygame.mixer` for the bed, `sounddevice` for triggers.
2. **Freelance designer, $500–$1500**: brief them with the framework above. Source from r/gameaudio or Designing Sound Discord. Sweet spot for production quality.
3. **Procedural in numpy**: every sound generated, varies subtly per invocation. Most "Her" answer technically. ~50 lines + coefficient table. Weeks of work. Only after a v1 ships and you know what works.

## Sources

Codified rules:

- [Brewster earcon guidelines](https://www.dcs.gla.ac.uk/~stephen/earcon_guidelines.shtml)
- [Brewster, Experimentally Derived Guidelines for Earcons (HCI '95)](https://www.dcs.gla.ac.uk/~stephen/papers/HCI95.pdf)
- [Brewster, TOCHI '98 — Using Non-Speech Sounds for Navigation Cues](https://www.dcs.gla.ac.uk/~stephen/papers/TOCHI98.pdf)
- [Dingler, Lindsay & Walker — Auditory icons, earcons, spearcons (ICAD 2008)](https://www.icad.org/Proceedings/2008/DinglerLindsay2008.pdf)
- [Hermann — Taxonomy and Definitions for Sonification (ICAD 2008)](https://www.icad.org/Proceedings/2008/Hermann2008.pdf)
- [The Sonification Handbook, Ch. 14 (Brewster & McGookin)](https://sonification.de/handbook/download/TheSonificationHandbook-chapter14.pdf)
- [NPR Training — Score! Best practices for music in audio storytelling](https://training.npr.org/2016/07/05/score-best-practices-for-using-music-in-audio-storytelling/)
- [Transom — Jonathan Menjivar / This American Life on using music](https://transom.org/2015/using-music-jonathan-menjivar-for-this-american-life/)
- [Karen Collins, _Playing with Sound_ (MIT Press)](https://direct.mit.edu/books/monograph/3725/Playing-with-SoundA-Theory-of-Interacting-with)
- [Wwise/FMOD ducking conventions](https://www.thegameaudioco.com/wwise-or-fmod-a-guide-to-choosing-the-right-audio-tool-for-every-game-developer)
- [NN/g — Audio Signifiers for Voice Interaction](https://www.nngroup.com/articles/audio-signifiers-voice-interaction/)
- [Cognitive load and auditory processing — Frontiers in Human Neuroscience](https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2016.00221/full)

Voice assistants (thin):

- [Google Conversation Design — Earcons](https://developers.google.com/assistant/conversation-design/earcons)
- [Amazon Alexa — Sonic Branding and Earcons](https://developer.amazon.com/en-US/blogs/alexa/post/a466dbf7-d9d0-463f-99da-6e632f5352e9/hear-it-from-a-skill-builder-how-to-make-your-skill-stand-out-with-sonic-branding-and-earcon.html)
- [Apple HIG — VoiceOver](https://developer.apple.com/design/human-interface-guidelines/voiceover)

Reference aesthetics:

- [Jim Reekes — Mac startup chime origin](https://reekes.net/sosumi-story-mac-startup-sound/)
- [That Real Blind Tech Show — Reekes interview](https://blindtechshow.com/episode-27-a-conversation-with-jim-reekes-about-the-mac-start-up-chime-let-it-beep-sosumi-and-good-audio-design/)
- [Wii System Soundtrack archive](https://archive.org/details/wii-system-soundtrack-flac)
- [On the Off-Screen Voice — Sound and Vision in Spike Jonze's Her](https://www.academia.edu/25921942/On_the_Off_screen_Voice_Sound_and_Vision_in_Spike_Jonzes_Her)

Tools:

- [python-sounddevice](https://python-sounddevice.readthedocs.io/)
- [bfxr](https://www.bfxr.net/), [ChipTone](https://sfbgames.itch.io/chiptone), [jsfxr](https://sfxr.me/) — procedural placeholder generators
- [Freesound.org](https://freesound.org/), [Pixabay sound effects](https://pixabay.com/sound-effects/)
