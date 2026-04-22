/**
 * Mic capture via AudioWorklet.
 *
 * Captures mic audio at 24 kHz (Realtime API native rate), converts Float32
 * samples to PCM16, and calls onFrame with a base64-encoded chunk whenever
 * `active` is true.
 *
 * Call init() once inside a user-gesture handler (pointer/click) so that
 * browsers allow AudioContext creation and getUserMedia.
 */

// Inline worklet — avoids Vite/SSR complications with ?url imports.
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

export class MicCapture {
  private ctx: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;

  /** Set to true while PTT button is held — gates frame transmission. */
  active = false;

  /** Called with a base64-encoded PCM16 chunk whenever active. */
  onFrame: ((base64: string) => void) | null = null;

  get initialized(): boolean {
    return this.ctx !== null;
  }

  /**
   * Initialize mic stream and AudioContext. Idempotent.
   * Must be called inside a user-gesture handler.
   */
  async init(): Promise<void> {
    if (this.ctx) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    });

    this.ctx = new AudioContext({ sampleRate: 24000 });

    const blob = new Blob([WORKLET_CODE], { type: "application/javascript" });
    const blobUrl = URL.createObjectURL(blob);
    try {
      await this.ctx.audioWorklet.addModule(blobUrl);
    } finally {
      URL.revokeObjectURL(blobUrl);
    }

    const source = this.ctx.createMediaStreamSource(this.stream);
    this.workletNode = new AudioWorkletNode(this.ctx, "mic-processor");

    this.workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      if (!this.active || !this.onFrame) return;
      this.onFrame(bufferToBase64(e.data));
    };

    // Connect through a silent gain node to keep the audio graph alive
    // without routing mic audio to the speakers.
    const silent = this.ctx.createGain();
    silent.gain.value = 0;
    source.connect(this.workletNode);
    this.workletNode.connect(silent);
    silent.connect(this.ctx.destination);
  }

  async resume(): Promise<void> {
    if (this.ctx?.state === "suspended") {
      await this.ctx.resume();
    }
  }

  destroy(): void {
    this.active = false;
    this.workletNode?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.ctx = null;
    this.workletNode = null;
    this.stream = null;
  }
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  // Process in chunks to avoid call stack limits on large buffers
  const CHUNK = 1024;
  for (let i = 0; i < bytes.byteLength; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}
