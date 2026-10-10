/**
 * Native audio for the iOS app: same surface as MicCapture / AudioPlayback,
 * backed by HuxleyAudioPlugin (ios/App/App/HuxleyAudioPlugin.swift), which
 * records and plays through AVAudioEngine in the app's own process.
 *
 * Web audio inside WKWebView runs in a WebKit helper process with its own
 * audio session: iOS showed it as a Dynamic Island live activity while
 * Huxley spoke, and WebKit's play-and-record routed replies to the
 * earpiece. Only used when the running app binary has the plugin
 * (`isNativeAudioAvailable`); an older install falls back to web audio.
 */

import { Capacitor, registerPlugin } from "@capacitor/core";

type EventFn = (event: string, data?: Record<string, unknown>) => void;

interface StartCaptureResult {
  ms: number;
  session_ms: number;
  build_ms: number;
  start_ms: number;
  warm_session: boolean;
  warm_engine: boolean;
  inputRate: number;
}

interface HuxleyAudioPlugin {
  startCapture(): Promise<StartCaptureResult>;
  stopCapture(): Promise<void>;
  play(options: { data: string; seq: number }): Promise<void>;
  stopPlayback(): Promise<void>;
  playEffect(options: { data: string }): Promise<void>;
  setLoopingEffect(options: { data?: string }): Promise<void>;
  setVolume(options: { level: number }): Promise<void>;
  addListener(
    event: "frame" | "playbackIdle" | "captureEnded" | "event",
    cb: (payload: Record<string, unknown>) => void,
  ): Promise<{ remove: () => Promise<void> }>;
}

const HuxleyAudio = registerPlugin<HuxleyAudioPlugin>("HuxleyAudio");

export function isNativeAudioAvailable(): boolean {
  return (
    Capacitor.isNativePlatform() && Capacitor.isPluginAvailable("HuxleyAudio")
  );
}

// ── Tone synthesis (PCM16 24 kHz mono, base64) ───────────────────────────
// The web build makes these with WebAudio oscillators; natively they're
// rendered here and played on the plugin's effects node.

const RATE = 24000;

type Segment =
  | { freq: number; ms: number; gain: number; edgeMs: number }
  | { silenceMs: number };

function renderTone(segments: Segment[]): string {
  const samples: number[] = [];
  for (const seg of segments) {
    if ("silenceMs" in seg) {
      for (let i = 0; i < (seg.silenceMs / 1000) * RATE; i++) samples.push(0);
      continue;
    }
    const n = Math.round((seg.ms / 1000) * RATE);
    const edge = Math.max(1, Math.round((seg.edgeMs / 1000) * RATE));
    for (let i = 0; i < n; i++) {
      const env = Math.min(1, i / edge, (n - 1 - i) / edge);
      samples.push(
        Math.sin((2 * Math.PI * seg.freq * i) / RATE) * seg.gain * env,
      );
    }
  }
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    pcm[i] = Math.max(
      -32768,
      Math.min(32767, Math.round((samples[i] ?? 0) * 32767)),
    );
  }
  return bytesToBase64(new Uint8Array(pcm.buffer));
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1024) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 1024));
  }
  return btoa(binary);
}

/** RMS of a base64 PCM16 chunk, 0..1, for the orb animation. */
function chunkLevel(b64: string): { level: number; seconds: number } {
  const binary = atob(b64);
  const n = Math.floor(binary.length / 2);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    let v = binary.charCodeAt(2 * i) | (binary.charCodeAt(2 * i + 1) << 8);
    if (v >= 0x8000) v -= 0x10000;
    sum += (v / 32768) ** 2;
  }
  return {
    level: n ? Math.min(1, Math.sqrt(sum / n) * 4) : 0,
    seconds: n / RATE,
  };
}

// ── Capture ──────────────────────────────────────────────────────────────

export class NativeCapture {
  active = false;
  onFrame: ((base64: string) => void) | null = null;
  onEvent: EventFn | null = null;
  /** Always releases between turns natively; kept for interface parity. */
  releaseWhenIdle = true;

  private capturing = false;
  // Released, but the plugin is still handing over the last block.
  private flushing = false;
  private wanted = false;
  private starting: Promise<void> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private openedAt: number | null = null;

