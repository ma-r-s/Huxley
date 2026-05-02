// Full-page Skills + Marketplace sheet (Marketplace v2 Phase A + C).
//
// Two tabs:
//   Installed   — cards for every entry-point-discoverable skill in
//                 the active venv. Tap → SkillConfigSheet (read +
//                 write per Phase B).
//   Marketplace — cards from the canonical huxley-registry feed.
//                 Browse-only in Phase C; Phase D adds an Install
//                 button that drives `uv add` server-side.
//
// Both tabs share the same card grid + visual rhythm. The
// "Installed ✓" badge surfaces on Marketplace cards whose package
// is also entry-point-discoverable in the active venv (the server
// decorates with `installed: bool`).

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  MarketplaceEntry,
  MarketplaceState,
  SkillSummary,
  SkillsState,
} from "../types.js";
import { prettyLabel } from "../lib/schemaForm.js";

// Position fixed (not absolute) so the sheet escapes the
// `.hux-stage`'s 720px max-width column on desktop. The card grid
// needs the full viewport width to breathe; the inner `.body`
// caps at 1100px so cards don't stretch ugly on ultra-wide displays.
// Stage container has no transform/filter/will-change so fixed
// resolves against the viewport, not the stage.
const S = {
  sheet: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 30,
    background: "var(--hux-bg)",
    color: "var(--hux-fg)",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
  },
  // Outer header is full-width (background + border bleed to edges).
  // Inner row caps at the same maxWidth as the body so the eyebrow
  // and close button align with the content column on ultra-wide
  // displays. Without this, the close button drifts far from the
  // cards on a 27" monitor.
  headerOuter: {
    flexShrink: 0,
    width: "100%",
  },
  headerInner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "20px 32px 12px",
    width: "100%",
    maxWidth: 1100,
    margin: "0 auto",
    boxSizing: "border-box" as const,
    fontFamily: "var(--hux-sans)",
    fontSize: 14,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
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
    padding: "8px 32px 48px",
    width: "100%",
    maxWidth: 1100,
    margin: "0 auto",
    boxSizing: "border-box" as const,
  },
  title: {
    fontFamily: "var(--hux-serif)",
    fontWeight: 400,
    fontSize: "clamp(34px, 8vw, 56px)",
    lineHeight: 1.05,
    margin: "8px 0 24px",
    letterSpacing: "-0.01em",
  },
  tabs: {
    display: "flex",
    gap: 6,
    marginBottom: 24,
    borderBottom: "1px solid var(--hux-fg-line)",
  },
  tabBtn: (active: boolean) => ({
    background: "transparent",
    border: "none",
    borderBottom: "2px solid " + (active ? "var(--hux-fg)" : "transparent"),
    color: active ? "var(--hux-fg)" : "var(--hux-fg-dim)",
    fontFamily: "var(--hux-sans)",
    fontSize: 13,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    padding: "10px 14px",
    marginBottom: -1,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    gap: 8,
  }),
  tabCount: {
    fontSize: 11,
    color: "var(--hux-fg-dim)",
    fontVariantNumeric: "tabular-nums" as const,
  },
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
    gap: 14,
  },
  card: {
    textAlign: "left" as const,
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    borderRadius: 12,
    color: "var(--hux-fg)",
    padding: "18px 18px 16px",
    cursor: "pointer",
    fontFamily: "var(--hux-sans)",
    display: "flex",
    flexDirection: "column" as const,
    gap: 10,
    minHeight: 132,
    transition: "border-color 200ms ease, transform 120ms ease",
  },
  cardName: {
    fontFamily: "var(--hux-serif)",
    fontSize: 22,
    lineHeight: 1.05,
    fontWeight: 400,
    letterSpacing: "-0.01em",
    display: "flex",
    alignItems: "center",
    gap: 10,
  },
  enabledDot: (enabled: boolean) => ({
    display: "inline-block",
    width: 8,
    height: 8,
    borderRadius: 999,
    background: enabled ? "var(--hux-fg)" : "transparent",
    border: "1px solid " + (enabled ? "var(--hux-fg)" : "var(--hux-fg-line)"),
    boxShadow: enabled ? "0 0 8px var(--hux-fg)" : "none",
    flexShrink: 0,
  }),
  cardDesc: {
    fontSize: 14,
    color: "var(--hux-fg-dim)",
    lineHeight: 1.4,
    flex: 1,
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical" as const,
    overflow: "hidden",
  },
  cardMeta: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline",
    fontSize: 12,
    color: "var(--hux-fg-dim)",
    letterSpacing: "0.04em",
    paddingTop: 8,
    borderTop: "1px solid var(--hux-fg-line)",
    marginTop: "auto",
  },
  marketplacePlaceholder: {
    fontFamily: "var(--hux-sans)",
    fontSize: 15,
    lineHeight: 1.45,
    color: "var(--hux-fg-dim)",
    padding: "32px 0",
    maxWidth: 540,
  },
  empty: {
    fontFamily: "var(--hux-sans)",
    color: "var(--hux-fg-dim)",
    fontSize: 15,
    padding: "24px 0",
    lineHeight: 1.45,
  },
};

