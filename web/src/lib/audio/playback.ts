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
  private nextTime = 0;
  private activeSources: AudioBufferSourceNode[] = [];

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
    source.connect(this.ctx.destination);

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
    gain.connect(this.ctx.destination);
    osc.start(now);
    osc.stop(end + 0.02);
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
  }

  destroy(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
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