  constructor() {
    void HuxleyAudio.addListener("frame", (payload) => {
      if (!(this.active || this.flushing) || !this.onFrame) return;
      if (this.openedAt !== null) {
        this.onEvent?.("mic_first_frame", {
          ms: Math.round(performance.now() - this.openedAt),
          native: true,
        });
        this.openedAt = null;
      }
      this.onFrame(String(payload.data ?? ""));
    });
    // The plugin stopped recording on its own: a phone call, Siri, the
    // app leaving the screen, or a route change it couldn't follow. A call
    // (skill_continuous) then sees `hasStream` false and reopens the mic.
    void HuxleyAudio.addListener("captureEnded", (payload) => {
      this.capturing = false;
      this.onEvent?.("mic_capture_ended", {
        reason: String(payload.reason ?? "unknown"),
        native: true,
      });
    });
  }

  get hasStream(): boolean {
    return this.capturing;
  }

  get holdsSession(): boolean {
    return this.capturing || this.starting !== null;
  }

  /** Native permission isn't gesture-bound; nothing to prime. */
  prime(): void {}

  async open(): Promise<void> {
    this.cancelIdle();
    this.wanted = true;
    this.openedAt = performance.now();
    if (this.capturing) return;
    if (!this.starting) {
      this.starting = HuxleyAudio.startCapture()
        .then((r) => {
          this.capturing = true;
          // The breakdown says where a slow press went: session_ms (audio
          // session switched on), build_ms (capture engine built), start_ms
          // (engine started). Warm presses should be start_ms only.
          this.onEvent?.("mic_acquired", {
            trigger: "press",
            ms: r.ms,
            session_ms: r.session_ms,
            build_ms: r.build_ms,
            start_ms: r.start_ms,
            warm_session: r.warm_session,
            warm_engine: r.warm_engine,
            native: true,
          });
          // Released while the engine was starting: stop it now.
          if (!this.wanted) this.release("released_during_start");
        })
        .catch((err: { code?: string; message?: string }) => {
          this.onEvent?.("mic_acquire_failed", {
            name: err?.code ?? "unknown",
            message: String(err?.message ?? err).slice(0, 200),
            native: true,
          });
          // Not "NotAllowedError": natively a refusal is the user's setting,
          // not a gesture problem, so the app shows "access denied".
          const e = new Error(err?.message ?? "native capture failed");
          e.name = "NativeCaptureError";
          throw e;
        })
        .finally(() => {
          this.starting = null;
        });
    }
    await this.starting;
  }

  scheduleIdle(delayMs: number, reason = "linger_elapsed"): void {
    this.cancelIdle();
    this.idleTimer = setTimeout(() => {
      this.idleTimer = null;
      this.idle(reason);
    }, delayMs);
  }

  idle(reason: string): void {
    this.release(reason);
  }

  /**
   * End of a press. Resolves once the last of the speech has reached
   * onFrame: the plugin keeps recording until the mic hands over its next
   * 100 ms block, which holds the audio up to the release. Send ptt_stop
   * after this resolves, or the server drops that tail (often the end of
   * the last word, sometimes all of a quick "si").
   */
  finish(): Promise<void> {
    this.cancelIdle();
    this.active = false;
    this.wanted = false;
    if (!this.capturing) return Promise.resolve();
    this.capturing = false;
    this.flushing = true;
    const started = performance.now();
    // The plugin answers within ~100 ms; never hold a release longer.
    const deadline = new Promise<void>((resolve) => setTimeout(resolve, 400));
    return Promise.race([HuxleyAudio.stopCapture(), deadline])
      .catch(() => {})
      .finally(() => {
        this.flushing = false;
        this.onEvent?.("mic_released", {
          reason: "press_ended",
          flush_ms: Math.round(performance.now() - started),
          native: true,
        });
      });
  }

  release(reason: string): void {
    this.cancelIdle();
    this.active = false;
    this.wanted = false;
    if (!this.capturing || this.flushing) return;
    this.capturing = false;
    void HuxleyAudio.stopCapture();
    this.onEvent?.("mic_released", { reason, native: true });
  }

  cancelIdle(): void {
    if (this.idleTimer !== null) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }
}

// ── Playback ─────────────────────────────────────────────────────────────