type Tab = "installed" | "marketplace";

interface Props {
  skillsState: SkillsState | null;
  marketplaceState: MarketplaceState | null;
  onClose: () => void;
  onPickSkill: (skill: SkillSummary) => void;
  onPickMarketplaceSkill?: (entry: MarketplaceEntry) => void;
  // Fired once on mount so the panel refreshes whenever the user
  // opens it. Without this, the sheet would rely on DeviceSheet's
  // mount-time fetch — a race when the user navigates fast OR when
  // StrictMode's double-mount swallows the first effect. The
  // server's persona-swap push (Phase A critic fix #3) covers
  // mid-session swaps; this covers the cold-open path.
  onRequestSkillsState: () => void;
  // Phase C — fired the first time the user opens the Marketplace
  // tab so the registry feed loads on demand (not at sheet mount,
  // since most opens just want to configure existing skills).
  onRequestMarketplace: () => void;
  // Sheet wrapper class — `hux-sheet` runs the fade-up animation,
  // `hux-sheet hux-sheet-no-anim` skips it. App passes the no-anim
  // variant when transitioning sheet → sheet so the user doesn't
  // see the underlying view briefly through the fading-in sheet.
  sheetClassName?: string;
}

export function SkillsSheet({
  skillsState,
  marketplaceState,
  onClose,
  onPickSkill,
  onPickMarketplaceSkill,
  onRequestSkillsState,
  onRequestMarketplace,
  sheetClassName = "hux-sheet",
}: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("installed");
  const installed = skillsState?.skills ?? [];
  const installedCount = installed.length;

  useEffect(() => {
    onRequestSkillsState();
  }, [onRequestSkillsState]);

  return (
    <div style={S.sheet} className={sheetClassName}>
      <div style={S.headerOuter}>
        <div style={S.headerInner}>
          <span>{t("skillsSheet.eyebrow", "Skills")}</span>
          <button style={S.closeBtn} onClick={onClose}>
            {t("skillsSheet.close", "Close")}
          </button>
        </div>
      </div>
      <div style={S.body}>
        <h2 style={S.title}>{t("skillsSheet.headline", "Your skills")}</h2>

        <div style={S.tabs}>
          <button
            style={S.tabBtn(tab === "installed")}
            onClick={() => setTab("installed")}
          >
            <span>{t("skillsSheet.tabs.installed", "Installed")}</span>
            {skillsState !== null && (
              <span style={S.tabCount}>{installedCount}</span>
            )}
          </button>
          <button
            style={S.tabBtn(tab === "marketplace")}
            onClick={() => {
              setTab("marketplace");
              // Phase C: lazy-fetch the feed when the user opens the
              // tab. Subsequent opens hit the server's 1h cache.
              if (marketplaceState === null) onRequestMarketplace();
            }}
          >
            <span>{t("skillsSheet.tabs.marketplace", "Marketplace")}</span>
            {marketplaceState !== null &&
              marketplaceState.skills.length > 0 && (
                <span style={S.tabCount}>{marketplaceState.skills.length}</span>
              )}
          </button>
        </div>

        {tab === "installed" && (
          <InstalledTab skillsState={skillsState} onPickSkill={onPickSkill} />
        )}
        {tab === "marketplace" && (
          <MarketplaceTab
            state={marketplaceState}
            onPick={onPickMarketplaceSkill}
            onRetry={onRequestMarketplace}
          />
        )}
      </div>
    </div>
  );
}

interface InstalledProps {
  skillsState: SkillsState | null;
  onPickSkill: (skill: SkillSummary) => void;
}

function InstalledTab({ skillsState, onPickSkill }: InstalledProps) {
  const { t } = useTranslation();
  if (skillsState === null) {
    return (
      <div style={S.empty}>
        {t("skillsSheet.installed.loading", "Loading…")}
      </div>
    );
  }
  if (skillsState.skills.length === 0) {
    return (
      <div style={S.empty}>
        {t(
          "skillsSheet.installed.empty",
          "No skills installed. Add one with `uv add huxley-skill-<name>` and restart the server.",
        )}
      </div>
    );
  }
  return (
    <div style={S.cardGrid}>
      {skillsState.skills.map((skill) => (
        <SkillCard
          key={skill.name}
          skill={skill}
          onClick={() => onPickSkill(skill)}
        />
      ))}
    </div>
  );
}

