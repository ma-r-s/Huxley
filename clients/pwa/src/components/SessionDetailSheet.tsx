// Read-only transcript view for a single past session (T1.12). Opens
// when the user clicks a row in SessionsSheet; closes back to the list
// or after the row is deleted.

import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { SessionTurn } from "../types.js";

const sheetStyles = {
  sheet: {
    position: "absolute" as const,
    inset: 0,
    zIndex: 31,
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
  turn: {
    padding: "10px 0",
    borderBottom: "1px solid var(--hux-fg-line)",
    fontFamily: "var(--hux-sans)",
    fontSize: 15,
    lineHeight: 1.4,
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
  },
  roleLabel: {
    fontSize: 10,
    letterSpacing: "0.14em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
  },
  text: {
    color: "var(--hux-fg)",
    overflowWrap: "anywhere" as const,
    whiteSpace: "pre-wrap" as const,
  },
  toolbar: {
    display: "flex",
    justifyContent: "flex-end",
    marginBottom: 16,
  },
  deleteBtn: {
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg-dim)",
    padding: "6px 14px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 11,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    cursor: "pointer",
  },
};

interface SessionDetailSheetProps {
  onClose: () => void;
  sessionId: number;
  // null = not yet loaded, OR loaded for a different id (we ignore
  // stale data and re-fetch on mount).
  detail: { id: number; turns: SessionTurn[] } | null;
  onMount: () => void;
  onDelete: () => void;
  sheetClassName?: string;
}

export function SessionDetailSheet({
  onClose,
  sessionId,
  detail,
  onMount,
  onDelete,
  sheetClassName = "hux-sheet",
}: SessionDetailSheetProps) {
  const { t } = useTranslation();

  // Fetch on mount. Mount-only — re-renders from new detail data
  // re-render the sheet, no refetch needed.
  useEffect(() => {
    onMount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Only show turns when the loaded detail matches our session id.
  // Anything else is stale (e.g. the previous detail still in
  // useWs state from a different click) — render the loading state.
  const turns = detail?.id === sessionId ? detail.turns : null;

  return (
    <div style={sheetStyles.sheet} className={sheetClassName}>
      <div style={sheetStyles.header}>
        <span>{t("sessionDetail.recent")}</span>
        <button style={sheetStyles.closeBtn} onClick={onClose}>
          {t("sessionDetail.back")}
        </button>
      </div>
      <div style={sheetStyles.body}>
        <h2
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(28px, 6vw, 44px)",
            lineHeight: 1.05,
            margin: "8px 0 24px",
            letterSpacing: "-0.01em",
          }}
        >
          {t("sessionDetail.title")}
        </h2>
        <div style={sheetStyles.toolbar}>
          <button
            style={sheetStyles.deleteBtn}
            onClick={() => {
              if (window.confirm(t("sessionDetail.confirmDelete"))) {
                onDelete();
              }
            }}
          >
            {t("sessionDetail.delete")}
          </button>
        </div>
        {turns === null && (
          <div
            style={{
              fontFamily: "var(--hux-sans)",
              color: "var(--hux-fg-dim)",
              fontSize: 15,
              padding: "20px 0",
            }}
          >
            {t("sessionDetail.loading")}
          </div>
        )}
        {turns !== null && turns.length === 0 && (
          <div
            style={{
              fontFamily: "var(--hux-sans)",
              color: "var(--hux-fg-dim)",
              fontSize: 15,
              padding: "20px 0",
            }}
          >
            {t("sessionDetail.empty")}
          </div>
        )}
        {turns?.map((turn) => (
          <div key={turn.idx} style={sheetStyles.turn}>
            <span style={sheetStyles.roleLabel}>
              {turn.role === "user"
                ? t("sessionDetail.user")
                : t("sessionDetail.assistant")}
            </span>
            <span style={sheetStyles.text}>{turn.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
