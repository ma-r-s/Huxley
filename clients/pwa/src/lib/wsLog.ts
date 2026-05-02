// T1.21 — bidirectional WebSocket frame log + connection lifecycle log.
//
// Sits next to `useWs.ts` and is fed by every frame send/receive plus
// every onopen/onclose/lifecycle event. The DiagnosticPanel snapshots
// this log and renders it. Mario's reproducer (PWA looks ready, server
// keeps responding, frames don't reach onmessage) is exactly the kind
// of bug this exists to make obvious: outbound frames show up in `tx`,
// the corresponding response should land in `rx`. If it doesn't, the
// failure is visible without re-instrumenting the client.
//
// Design notes:
// - Bounded ring buffer (CAP entries). At 500 entries × ~120 bytes per
//   entry, the in-memory footprint is ~60 KB, fine for a dev panel.
//   Ample for any single-session debugging window; a long-running
//   audiobook stream would cycle through audio frames faster than the
//   buffer holds them, but the panel is for *debugging*, not for
//   archival — accepting that trade.
// - Audio frames get a special compressed entry (no base64 payload in
//   the log) since they're hot-path and would dominate the buffer.
// - Logged entries are append-only; a "clear" button on the panel
//   re-creates the buffer.
// - This module exports a class; `useWs` instantiates one per hook
//   instance via useRef. There's no global state; multiple hook
//   instances (a hypothetical multi-tab future) get independent logs.

export type WsLogDirection = "rx" | "tx" | "lifecycle";

export interface WsLogEntry {
  /** Wall-clock ms (Date.now()) at the moment of the event. */
  ts: number;
  /** rx = inbound from server; tx = outbound to server; lifecycle = open/close/error. */
  dir: WsLogDirection;
  /** Frame `type` field for rx/tx, or lifecycle event name (e.g., "open", "close-1006"). */
  type: string;
  /** Bytes (rx/tx) or 0 (lifecycle). */
  size: number;
  /**
   * One-line summary of the payload. For audio frames we just show the
   * byte count to avoid drowning the log in base64; for everything
   * else we show the JSON minus large fields. Truncated to 200 chars
   * so the panel can render a single line per entry.
   */
  preview: string;
}

export interface WsLogSnapshot {
  entries: WsLogEntry[];
  /** Last received `conn_id` from a hello frame, if any. */
  connId: string | null;
  /** Aggregate counters since the last clear(). */
  totals: {
    rx: number;
    tx: number;
    rxBytes: number;
    txBytes: number;
    /** Per-type rx histogram. Same shape as server-side `rx_types`. */
    rxByType: Record<string, number>;
    txByType: Record<string, number>;
  };
  /** ms timestamps so the panel can show "last seen 1.2s ago". */
  lastRxAt: number | null;
  lastTxAt: number | null;
  lastOpenAt: number | null;
  lastCloseAt: number | null;
}

const CAP = 500;
const PREVIEW_LEN = 200;

/** Audio frame size after base64 encoding is ~3500 bytes per ~50ms PCM
 *  chunk; logging the full base64 would balloon the buffer. We only
 *  record the binary size. */
function previewFrame(parsed: unknown): string {
  if (typeof parsed !== "object" || parsed === null) return String(parsed);
  const obj = parsed as Record<string, unknown>;
  if (obj.type === "audio" && typeof obj.data === "string") {
    // PCM16 base64 → ~3 bytes per 4 chars. Show the byte count, not
    // the base64. The size column already has total wire bytes.
    const approxPcmBytes = Math.floor((obj.data.length * 3) / 4);
    return `audio: ~${approxPcmBytes}B PCM`;
  }
  // Compact JSON; truncated to PREVIEW_LEN. We strip "type" since the
  // log row already has its own column for it.
  const { type: _omit, ...rest } = obj;
  void _omit;
  let s: string;
  try {
    s = JSON.stringify(rest);
  } catch {
    s = "[unserializable]";
  }
  if (s.length > PREVIEW_LEN) s = s.slice(0, PREVIEW_LEN - 1) + "…";
  return s;
}