const READY_TONE = renderTone([{ freq: 880, ms: 90, gain: 0.25, edgeMs: 8 }]);
const ERROR_TONE = renderTone([
  { freq: 660, ms: 120, gain: 0.22, edgeMs: 10 },
  { silenceMs: 40 },
  { freq: 330, ms: 120, gain: 0.22, edgeMs: 10 },
]);
// One period of the 120 Hz thinking pulse (150 ms on, 250 ms off), looped.
const THINKING_PULSE = renderTone([
  { freq: 120, ms: 150, gain: 0.15, edgeMs: 20 },
  { silenceMs: 250 },
]);

export class NativePlayback {
  onEvent: EventFn | null = null;
  /** Interface parity with AudioPlayback; the native session handles it. */
  micOpen: () => boolean = () => false;

  private busy = false;
  // Sequence number of the latest chunk sent to the plugin. Its
  // playbackIdle carries the latest it had; an older one is stale (a newer
  // chunk was already on its way) and must not end this reply.
  private seq = 0;
  private idleCallbacks: Array<() => void> = [];
  private thinking = false;
  // Level timeline for the orb: each scheduled chunk's playing window.
  private levels: Array<{ start: number; end: number; level: number }> = [];
  private playhead = 0;

  constructor() {
    void HuxleyAudio.addListener("playbackIdle", (payload) => {
      if (Number(payload.seq) !== this.seq) return;
      this.busy = false;
      this.levels = [];
      const cbs = this.idleCallbacks.splice(0);
      for (const cb of cbs) cb();
    });
    void HuxleyAudio.addListener("event", (payload) => {
      const { name, ...rest } = payload;
      this.onEvent?.(String(name ?? "native_audio_event"), rest);
    });
  }

  async init(): Promise<void> {}
  async resume(): Promise<void> {}
  unlock(): Promise<void> {
    return Promise.resolve();
  }

  play(base64: string): void {
    this.busy = true;
    const now = performance.now() / 1000;
    const { level, seconds } = chunkLevel(base64);
    const start = Math.max(now, this.playhead);
    this.playhead = start + seconds;
    this.levels.push({ start, end: this.playhead, level });
    this.seq += 1;
    void HuxleyAudio.play({ data: base64, seq: this.seq }).catch(
      (err: { message?: string }) => {
        this.onEvent?.("native_play_failed", {
          message: String(err?.message ?? err).slice(0, 200),
        });
      },
    );
  }

  onceIdle(cb: () => void): void {
    if (!this.busy) cb();
    else this.idleCallbacks.push(cb);
  }

  stop(): void {
    this.busy = false;
    this.levels = [];
    this.playhead = 0;
    this.thinking = false;
    void HuxleyAudio.stopPlayback();
    const cbs = this.idleCallbacks.splice(0);
    for (const cb of cbs) cb();
  }

  playTone(_freq = 880, _durationMs = 90): void {
    void HuxleyAudio.playEffect({ data: READY_TONE });
  }

  playErrorTone(): void {
    void HuxleyAudio.playEffect({ data: ERROR_TONE });
  }

  playThinkingTone(): void {
    if (this.thinking) return;
    this.thinking = true;
    void HuxleyAudio.setLoopingEffect({ data: THINKING_PULSE });
  }

  stopThinkingTone(): void {
    if (!this.thinking) return;
    this.thinking = false;
    void HuxleyAudio.setLoopingEffect({});
  }

  setVolume(level: number): void {
    void HuxleyAudio.setVolume({ level });
  }

  /** Orb bands from the playing chunk's loudness (no native FFT needed). */
  getFrequencyData(n: number): number[] {
    const now = performance.now() / 1000;
    while (this.levels.length > 0 && (this.levels[0]?.end ?? 0) < now)
      this.levels.shift();
    const current = this.levels.find((l) => l.start <= now && now <= l.end);
    if (!current) return new Array(n).fill(0) as number[];
    const out: number[] = [];
    for (let i = 0; i < n; i++) {
      const wobble = 0.65 + 0.35 * Math.sin(i * 1.7 + now * 9);
      out.push(Math.min(1, current.level * wobble));
    }
    return out;
  }

  preloadPersonaSwap(): Promise<void> {
    return Promise.resolve();
  }

  playPersonaSwap(): void {}

  destroy(): void {
    this.stop();
  }
}
