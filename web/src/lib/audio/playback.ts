/**
 * Streaming PCM16 audio playback via Web Audio API.
 *
 * Receives base64-encoded PCM16 chunks (24 kHz, mono) from the server and
 * schedules them back-to-back for gapless playback.
 */
export class AudioPlayback {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private nextTime = 0;
  private activeSources: AudioBufferSourceNode[] = [];
  private idleCallbacks: Array<() => void> = [];
  private thinkingToneOsc: OscillatorNode | null = null;
  private thinkingToneGain: GainNode | null = null;
  private thinkingToneRefillTimer: ReturnType<typeof setTimeout> | null = null;

  get initialized(): boolean {
    return this.ctx !== null;
  }

  /**
   * Calls `cb` once when the playback buffer goes idle (activeSources reaches
   * zero). If the buffer is already idle, `cb` is called synchronously.
   * The callback is one-shot and removed after firing.
   */
  onceIdle(cb: () => void): void {
    if (this.activeSources.length === 0) {
      cb();
    } else {
      this.idleCallbacks.push(cb);
    }
  }

  private drainIdleCallbacks(): void {
    if (this.activeSources.length > 0) return;
    const cbs = this.idleCallbacks.splice(0);
    for (const cb of cbs) cb();
  }

  async init(): Promise<void> {
    if (this.ctx) return;
    this.ctx = new AudioContext({ sampleRate: 24000 });
    this.masterGain = this.ctx.createGain();
    this.masterGain.connect(this.ctx.destination);
    // Branch the master output to an analyser for the waveform visualizer.
    // The analyser is transparent — audio still reaches destination unchanged.
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256; // frequencyBinCount = 128
    this.analyser.smoothingTimeConstant = 0.75;
    this.masterGain.connect(this.analyser);
  }

  /**
   * Returns N normalised frequency bands (0..1) from the playback analyser.
   * Aggregates the raw 128-bin FFT into `n` evenly-sized buckets.
   * Returns zeros when the AudioContext hasn't been initialised yet.
   */
  getFrequencyData(n: number): number[] {
    if (!this.analyser) return new Array(n).fill(0) as number[];
    const buf = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteFrequencyData(buf);
    const step = Math.max(1, Math.floor(buf.length / n));
    const result: number[] = [];
    for (let i = 0; i < n; i++) {
      let sum = 0;
      const start = i * step;
      for (let j = 0; j < step; j++) sum += buf[start + j] ?? 0;
      result.push(sum / step / 255);
    }
    return result;
  }

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

  play(base64: string): void {
    if (!this.ctx) return;
    const float32 = pcm16Base64ToFloat32(base64);
    const buffer = this.ctx.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32 as Float32Array<ArrayBuffer>, 0);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.masterGain ?? this.ctx.destination);
    const now = this.ctx.currentTime;
    const start = Math.max(now + 0.01, this.nextTime);
    source.start(start);
    this.nextTime = start + buffer.duration;
    this.activeSources.push(source);
    source.onended = () => {
      const i = this.activeSources.indexOf(source);
      if (i >= 0) this.activeSources.splice(i, 1);
      this.drainIdleCallbacks();
    };
  }

  /** Rising 880 Hz ready-to-talk cue. */
  playTone(freq = 880, durationMs = 90): void {
    if (!this.ctx) return;
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const now = this.ctx.currentTime;
    const end = now + durationMs / 1000;
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.25, now + 0.005);
    gain.gain.setValueAtTime(0.25, end - 0.01);
    gain.gain.linearRampToValueAtTime(0, end);
    osc.connect(gain);
    gain.connect(this.masterGain ?? this.ctx.destination);
    osc.start(now);
    osc.stop(end + 0.02);
  }

  /** Descending two-tone error chime: 660 Hz -> 330 Hz. */
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
   * Thinking gap-filler tone: 120 Hz pulse, 150 ms on / 250 ms off.
   * Below vocal band so it doesn't mask incoming speech.
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

  private scheduleThinkingPulses(startTime: number): void {
    if (!this.ctx || !this.thinkingToneGain) return;
    const gain = this.thinkingToneGain.gain;
    const PULSE_ON = 0.15;
    const PULSE_OFF = 0.25;
    const PULSE_PERIOD = PULSE_ON + PULSE_OFF;
    const ATTACK = 0.02;
    const RELEASE = 0.02;
    const PEAK = 0.15;
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
      /* already stopped */
    }
    this.thinkingToneOsc = null;
    this.thinkingToneGain = null;
  }

  stop(): void {
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        /* already ended */
      }
    }
    this.activeSources = [];
    this.nextTime = 0;
    this.stopThinkingTone();
    this.drainIdleCallbacks();
  }

  destroy(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
    this.masterGain = null;
  }
}

function pcm16Base64ToFloat32(base64: string): Float32Array {
  const binary = atob(base64);
  const buf = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const int16 = new Int16Array(buf);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = (int16[i] ?? 0) / 32768;
  }
  return float32;
}