export class WsLog {
  /** Ring buffer head/tail emulated via a normal array + shift-on-cap.
   *  Could be a circular index for O(1) writes, but at 500 entries the
   *  shift cost is negligible and the array is easier to snapshot. */
  private entries: WsLogEntry[] = [];
  private connId: string | null = null;
  private rx = 0;
  private tx = 0;
  private rxBytes = 0;
  private txBytes = 0;
  private rxByType: Record<string, number> = {};
  private txByType: Record<string, number> = {};
  private lastRxAt: number | null = null;
  private lastTxAt: number | null = null;
  private lastOpenAt: number | null = null;
  private lastCloseAt: number | null = null;

  recordRx(rawText: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawText);
    } catch {
      this.append({
        ts: Date.now(),
        dir: "rx",
        type: "[malformed]",
        size: rawText.length,
        preview: rawText.slice(0, PREVIEW_LEN),
      });
      this.rx += 1;
      this.rxBytes += rawText.length;
      this.lastRxAt = Date.now();
      return;
    }
    const type =
      typeof (parsed as { type?: unknown })?.type === "string"
        ? (parsed as { type: string }).type
        : "[no-type]";
    // Capture conn_id from hello frames so the panel can surface it.
    if (type === "hello") {
      const hello = parsed as { conn_id?: unknown };
      if (typeof hello.conn_id === "string") this.connId = hello.conn_id;
    }
    this.append({
      ts: Date.now(),
      dir: "rx",
      type,
      size: rawText.length,
      preview: previewFrame(parsed),
    });
    this.rx += 1;
    this.rxBytes += rawText.length;
    this.lastRxAt = Date.now();
    this.rxByType[type] = (this.rxByType[type] ?? 0) + 1;
  }

  recordTx(rawText: string, parsedHint?: unknown): void {
    // Caller already has the parsed object from constructing the
    // frame, so avoid round-tripping through JSON.parse. Falls back
    // to parsing if the caller didn't supply it.
    let parsed: unknown = parsedHint;
    if (parsed === undefined) {
      try {
        parsed = JSON.parse(rawText);
      } catch {
        parsed = null;
      }
    }
    const type =
      typeof (parsed as { type?: unknown } | null)?.type === "string"
        ? (parsed as { type: string }).type
        : "[no-type]";
    this.append({
      ts: Date.now(),
      dir: "tx",
      type,
      size: rawText.length,
      preview:
        parsed === null ? rawText.slice(0, PREVIEW_LEN) : previewFrame(parsed),
    });
    this.tx += 1;
    this.txBytes += rawText.length;
    this.lastTxAt = Date.now();
    this.txByType[type] = (this.txByType[type] ?? 0) + 1;
  }

  recordLifecycle(kind: string, detail?: Record<string, unknown>): void {
    const ts = Date.now();
    if (kind === "open") this.lastOpenAt = ts;
    if (kind.startsWith("close")) this.lastCloseAt = ts;
    let preview = "";
    if (detail) {
      try {
        preview = JSON.stringify(detail);
      } catch {
        preview = "[unserializable]";
      }
      if (preview.length > PREVIEW_LEN)
        preview = preview.slice(0, PREVIEW_LEN - 1) + "…";
    }
    this.append({ ts, dir: "lifecycle", type: kind, size: 0, preview });
  }

  /** Reset everything except the conn_id of an in-flight session — the
   *  conn_id stays so the panel still shows correlation across the
   *  clear. (Operator can read frames-since-clear in the totals.) */
  clear(): void {
    this.entries = [];
    this.rx = 0;
    this.tx = 0;
    this.rxBytes = 0;
    this.txBytes = 0;
    this.rxByType = {};
    this.txByType = {};
    this.lastRxAt = null;
    this.lastTxAt = null;
    // Lifecycle markers also reset — they're scoped to the visible
    // window and would mislead an operator who cleared mid-session.
    this.lastOpenAt = null;
    this.lastCloseAt = null;
  }

  snapshot(): WsLogSnapshot {
    return {
      entries: this.entries.slice(),
      connId: this.connId,
      totals: {
        rx: this.rx,
        tx: this.tx,
        rxBytes: this.rxBytes,
        txBytes: this.txBytes,
        rxByType: { ...this.rxByType },
        txByType: { ...this.txByType },
      },
      lastRxAt: this.lastRxAt,
      lastTxAt: this.lastTxAt,
      lastOpenAt: this.lastOpenAt,
      lastCloseAt: this.lastCloseAt,
    };
  }

  private append(entry: WsLogEntry): void {
    this.entries.push(entry);
    if (this.entries.length > CAP) {
      this.entries.shift();
    }
  }
}
