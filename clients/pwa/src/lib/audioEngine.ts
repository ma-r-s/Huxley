// AudioEngine — drives the orb's visual animation only.
// Real mic capture (for huxley) goes through MicCapture in audio/capture.ts.
// Real audio playback goes through AudioPlayback in audio/playback.ts.
// This class provides amplitude data for the orb's canvas render loop.

export interface AudioReading {
  level: number; // 0..1
  bands: [number, number, number]; // [low, mid, high] 0..1
}

type SynthMode = "speak" | "think" | "listen" | null;

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  micActive = false;
  private synthMode: SynthMode = null;

  async ensure(): Promise<void> {
    if (this.ctx) return;
    try {
      this.ctx = new AudioContext();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.smoothingTimeConstant = 0.7;
    } catch {
      // no audio context — degrade gracefully
    }
  }

  // Connect an external MediaStream to the visual analyser (e.g. from MicCapture).
  // Called by the app so the orb reacts to the real mic without opening a second stream.
  connectStream(stream: MediaStream): void {
    if (!this.ctx || !this.analyser) return;
    const src = this.ctx.createMediaStreamSource(stream);
    src.connect(this.analyser);
    this.micActive = true;
  }

  startSynth(mode: SynthMode): void {
    this.synthMode = mode;
  }

  stopSynth(): void {
    this.synthMode = null;
  }

  // Returns amplitude data for the current frame.
  read(now: number): AudioReading {
    // Real mic via analyser
    if (this.micActive && this.analyser) {
      const buf = new Uint8Array(this.analyser.frequencyBinCount);
      this.analyser.getByteFrequencyData(buf);
      let sum = 0,
        low = 0,
        mid = 0,
        high = 0;
      const n = buf.length;
      for (let i = 0; i < n; i++) {
        sum += buf[i] ?? 0;
        if (i < n / 3) low += buf[i] ?? 0;
        else if (i < (2 * n) / 3) mid += buf[i] ?? 0;
        else high += buf[i] ?? 0;
      }
      const avg = sum / n / 255;
      return {
        level: Math.min(1, avg * 2.5),
        bands: [low / (n / 3) / 255, mid / (n / 3) / 255, high / (n / 3) / 255],
      };
    }

    // Synthesized envelopes
    const t = now / 1000;
    if (this.synthMode === "speak") {
      const env =
        0.45 +
        0.35 * Math.sin(t * 6.3) * Math.sin(t * 2.1) +
        0.2 * Math.sin(t * 11) * Math.cos(t * 3.7);
      const burst =
        Math.max(0, Math.sin(t * 1.3 + Math.sin(t * 0.4) * 2)) * 0.3;
      const lvl = Math.min(1, Math.max(0.05, Math.abs(env) + burst));
      return {
        level: lvl,
        bands: [
          Math.max(0, Math.min(1, (0.5 + 0.4 * Math.sin(t * 3.2)) * lvl)),
          Math.max(0, Math.min(1, (0.5 + 0.4 * Math.sin(t * 5.7 + 1)) * lvl)),
          Math.max(0, Math.min(1, (0.5 + 0.4 * Math.sin(t * 9.1 + 2)) * lvl)),
        ],
      };
    }
    if (this.synthMode === "think") {
      const lvl = 0.15 + 0.1 * Math.sin(t * 1.2) + 0.05 * Math.sin(t * 0.4);
      return { level: lvl, bands: [lvl, lvl * 0.7, lvl * 0.4] };
    }
    if (this.synthMode === "listen") {
      // Simulates mic-like amplitude variation
      const lvl = Math.max(
        0,
        Math.min(
          1,
          0.3 + 0.2 * Math.abs(Math.sin(t * 7.1)) + 0.1 * Math.sin(t * 13.3),
        ),
      );
      return { level: lvl, bands: [lvl * 0.8, lvl, lvl * 0.6] };
    }

    return { level: 0, bands: [0, 0, 0] };
  }
}
