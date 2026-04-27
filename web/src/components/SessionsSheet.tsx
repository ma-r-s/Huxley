// Sessions sheet — shows recent conversations.
// v1: in-memory only (no server persistence). Sessions disappear on reload.

import { useTranslation } from "react-i18next";

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

export interface Session {
  id: string;
  preview: string;
  when: string;
  duration: string;
  turns: number;
}

interface SessionsSheetProps {
  onClose: () => void;
  sessions: Session[];
  onPick: (s: Session) => void;
}

export function SessionsSheet({
  onClose,
  sessions,
  onPick,
}: SessionsSheetProps) {
  const { t } = useTranslation();
  return (
    <div style={sheetStyles.sheet} className="hux-sheet">
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
        {sessions.length === 0 && (
          <div
            style={{
              fontFamily: "var(--hux-sans)",
              color: "var(--hux-fg-dim)",
              fontSize: 15,
              padding: "20px 0",
            }}
          >
            {t("sessions.empty")}
          </div>
        )}
        {sessions.map((s) => (
          <button
            key={s.id}
            style={sheetStyles.rowBtn}
            onClick={() => onPick(s)}
          >
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 17 }}>{s.preview}</span>
              <span
                style={{
                  fontSize: 12,
                  color: "var(--hux-fg-dim)",
                  letterSpacing: "0.04em",
                }}
              >
                {s.when} {"\u00b7"} {s.duration} {"\u00b7"}{" "}
                {t("sessions.turnsCount", { count: s.turns })}
              </span>
            </span>
            <span style={{ fontSize: 13, color: "var(--hux-fg-dim)" }}>
              {"\u2192"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
