/**
 * Streaming PCM16 audio playback via Web Audio API.
 *
 * Receives base64-encoded PCM16 chunks (24 kHz, mono) from the server and
 * schedules them back-to-back for gapless playback.
 *
 * Call init() once before using (ideally inside a user-gesture handler so
 * the AudioContext starts in a running state on Safari).
 * Call stop() to immediately cancel queued audio (e.g. user interrupts).
 */
export class AudioPlayback {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private nextTime = 0;
  private activeSources: AudioBufferSourceNode[] = [];
  // Thinking-tone state — held so it can be stopped cleanly.
  private thinkingToneOsc: OscillatorNode | null = null;
  private thinkingToneGain: GainNode | null = null;
  private thinkingToneRefillTimer: ReturnType<typeof setTimeout> | null = null;

  get initialized(): boolean {
    return this.ctx !== null;
  }

  /**
   * Create the AudioContext. Idempotent.
   * Call inside a user-gesture handler on first use.
   */
  async init(): Promise<void> {
    if (this.ctx) return;
    this.ctx = new AudioContext({ sampleRate: 24000 });
    this.masterGain = this.ctx.createGain();
    this.masterGain.connect(this.ctx.destination);
  }

  /**
   * Set master output volume. Level is 0-100 (matching the server's scale).
   * Applies immediately to all future and currently-scheduled audio.
   */
  setVolume(level: number): void {
    if (this.masterGain) {
      this.masterGain.gain.value = Math.max(0, Math.min(100, level)) / 100;
    }
  }

  async resume(): Promise<void> {
    if (!this.ctx) await this.init();
    if (this.ctx!.state === "suspended") {
      await this.ctx!.resume();
    }
  }

