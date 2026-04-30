/**
 * ClientEventPanel — dev affordance for firing arbitrary `client_event`
 * messages from the PWA. Bound to Shift+E in App.tsx (matches the
 * locked Stage-4 DoD).
 *
 * Wraps the existing `ws.sendClientEvent(key, data)` callable from
 * `useWs.ts:154`, which has been the canonical PWA → server `client_event`
 * sender since before Stage 4 (used internally for silence-timer +
 * thinking-tone telemetry). Exposing it via this panel just lets a
 * developer fire arbitrary events without wiring a new code path —
 * useful for testing skill subscriptions added via
 * `ctx.subscribe_client_event`.
 *
 * Not for end users. Intentionally undocumented in user-facing surfaces;
 * mirrors the pattern of Ctrl+Shift+T (TweaksPanel) which is also a dev
 * affordance with no public-doc home.
 */
import { useCallback, useState } from "react";

type Props = {
  onClose: () => void;
  onSend: (event: string, data: Record<string, unknown>) => void;
};

export function ClientEventPanel({ onClose, onSend }: Props) {
  const [eventKey, setEventKey] = useState("demo.ping");
  const [dataJson, setDataJson] = useState('{\n  "hello": "world"\n}');
  const [error, setError] = useState<string | null>(null);

  const handleSend = useCallback(() => {
    let parsed: Record<string, unknown> = {};
    if (dataJson.trim().length > 0) {
      try {
        const candidate: unknown = JSON.parse(dataJson);
        if (
          typeof candidate !== "object" ||
          candidate === null ||
          Array.isArray(candidate)
        ) {
          setError("data must be a JSON object (got an array or primitive)");
          return;
        }
        parsed = candidate as Record<string, unknown>;
      } catch (e) {
        setError(`invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
        return;
      }
    }
    if (!eventKey.trim()) {
      setError("event key cannot be empty");
      return;
    }
    setError(null);
    onSend(eventKey.trim(), parsed);
  }, [eventKey, dataJson, onSend]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Send client_event"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        // Click inside doesn't dismiss.
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg, #fff)",
          color: "var(--fg, #111)",
          padding: "1rem 1.25rem",
          borderRadius: 8,
          minWidth: 380,
          maxWidth: 520,
          fontFamily: "var(--mono, monospace)",
          fontSize: 13,
          boxShadow: "0 8px 32px rgba(0,0,0,0.35)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginBottom: 8,
          }}
        >
          <strong style={{ fontSize: 14 }}>Send client_event</strong>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              border: "none",
              background: "transparent",
              cursor: "pointer",
              fontSize: 16,
              color: "inherit",
            }}
          >
            ×
          </button>
        </div>
        <label style={{ display: "block", marginBottom: 8 }}>
          <span style={{ display: "block", marginBottom: 2, opacity: 0.7 }}>
            event key
          </span>
          <input
            type="text"
            value={eventKey}
            onChange={(e) => setEventKey(e.target.value)}
            spellCheck={false}
            autoFocus
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: 6,
              fontFamily: "inherit",
              fontSize: "inherit",
              background: "var(--input-bg, #f4f4f5)",
              color: "inherit",
              border: "1px solid var(--border, #ddd)",
              borderRadius: 4,
            }}
            placeholder="my-skill.event_name"
          />
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          <span style={{ display: "block", marginBottom: 2, opacity: 0.7 }}>
            data (JSON object; empty for {"{}"})
          </span>
          <textarea
            value={dataJson}
            onChange={(e) => setDataJson(e.target.value)}
            spellCheck={false}
            rows={6}
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: 6,
              fontFamily: "inherit",
              fontSize: "inherit",
              background: "var(--input-bg, #f4f4f5)",
              color: "inherit",
              border: "1px solid var(--border, #ddd)",
              borderRadius: 4,
              resize: "vertical",
            }}
          />
        </label>
        {error && (
          <div
            role="alert"
            style={{
              color: "#c0392b",
              marginBottom: 8,
              padding: 6,
              background: "rgba(192,57,43,0.08)",
              borderRadius: 4,
            }}
          >
            {error}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: "6px 12px",
              cursor: "pointer",
              background: "transparent",
              color: "inherit",
              border: "1px solid var(--border, #ddd)",
              borderRadius: 4,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSend}
            style={{
              padding: "6px 12px",
              cursor: "pointer",
              background: "var(--accent, #2563eb)",
              color: "var(--accent-fg, #fff)",
              border: "none",
              borderRadius: 4,
            }}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
