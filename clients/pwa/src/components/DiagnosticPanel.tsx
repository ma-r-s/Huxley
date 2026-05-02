// T1.21 — diagnostic panel. Always-on observability for the WebSocket
// link, surfaced as a dev-only overlay. Mario's "ready but doesn't
// answer / skills stuck loading" failure modes are exactly the kind
// of bug this panel exists to make obvious without re-instrumenting:
// every WS frame is in the timeline, every connection's eviction has
// a code + reason, every ping has an RTT, every watchdog firing is
// labeled.
//
// Open via:
// - `Ctrl+Shift+\` (avoids Chrome shortcuts on macOS + Linux)
// - `?diag=1` URL param (auto-opens; useful for screenshots / shared links)
//
// Closes via: Escape, the X button, or another `Ctrl+Shift+\`.
//
// Polls `ws.getDiagnostic()` every 200ms while open. Logging is in
// refs so polling is cheap; only the panel re-renders.

import { useCallback, useEffect, useRef, useState } from "react";
import type { WsHandle } from "../lib/useWs.js";
import type { WsLogEntry } from "../lib/wsLog.js";

interface Props {
  ws: WsHandle;
  open: boolean;
  onClose: () => void;
}

const POLL_INTERVAL_MS = 200;
// How many frame-log entries to render at once. Past this we'd spend
// the panel's render budget on layout for rows the operator can't
// read anyway. Newest entries shown first so the most recent failure
// is always at the top.
const MAX_RENDERED_ENTRIES = 200;