  /**
   * Decode and schedule a base64-encoded PCM16 chunk for playback.
   * Chunks are queued gaplessly using a monotonic time cursor.
   */
  play(base64: string): void {
    if (!this.ctx) return;

    const float32 = pcm16Base64ToFloat32(base64);
    const buffer = this.ctx.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32, 0);

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.masterGain ?? this.ctx.destination);

    // Schedule gaplessly: start no earlier than now + small jitter margin
    const now = this.ctx.currentTime;
    const start = Math.max(now + 0.01, this.nextTime);
    source.start(start);
    this.nextTime = start + buffer.duration;

    this.activeSources.push(source);
    source.onended = () => {
      const i = this.activeSources.indexOf(source);
      if (i >= 0) this.activeSources.splice(i, 1);
    };
  }

  /**
   * Play a short sine-wave tone as a user-facing cue.
   *
   * Used as the "ready to talk" cue when the single button consolidates
   * session-start + PTT — the hardware walky-talky metaphor wants an audible
   * beep the moment the mic actually goes live. Grandpa is blind, so the
   * button's "you can talk now" must be audible, not visual.
   */
  playTone(freq = 880, durationMs = 90): void {
    if (!this.ctx) return;
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const now = this.ctx.currentTime;
    const end = now + durationMs / 1000;
    // Short fade in/out avoids audible clicks at the start/stop edges.
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.25, now + 0.005);
    gain.gain.setValueAtTime(0.25, end - 0.01);
    gain.gain.linearRampToValueAtTime(0, end);
    osc.connect(gain);
    gain.connect(this.masterGain ?? this.ctx.destination);
    osc.start(now);
    osc.stop(end + 0.02);
  }

  /**
   * Descending two-tone error chime: 660 Hz → 330 Hz, ~280 ms total.
   *
   * Falling intervals are universally read as "negative outcome" (Brewster).
   * Used when the session drops to IDLE after an error, so a blind user can
   * tell the device hit a problem rather than just "stopped responding."
   * Distinct from the rising 880 Hz ready-tone so the two can't be confused.
   */
  playErrorTone(): void {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const beepDur = 0.12;
    const gap = 0.04;
    const dest = this.masterGain ?? this.ctx.destination;
    const beep = (start: number, freq: number) => {
      const osc = this.ctx!.createOscillator();
      const g = this.ctx!.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      g.gain.setValueAtTime(0, start);
      g.gain.linearRampToValueAtTime(0.22, start + 0.005);
      g.gain.setValueAtTime(0.22, start + beepDur - 0.02);
      g.gain.linearRampToValueAtTime(0, start + beepDur);
      osc.connect(g);
      g.connect(dest);
      osc.start(start);
      osc.stop(start + beepDur + 0.02);
    };
    beep(now, 660);
    beep(now + beepDur + gap, 330);
  }

  /**
   * Start the "thinking" gap-filler tone.
   *
   * 120 Hz sine pulse — deliberately BELOW the 200 Hz–4 kHz vocal band so
   * it doesn't mask incoming model speech (the earlier 440 Hz sat squarely
   * in the speech band, per the sonic-UX research in docs/research/sonic-ux.md).
   * Pulses 150 ms on / 250 ms off, softer than the ready tone so it reads
   * as "low background hum, system thinking" rather than "alarm."
   *
   * Owned by the silence-detection timer in `ws.svelte.ts`, fires after
   * `SILENCE_TIMEOUT_MS` of dead air. Grandpa is blind — dead air past
   * the threshold is indistinguishable from a broken device.
   */
  playThinkingTone(): void {
    if (!this.ctx || this.thinkingToneOsc) return;

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 120;

    gain.gain.setValueAtTime(0, this.ctx.currentTime);
    osc.connect(gain);
    gain.connect(this.masterGain ?? this.ctx.destination);
    osc.start(this.ctx.currentTime);

    this.thinkingToneOsc = osc;
    this.thinkingToneGain = gain;
    this.scheduleThinkingPulses(this.ctx.currentTime);
  }

  /**
   * Schedule a batch of pulses on the AudioContext clock.
   *
   * Web Audio scheduling is sample-accurate — we precompute 10 pulses at a
   * time and re-arm a setTimeout to schedule the next batch ~500 ms before
   * the current batch ends. Drift-free, no setInterval polling.
   */
  private scheduleThinkingPulses(startTime: number): void {
    if (!this.ctx || !this.thinkingToneGain) return;
    const gain = this.thinkingToneGain.gain;
    const PULSE_ON = 0.15;
    const PULSE_OFF = 0.25;
    const PULSE_PERIOD = PULSE_ON + PULSE_OFF; // 0.40
    const ATTACK = 0.02;
    const RELEASE = 0.02;
    const PEAK = 0.15; // softer than the 880 Hz ready tone (0.25)
    const BATCH = 10;

    for (let i = 0; i < BATCH; i++) {
      const pulseStart = startTime + i * PULSE_PERIOD;
      gain.setValueAtTime(0, pulseStart);
      gain.linearRampToValueAtTime(PEAK, pulseStart + ATTACK);
      gain.setValueAtTime(PEAK, pulseStart + PULSE_ON - RELEASE);
      gain.linearRampToValueAtTime(0, pulseStart + PULSE_ON);
    }

    const batchEnd = startTime + BATCH * PULSE_PERIOD;
    const refillIn = (batchEnd - this.ctx.currentTime - 0.5) * 1000;
    this.thinkingToneRefillTimer = setTimeout(
      () => {
        this.thinkingToneRefillTimer = null;
        if (this.thinkingToneGain && this.ctx) {
          this.scheduleThinkingPulses(this.ctx.currentTime);
        }
      },
      Math.max(0, refillIn),
    );
  }

  /**
   * Stop the thinking tone with a short fade to avoid clicks.
   * Idempotent — safe to call when the tone isn't playing.
   */
  stopThinkingTone(): void {
    if (this.thinkingToneRefillTimer !== null) {
      clearTimeout(this.thinkingToneRefillTimer);
      this.thinkingToneRefillTimer = null;
    }
    if (!this.ctx || !this.thinkingToneOsc || !this.thinkingToneGain) return;

    const osc = this.thinkingToneOsc;
    const gain = this.thinkingToneGain.gain;
    const now = this.ctx.currentTime;
    gain.cancelScheduledValues(now);
    gain.setValueAtTime(gain.value, now);
    gain.linearRampToValueAtTime(0, now + 0.02);
    try {
      osc.stop(now + 0.03);
    } catch {
      // already stopped
    }
    this.thinkingToneOsc = null;
    this.thinkingToneGain = null;
  }

  /**
   * Immediately cancel all queued audio.
   * Call when the user interrupts the assistant mid-speech.
   */
  stop(): void {
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        // already ended — ignore
      }
    }
    this.activeSources = [];
    this.nextTime = 0;
    this.stopThinkingTone();
  }

  destroy(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
    this.masterGain = null;
  }
}

function pcm16Base64ToFloat32(base64: string): Float32Array<ArrayBuffer> {
  const binary = atob(base64);
  const buf = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const int16 = new Int16Array(buf);
  const float32 = new Float32Array(new ArrayBuffer(int16.length * 4));
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768;
  }
  return float32;
}
