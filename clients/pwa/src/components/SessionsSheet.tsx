// Sessions sheet — lists persisted conversations from the server (T1.12).
// Replaces the v1 hardcoded sample array. The server is the source of
// truth: this component triggers a `list_sessions` fetch on mount and
// renders whatever `ws.sessionsList` becomes.

import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { SessionMeta } from "../types.js";

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
  rowBtn: {
    width: "100%",
    textAlign: "left" as const,
    background: "transparent",
    border: "none",
    borderBottom: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg)",
    padding: "18px 0",
    fontFamily: "var(--hux-sans)",
    fontSize: 16,
    cursor: "pointer",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline" as const,
  },
};

interface SessionsSheetProps {
  onClose: () => void;
  // null = not yet loaded; [] = loaded and empty.
  sessions: SessionMeta[] | null;
  onPick: (id: number) => void;
  onMount: () => void;
  sheetClassName?: string;
}

export function SessionsSheet({
  onClose,
  sessions,
  onPick,
  onMount,
  sheetClassName = "hux-sheet",
}: SessionsSheetProps) {
  const { t } = useTranslation();

  // Fetch on mount. The fetch is fire-and-forget; useWs sets
  // sessionsList when the reply arrives, which re-renders this sheet.
  // Mount-only, so the empty deps array is intentional — re-running on
  // every onMount identity change would refetch on parent re-renders.
  useEffect(() => {
    onMount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={sheetStyles.sheet} className={sheetClassName}>
      <div style={sheetStyles.header}>
        <span>{t("sessions.recent")}</span>
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
            margin: "8px 0 32px",
            letterSpacing: "-0.01em",
          }}
        >
          {t("sessions.title")}
        </h2>
        {sessions === null && (
          <div style={emptyMsgStyle()}>{t("sessions.loading")}</div>
        )}
        {sessions !== null && sessions.length === 0 && (
          <div style={emptyMsgStyle()}>{t("sessions.empty")}</div>
        )}
        {sessions?.map((s) => (
          <button
            key={s.id}
            style={sheetStyles.rowBtn}
            onClick={() => onPick(s.id)}
          >
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 17 }}>{previewLabel(s, t)}</span>
              <span
                style={{
                  fontSize: 12,
                  color: "var(--hux-fg-dim)",
                  letterSpacing: "0.04em",
                }}
              >
                {formatWhen(s.started_at, t)} {"·"}{" "}
                {formatDuration(s.started_at, s.ended_at, t)} {"·"}{" "}
                {t("sessions.turnsCount", { count: s.turn_count })}
              </span>
            </span>
            <span style={{ fontSize: 13, color: "var(--hux-fg-dim)" }}>
              {"→"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function emptyMsgStyle() {
  return {
    fontFamily: "var(--hux-sans)",
    color: "var(--hux-fg-dim)",
    fontSize: 15,
    padding: "20px 0",
  };
}

// Display label: prefer `preview` (first user turn), fall back to the
// summary, else a localized "(no transcript)" placeholder for sessions
// where no user reply landed (e.g. proactive assistant turns).
function previewLabel(s: SessionMeta, t: (k: string) => string): string {
  if (s.preview) return s.preview;
  if (s.summary) return s.summary;
  return t("sessions.noTranscript");
}

// Render the row's `when` field as "Today, 3:42 PM" / "Yesterday" /
// "Apr 15". Server sends raw ISO strings (UTC SQLite datetime); we
// localize client-side.
function formatWhen(
  startedAt: string,
  t: (k: string, opts?: Record<string, unknown>) => string,
): string {
  const date = parseSqliteUtc(startedAt);
  if (!date) return startedAt;
  const now = new Date();
  const isSameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  const time = date.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  if (isSameDay) {
    return t("sessions.when.todayAt", { time });
  }
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (
    date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate()
  ) {
    return t("sessions.when.yesterday");
  }
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function formatDuration(
  startedAt: string,
  endedAt: string | null,
  t: (k: string) => string,
): string {
  if (!endedAt) return t("sessions.live");
  const start = parseSqliteUtc(startedAt);
  const end = parseSqliteUtc(endedAt);
  if (!start || !end) return "—";
  const ms = end.getTime() - start.getTime();
  const minutes = Math.max(1, Math.round(ms / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder === 0 ? `${hours}h` : `${hours}h ${remainder}m`;
}

// Treat the SQLite `datetime('now')` output as UTC. Without the `Z`
// suffix, Date() parses it as local — which would shift "Today" / time
// labels by hours when the server runs in a different TZ than the
// browser.
function parseSqliteUtc(s: string): Date | null {
  const iso = s.includes("T") ? s : s.replace(" ", "T");
  const withZ = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(withZ);
  return Number.isNaN(d.getTime()) ? null : d;
}
