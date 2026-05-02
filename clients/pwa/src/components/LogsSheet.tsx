// Logs sheet — dev tool. Shows the merged stream of status messages and
// dev_events captured by useWs for the current session. Newest first.

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { StatusEntry, DevEvent } from "../types.js";

const sheetStyles = {
  sheet: {
    position: "absolute" as const,
    inset: 0,
    zIndex: 30,
    background: "var(--hux-bg)",
    color: "var(--hux-fg)",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "20px 24px 12px",
    fontFamily: "var(--hux-sans)",
    fontSize: 14,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
    flexShrink: 0,
  },
  closeBtn: {
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg)",
    padding: "6px 12px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 12,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    cursor: "pointer",
  },
  body: {
    flex: 1,
    overflowY: "auto" as const,
    overscrollBehavior: "contain" as const,
    padding: "8px 24px 32px",
  },
  toolbar: {
    display: "flex",
    justifyContent: "flex-end",
    marginBottom: 12,
  },
  clearBtn: {
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg-dim)",
    padding: "4px 10px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 11,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    cursor: "pointer",
  },
  row: {
    padding: "10px 0",
    borderBottom: "1px solid var(--hux-fg-line)",
    fontFamily: '"JetBrains Mono", ui-monospace, monospace',
    fontSize: 12,
    lineHeight: 1.45,
    display: "grid",
    gridTemplateColumns: "auto auto 1fr",
    columnGap: 12,
    alignItems: "baseline",
  },
  ts: {
    color: "var(--hux-fg-dim)",
    fontVariantNumeric: "tabular-nums" as const,
  },
  kindStatus: {
    color: "var(--hux-fg-dim)",
    textTransform: "uppercase" as const,
    letterSpacing: "0.08em",
    fontSize: 10,
  },
  kindEvent: {
    color: "var(--hux-fg)",
    fontWeight: 500,
  },
  text: {
    color: "var(--hux-fg)",
    overflowWrap: "anywhere" as const,
    whiteSpace: "pre-wrap" as const,
  },
  payload: {
    color: "var(--hux-fg-dim)",
    overflowWrap: "anywhere" as const,
    whiteSpace: "pre-wrap" as const,
  },
};

type LogRow =
  | { source: "status"; id: number; ts: string; text: string }
  | {
      source: "dev";
      id: number;
      ts: string;
      kind: string;
      payload: Record<string, unknown>;
    };

interface LogsSheetProps {
  onClose: () => void;
  statusLog: StatusEntry[];
  devEvents: DevEvent[];
  onClear: () => void;
  sheetClassName?: string;
}

export function LogsSheet({
  onClose,
  statusLog,
  devEvents,
  onClear,
  sheetClassName = "hux-sheet",
}: LogsSheetProps) {
  const { t } = useTranslation();

  // useWs gives both arrays newest-first by id. Merge by id descending so
  // statuses and dev events interleave in true chronological order.
  const rows = useMemo<LogRow[]>(() => {
    const merged: LogRow[] = [
      ...statusLog.map(
        (s): LogRow => ({
          source: "status",
          id: s.id,
          ts: s.ts,
          text: s.text,
        }),
      ),
      ...devEvents.map(
        (d): LogRow => ({
          source: "dev",
          id: d.id,
          ts: d.ts,
          kind: d.kind,
          payload: d.payload,
        }),
      ),
    ];
    merged.sort((a, b) => b.id - a.id);
    return merged;
  }, [statusLog, devEvents]);

  return (
    <div style={sheetStyles.sheet} className={sheetClassName}>
      <div style={sheetStyles.header}>
        <span>{t("logs.recent")}</span>
        <button style={sheetStyles.closeBtn} onClick={onClose}>
          {t("device.close")}
        </button>
      </div>
      <div style={sheetStyles.body}>
        <h2
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(34px, 8vw, 56px)",
            lineHeight: 1.05,
            margin: "8px 0 24px",
            letterSpacing: "-0.01em",
          }}
        >
          {t("logs.title")}
        </h2>
        <div style={sheetStyles.toolbar}>
          <button
            style={sheetStyles.clearBtn}
            onClick={onClear}
            disabled={rows.length === 0}
          >
            {t("logs.clear")}
          </button>
        </div>
        {rows.length === 0 ? (
          <div
            style={{
              fontFamily: "var(--hux-sans)",
              color: "var(--hux-fg-dim)",
              fontSize: 15,
              padding: "20px 0",
            }}
          >
            {t("logs.empty")}
          </div>
        ) : (
          rows.map((r) =>
            r.source === "status" ? (
              <div key={`s-${r.id}`} style={sheetStyles.row}>
                <span style={sheetStyles.ts}>{r.ts}</span>
                <span style={sheetStyles.kindStatus}>
                  {t("logs.statusTag")}
                </span>
                <span style={sheetStyles.text}>{r.text}</span>
              </div>
            ) : (
              <div key={`d-${r.id}`} style={sheetStyles.row}>
                <span style={sheetStyles.ts}>{r.ts}</span>
                <span style={sheetStyles.kindEvent}>{r.kind}</span>
                <span style={sheetStyles.payload}>
                  {formatPayload(r.payload)}
                </span>
              </div>
            ),
          )
        )}
      </div>
    </div>
  );
}

// Compact, deterministic single-line representation of a dev_event payload.
// Empty object renders as "—" so the row reads cleanly when a kind has no
// fields. Anything richer than a flat object/scalar falls back to JSON.
function formatPayload(p: Record<string, unknown>): string {
  const keys = Object.keys(p);
  if (keys.length === 0) return "—";
  return keys.map((k) => `${k}=${formatValue(p[k])}`).join("  ");
}

function formatValue(v: unknown): string {
  if (v === null) return "null";
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