interface CardProps {
  skill: SkillSummary;
  onClick: () => void;
}

function SkillCard({ skill, onClick }: CardProps) {
  const { t } = useTranslation();
  return (
    <button
      style={S.card}
      onClick={onClick}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor =
          "var(--hux-fg)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor =
          "var(--hux-fg-line)";
      }}
    >
      <div style={S.cardName}>
        <span style={S.enabledDot(skill.enabled)} />
        <span>{prettyLabel(skill.name)}</span>
      </div>
      <div style={S.cardDesc}>
        {skill.description ??
          t("skillsSheet.card.noDescription", "No description provided.")}
      </div>
      <div style={S.cardMeta}>
        <span>{skill.author ?? "—"}</span>
        <span style={{ fontVariantNumeric: "tabular-nums" }}>
          {skill.version ? `v${skill.version}` : ""}
        </span>
      </div>
    </button>
  );
}

interface MarketplaceTabProps {
  state: MarketplaceState | null;
  onPick: ((entry: MarketplaceEntry) => void) | undefined;
  onRetry: () => void;
}

function MarketplaceTab({ state, onPick, onRetry }: MarketplaceTabProps) {
  const { t } = useTranslation();
  if (state === null) {
    return (
      <div style={S.empty}>
        {t("skillsSheet.marketplace.loading", "Loading registry…")}
      </div>
    );
  }
  if (state.skills.length === 0) {
    return (
      <div style={S.empty}>
        <p style={{ marginTop: 0 }}>
          {state.error
            ? state.error
            : t(
                "skillsSheet.marketplace.empty",
                "Registry is empty. Submit a PR at ma-r-s/huxley-registry to add a skill.",
              )}
        </p>
        <button
          style={{
            background: "transparent",
            border: "1px solid var(--hux-fg-line)",
            color: "var(--hux-fg)",
            padding: "6px 14px",
            borderRadius: 999,
            cursor: "pointer",
            fontFamily: "var(--hux-sans)",
            fontSize: 12,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
          }}
          onClick={onRetry}
        >
          {t("skillsSheet.marketplace.retry", "Retry")}
        </button>
      </div>
    );
  }
  return (
    <div>
      {state.stale && state.error && (
        <div
          style={{
            fontFamily: "var(--hux-sans)",
            fontSize: 12,
            color: "var(--hux-fg-dim)",
            padding: "8px 12px",
            border: "1px solid var(--hux-fg-line)",
            borderRadius: 8,
            marginBottom: 16,
          }}
        >
          {t(
            "skillsSheet.marketplace.staleHint",
            "Showing cached registry — couldn't reach the live feed.",
          )}
        </div>
      )}
      <div style={S.cardGrid}>
        {state.skills.map((entry) => (
          <MarketplaceCard
            key={entry.namespace ?? entry.name}
            entry={entry}
            onClick={onPick ? () => onPick(entry) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

interface MarketplaceCardProps {
  entry: MarketplaceEntry;
  onClick: (() => void) | undefined;
}

function MarketplaceCard({ entry, onClick }: MarketplaceCardProps) {
  const { t } = useTranslation();
  const tierLabel =
    entry.tier === "first-party"
      ? t("skillsSheet.marketplace.tierFirst", "First-party")
      : entry.tier === "experimental"
        ? t("skillsSheet.marketplace.tierExperimental", "Experimental")
        : t("skillsSheet.marketplace.tierCommunity", "Community");
  const display =
    entry.display_name ?? prettyLabel(entry.name.replace(/^huxley-skill-/, ""));
  return (
    <button
      style={S.card}
      onClick={onClick}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor =
          "var(--hux-fg)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor =
          "var(--hux-fg-line)";
      }}
    >
      <div style={S.cardName}>
        <span>{display}</span>
        {entry.installed && (
          <span
            style={{
              fontSize: 10,
              fontFamily: "var(--hux-sans)",
              letterSpacing: "0.10em",
              textTransform: "uppercase",
              color: "var(--hux-fg)",
              border: "1px solid var(--hux-fg)",
              borderRadius: 999,
              padding: "1px 8px",
            }}
          >
            {t("skillsSheet.marketplace.installed", "Installed ✓")}
          </span>
        )}
      </div>
      <div style={S.cardDesc}>
        {entry.tagline ??
          t("skillsSheet.card.noDescription", "No description provided.")}
      </div>
      <div style={S.cardMeta}>
        <span
          style={{
            fontSize: 10,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          {tierLabel}
        </span>
        <span style={{ fontVariantNumeric: "tabular-nums" }}>
          {entry.version ? `v${entry.version}` : ""}
        </span>
      </div>
    </button>
  );
}