function fmtAgo(ts: number | null, now: number): string {
  if (ts === null) return "—";
  const ms = now - ts;
  if (ms < 1_000) return `${ms}ms ago`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(1)}s ago`;
  return `${Math.floor(ms / 60_000)}m ago`;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(2)}MB`;
}

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const mss = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${mss}`;
}

function fmtReadyState(rs: number | null): string {
  if (rs === null) return "no socket";
  return ["CONNECTING", "OPEN", "CLOSING", "CLOSED"][rs] ?? `?${rs}`;
}

const dirColor: Record<WsLogEntry["dir"], string> = {
  rx: "#7ef0a3",
  tx: "#7eb6f0",
  lifecycle: "#f0d97e",
};

export function DiagnosticPanel({
  ws,
  open,
  onClose,
}: Props): JSX.Element | null {
  // Tick-tock state to force re-renders during the poll loop. The
  // diagnostic snapshot itself lives in refs / WsLog and doesn't
  // need to be in state.
  const [tick, setTick] = useState(0);
  const lastSnapshotRef = useRef<ReturnType<typeof ws.getDiagnostic> | null>(
    null,
  );
  // `ws` is the return value of the `useWs` hook, which is a fresh
  // object on every parent render. Depending on it in the poll-interval
  // effect would re-fire the effect (and the `requestDiag` it sends!)
  // on every App re-render — observed as 8 `get_diag` txs in seconds.
  // Stash the latest `ws` in a ref so the effect reads the current
  // value without subscribing to its identity, and key the effect off
  // `open` only.
  const wsRef = useRef(ws);
  wsRef.current = ws;

  useEffect(() => {
    if (!open) return;
    // First render: pull a fresh snapshot synchronously so the panel
    // has data on the first paint. Subsequent ticks come from the
    // interval below.
    lastSnapshotRef.current = wsRef.current.getDiagnostic();
    setTick((t) => t + 1);
    // Also request a fresh server-side diag right away.
    wsRef.current.requestDiag();
    const id = setInterval(() => {
      lastSnapshotRef.current = wsRef.current.getDiagnostic();
      setTick((t) => t + 1);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [open]);

  // Escape closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const handleCopy = useCallback(() => {
    const snap = ws.getDiagnostic();
    const dump = {
      timestamp: new Date().toISOString(),
      now_ms: Date.now(),
      client: snap,
    };
    void navigator.clipboard.writeText(JSON.stringify(dump, null, 2));
  }, [ws]);

  const handleRefresh = useCallback(() => {
    ws.requestDiag();
  }, [ws]);

  const handleClear = useCallback(() => {
    ws.clearDiagLog();
    lastSnapshotRef.current = ws.getDiagnostic();
    setTick((t) => t + 1);
  }, [ws]);

  if (!open) return null;
  void tick; // ensure the closure observes tick changes
  const snap = lastSnapshotRef.current ?? ws.getDiagnostic();
  const now = Date.now();
  const log = snap.log;
  const visible = log.entries.slice(-MAX_RENDERED_ENTRIES).reverse();

  return (
    <div
      className="hux-diag-overlay"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20, 18, 25, 0.96)",
        color: "#eaeaea",
        zIndex: 9999,
        fontFamily:
          "ui-monospace, SF Mono, Monaco, Cascadia Code, Roboto Mono, monospace",
        fontSize: 12,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid #444",
          display: "flex",
          gap: 12,
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <strong style={{ fontSize: 13 }}>WS Diagnostic (T1.21)</strong>
        <span style={{ opacity: 0.6 }}>
          conn_id: <code style={{ color: "#f0d97e" }}>{log.connId ?? "—"}</code>
        </span>
        <span style={{ opacity: 0.6 }}>
          ready: <code>{fmtReadyState(snap.socketReadyState)}</code>
        </span>
        <span style={{ opacity: 0.6 }}>
          ping rtt:{" "}
          <code>
            {snap.lastPingRttMs !== null ? `${snap.lastPingRttMs}ms` : "—"}
          </code>
        </span>
        {snap.pingMisses > 0 && (
          <span style={{ color: "#ff8e7e" }}>
            misses: <code>{snap.pingMisses}</code>
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button
          onClick={handleRefresh}
          style={{
            padding: "4px 10px",
            background: "#2a3140",
            color: "#eaeaea",
            border: "1px solid #555",
            borderRadius: 4,
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11,
          }}
        >
          Refresh server
        </button>
        <button
          onClick={handleCopy}
          style={{
            padding: "4px 10px",
            background: "#2a3140",
            color: "#eaeaea",
            border: "1px solid #555",
            borderRadius: 4,
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11,
          }}
        >
          Copy JSON
        </button>
        <button
          onClick={handleClear}
          style={{
            padding: "4px 10px",
            background: "#2a3140",
            color: "#eaeaea",
            border: "1px solid #555",
            borderRadius: 4,
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11,
          }}
        >
          Clear log
        </button>
        <button
          onClick={onClose}
          style={{
            padding: "4px 10px",
            background: "#3a1f25",
            color: "#eaeaea",
            border: "1px solid #6a3a40",
            borderRadius: 4,
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11,
          }}
        >
          ✕ Close
        </button>
      </div>

      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid #333",
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 18,
          flexShrink: 0,
        }}
      >
        <div>
          <div style={{ opacity: 0.5, marginBottom: 4 }}>CLIENT</div>
          <div>
            url: <code>{snap.activeUrl ?? "—"}</code>
          </div>
          <div>
            last rx: <code>{fmtAgo(log.lastRxAt, now)}</code>
          </div>
          <div>
            last tx: <code>{fmtAgo(log.lastTxAt, now)}</code>
          </div>
          <div>
            rx: <code>{log.totals.rx}</code> ({fmtBytes(log.totals.rxBytes)}) /
            tx: <code>{log.totals.tx}</code> ({fmtBytes(log.totals.txBytes)})
          </div>
        </div>
        <div>
          <div style={{ opacity: 0.5, marginBottom: 4 }}>RX BY TYPE</div>
          {Object.entries(log.totals.rxByType)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([t, n]) => (
              <div key={t}>
                <code>{t}</code>: {n}
              </div>
            ))}
          {Object.keys(log.totals.rxByType).length === 0 && (
            <div style={{ opacity: 0.4 }}>no rx yet</div>
          )}
        </div>
        <div>
          <div style={{ opacity: 0.5, marginBottom: 4 }}>TX BY TYPE</div>
          {Object.entries(log.totals.txByType)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([t, n]) => (
              <div key={t}>
                <code>{t}</code>: {n}
              </div>
            ))}
          {Object.keys(log.totals.txByType).length === 0 && (
            <div style={{ opacity: 0.4 }}>no tx yet</div>
          )}
        </div>
      </div>

      {snap.lastServerDiag && (
        <details
          style={{
            padding: "8px 14px",
            borderBottom: "1px solid #333",
            flexShrink: 0,
          }}
        >
          <summary style={{ cursor: "pointer", opacity: 0.7 }}>
            Server snapshot (received{" "}
            {fmtAgo(snap.lastServerDiag.receivedAtMs, now)})
          </summary>
          <pre
            style={{
              marginTop: 8,
              maxHeight: 200,
              overflow: "auto",
              background: "#181820",
              padding: 8,
              borderRadius: 4,
              fontSize: 11,
              lineHeight: 1.4,
            }}
          >
            {JSON.stringify(snap.lastServerDiag.payload, null, 2)}
          </pre>
        </details>
      )}

      <div
        style={{
          padding: "8px 14px 4px",
          opacity: 0.5,
          fontSize: 11,
          flexShrink: 0,
        }}
      >
        Frame log (newest first, last {MAX_RENDERED_ENTRIES} of{" "}
        {log.entries.length})
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: "0 8px 12px" }}>
        <table
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}
        >
          <thead>
            <tr style={{ position: "sticky", top: 0, background: "#1a1820" }}>
              <th
                style={{ textAlign: "left", padding: "4px 8px", opacity: 0.6 }}
              >
                time
              </th>
              <th
                style={{ textAlign: "left", padding: "4px 8px", opacity: 0.6 }}
              >
                dir
              </th>
              <th
                style={{ textAlign: "left", padding: "4px 8px", opacity: 0.6 }}
              >
                type
              </th>
              <th
                style={{ textAlign: "right", padding: "4px 8px", opacity: 0.6 }}
              >
                size
              </th>
              <th
                style={{ textAlign: "left", padding: "4px 8px", opacity: 0.6 }}
              >
                preview
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((entry, i) => (
              <tr
                key={`${entry.ts}-${i}`}
                style={{ borderBottom: "1px solid #222" }}
              >
                <td style={{ padding: "2px 8px", opacity: 0.6 }}>
                  {fmtTime(entry.ts)}
                </td>
                <td
                  style={{
                    padding: "2px 8px",
                    color: dirColor[entry.dir],
                    fontWeight: 600,
                  }}
                >
                  {entry.dir}
                </td>
                <td style={{ padding: "2px 8px" }}>
                  <code>{entry.type}</code>
                </td>
                <td
                  style={{
                    padding: "2px 8px",
                    textAlign: "right",
                    opacity: 0.7,
                  }}
                >
                  {entry.size > 0 ? fmtBytes(entry.size) : ""}
                </td>
                <td
                  style={{
                    padding: "2px 8px",
                    fontFamily: "inherit",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    maxWidth: 600,
                    opacity: 0.85,
                  }}
                >
                  {entry.preview}
                </td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  style={{ padding: 24, textAlign: "center", opacity: 0.5 }}
                >
                  no frames logged
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
