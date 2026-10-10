/**
 * Mic capture via AudioWorklet, walkie-talkie lifecycle under iOS rules.
 *
 * Captures mic audio at 24 kHz (Realtime API native rate), converts Float32
 * samples to PCM16, and calls onFrame with a base64-encoded chunk whenever
 * `active` is true.
 *
 * What iOS/WebKit allows (measured on the iOS 18.6 simulator, 2026-10-10):
 * - For touch input only a finger LIFT (pointerup/touchend/click) counts as
 *   a user gesture. A press (pointerdown) does not.
 * - An AudioContext created outside a gesture stays suspended until one
 *   resumes it from inside a gesture; after that it can be suspended and
 *   resumed freely.
 * - `getUserMedia` after the previous stream was stopped is refused
 *   ("Request is not allowed") unless called from a gesture or within a
 *   few seconds of the last capture.
 * - `getUserMedia` is refused outright while the session category is
 *   "playback" (see session.ts).
 *
 * So: the context lives for the whole page and is unlocked by the first
 * gesture (`prime()` from a document-level lift listener). The stream is
 * acquired once per foreground visit, on a press if WebKit allows it or
 * otherwise on the next lift. Between turns, and while the app is in the
 * background, the track is disabled and the context suspended rather than
 * stopped (`idle()`): a stopped stream makes iOS re-prompt for permission
 * and refuse to re-open from a press. `release()` is for unmount only.
 */

import { setAudioSessionType } from "./session.js";

// Inline worklet — avoids Vite complications with ?url imports.
const WORKLET_CODE = `
class MicProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch && ch.length > 0) {
      const out = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) {
        const s = Math.max(-1.0, Math.min(1.0, ch[i]));
        out[i] = s < 0 ? (s * 32768) | 0 : (s * 32767) | 0;
      }
      this.port.postMessage(out.buffer, [out.buffer]);
    }
    return true;
  }
}
registerProcessor('mic-processor', MicProcessor);
`;

export type MicEvent = (event: string, data?: Record<string, unknown>) => void;

export class MicCapture {
  private ctx: AudioContext | null = null;
  private workletReady: Promise<AudioWorkletNode> | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private acquiring: Promise<void> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;

  /** Set to true while PTT button is held — gates frame transmission. */
  active = false;

  /** Called with a base64-encoded PCM16 chunk whenever active. */
  onFrame: ((base64: string) => void) | null = null;

  /** Telemetry hook (forwarded to the server log as client events). */
  onEvent: MicEvent | null = null;

  /** True when a capture stream is live (it may be idle/disabled). */
  get hasStream(): boolean {
    const track = this.stream?.getAudioTracks()[0];
    return track !== undefined && track.readyState === "live";
  }

  /** True while the mic holds (or is acquiring) the audio session. */
  get holdsSession(): boolean {
    return this.stream !== null || this.acquiring !== null;
  }

  /** Context and worklet, created once. Call inside a gesture if possible. */
  private ensureContext(): AudioContext {
    if (this.ctx === null || this.ctx.state === "closed") {
      this.ctx = new AudioContext({ sampleRate: 24000 });
      const ctx = this.ctx;
      this.workletReady = (async () => {
        const blob = new Blob([WORKLET_CODE], {
          type: "application/javascript",
        });
        const blobUrl = URL.createObjectURL(blob);
        try {
          await ctx.audioWorklet.addModule(blobUrl);
        } finally {
          URL.revokeObjectURL(blobUrl);
        }
        const node = new AudioWorkletNode(ctx, "mic-processor");
        node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
          if (!this.active || !this.onFrame) return;
          this.onFrame(bufferToBase64(e.data));
        };
        // Silent gain keeps the graph pulling without echoing the mic.
        const silent = ctx.createGain();
        silent.gain.value = 0;
        node.connect(silent);
        silent.connect(ctx.destination);
        return node;
      })();
    }
    return this.ctx;
  }

  /**
   * From a gesture (finger lift, click, key): unlock the context and, if
   * there's no stream yet this visit, acquire one. The first call per
   * launch shows iOS's permission prompt.
   */
  prime(): void {
    const ctx = this.ensureContext();
    if (ctx.state !== "running") void ctx.resume().catch(() => undefined);
    if (!this.hasStream && this.acquiring === null) {
      void this.acquire("gesture")
        .then(() => {
          // Primed between turns: keep it muted until someone presses.
          if (!this.active) this.idle("primed");
        })
        .catch(() => undefined);
    }
  }

  /**
   * Press: make the mic ready to stream. Throws if a stream can't be
   * acquired without a gesture (caller asks the user to press again).
   */
  async open(): Promise<void> {
    this.cancelIdle();
    const ctx = this.ensureContext();
    if (ctx.state !== "running") void ctx.resume().catch(() => undefined);
    if (!this.hasStream) await this.acquire("press");
    this.setTrackEnabled(true);
    if (ctx.state !== "running") {
      await Promise.race([ctx.resume().catch(() => undefined), sleep(1000)]);
    }
    if (ctx.state !== "running") {
      this.onEvent?.("mic_ctx_not_running", { state: ctx.state });
    }
  }

  private acquire(trigger: "gesture" | "press"): Promise<void> {
    if (this.acquiring) return this.acquiring;
    this.acquiring = (async () => {
      const t0 = performance.now();
      // Capture is refused under the "playback" session category.
      setAudioSessionType("play-and-record");
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          },
        });
        this.stopStream();
        this.stream = stream;
        const ctx = this.ensureContext();
        this.onEvent?.("mic_stream_granted", {
          trigger,
          ms: Math.round(performance.now() - t0),
          ctx_state: ctx.state,
        });
        const node = await this.workletReady!;
        this.source = ctx.createMediaStreamSource(stream);
        this.source.connect(node);
        this.onEvent?.("mic_acquired", {
          trigger,
          ms: Math.round(performance.now() - t0),
          ctx_state: ctx.state,
        });
      } catch (err) {
        if (this.stream === null) setAudioSessionType("playback");
        const e = err as { name?: string; message?: string };
        this.onEvent?.("mic_acquire_failed", {
          trigger,
          name: e?.name ?? "unknown",
          message: (e?.message ?? String(err)).slice(0, 200),
        });
        throw err;
      }
    })().finally(() => {
      this.acquiring = null;
    });
    return this.acquiring;
  }

  /** Between turns, after `delayMs`: mute the track, pause the context. */
  scheduleIdle(delayMs: number, reason = "linger_elapsed"): void {
    this.cancelIdle();
    this.idleTimer = setTimeout(() => {
      this.idleTimer = null;
      this.idle(reason);
    }, delayMs);
  }

  idle(reason: string): void {
    this.cancelIdle();
    this.active = false;
    if (!this.hasStream) return;
    this.setTrackEnabled(false);
    if (this.ctx?.state === "running") void this.ctx.suspend();
    this.onEvent?.("mic_idle", { reason });
  }

  /** Leaving the app / unmount: stop the stream entirely. */
  release(reason: string): void {
    this.cancelIdle();
    this.active = false;
    if (this.stream === null) return;
    this.stopStream();
    if (this.ctx?.state === "running") void this.ctx.suspend();
    setAudioSessionType("playback");
    this.onEvent?.("mic_released", { reason });
  }

  private setTrackEnabled(enabled: boolean): void {
    for (const t of this.stream?.getAudioTracks() ?? []) t.enabled = enabled;
  }

  private stopStream(): void {
    this.source?.disconnect();
    this.source = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  private cancelIdle(): void {
    if (this.idleTimer !== null) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const CHUNK = 1024;
  for (let i = 0; i < bytes.byteLength; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}
