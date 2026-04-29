// Cross-section chrome: backdrop, nav, section header, chip styles.
// Stays in one place so sections don't redefine the same primitives.

import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useViewport } from "../lib/useViewport.js";
import { LANG_LABEL, SUPPORTED_LANGS, type LangCode } from "../i18n/index.js";
import { Reveal } from "./Reveal.js";
import { Wordmark } from "./Wordmark.js";

// Coral radial warmth + subtle film grain. Pinned to the page background.
export function BalancedBackdrop() {
  return (
    <>
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 0,
          background: `
            radial-gradient(ellipse at 50% 20%, color-mix(in oklab, white 6%, transparent) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 100%, color-mix(in oklab, black 35%, transparent) 0%, transparent 55%)
          `,
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 0,
          opacity: 0.06,
          mixBlendMode: "overlay",
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.55 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>\")",
        }}
      />
    </>
  );
}

// ── Nav bar ────────────────────────────────────────────────────────────
// Wordmark on the left, language picker + external links on the right.
// In-page section links were removed — they were just scrolling within
// the same page, not leading anywhere useful.
export function BalNav() {
  const { t } = useTranslation();
  const { isMobile } = useViewport();
  return (
    <nav
      style={{
        position: "relative",
        zIndex: 42,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
        padding: isMobile ? "20px 24px" : "28px 48px",
        background: "var(--hux-coral)",
      }}
    >
      <Wordmark
        size={isMobile ? 26 : 32}
        subtle={isMobile ? undefined : t("nav.wordmarkSubtle")}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: isMobile ? 8 : 12,
        }}
      >
        <LangToggle />
        {!isMobile && (
          <a style={chipGhost} href="#">
            {t("nav.docs")}
          </a>
        )}
        <a style={chipSolid} href="https://github.com/ma-r-s/Huxley">
          {t("nav.github")} {!isMobile && "↗"}
        </a>
      </div>
    </nav>
  );
}

// Segmented language picker — drives i18next + persists to localStorage
// (the i18next LanguageDetector handles the persistence; we just call
// changeLanguage and React re-renders via the t() hook).
function LangToggle() {
  const { i18n } = useTranslation();
  const current = (i18n.resolvedLanguage as LangCode) || "en";
  return (
    <div
      role="group"
      aria-label="Language"
      style={{
        display: "inline-flex",
        padding: 2,
        borderRadius: 999,
        border: "1px solid var(--hux-fg-line)",
        background: "rgba(0,0,0,0.06)",
        fontFamily: "var(--hux-mono)",
      }}
    >
      {SUPPORTED_LANGS.map((code) => {
        const active = current === code;
        return (
          <button
            key={code}
            onClick={() => i18n.changeLanguage(code)}
            aria-pressed={active}
            style={{
              appearance: "none",
              border: "none",
              cursor: "pointer",
              padding: "5px 10px",
              minWidth: 30,
              borderRadius: 999,
              fontFamily: "inherit",
              fontSize: 11,
              letterSpacing: "0.14em",
              color: active ? "var(--hux-coral)" : "var(--hux-fg)",
              background: active ? "var(--hux-fg)" : "transparent",
              opacity: active ? 1 : 0.7,
              transition:
                "background 160ms ease, color 160ms ease, opacity 160ms ease",
            }}
          >
            {LANG_LABEL[code]}
          </button>
        );
      })}
    </div>
  );
}

// ── Section surface helpers ─────────────────────────────────────────────
// Atmospheric inner gradients — give each surface depth so it reads as
// a "place" instead of a flat block. Three layers per surface: a raking
// highlight from the upper-left, a deep vignette in the lower-right,
// and a horizon shadow at the bottom edge that grounds the section
// against the next one. Opacities pushed high enough to actually read
// (the previous 6%/10% mixes were below the JND on saturated coral).
const ATMOSPHERE_DARK = `
  radial-gradient(ellipse 70% 50% at 15% 0%, color-mix(in oklab, white 18%, transparent), transparent 65%),
  radial-gradient(ellipse 90% 70% at 85% 100%, color-mix(in oklab, black 50%, transparent), transparent 65%),
  linear-gradient(to bottom, transparent 70%, color-mix(in oklab, black 22%, transparent) 100%)
`;
const ATMOSPHERE_PAPER = `
  radial-gradient(ellipse 60% 45% at 20% 0%, color-mix(in oklab, white 55%, transparent), transparent 70%),
  radial-gradient(ellipse 90% 70% at 80% 100%, color-mix(in oklab, var(--hux-coral) 28%, transparent), transparent 65%),
  linear-gradient(to bottom, transparent 75%, color-mix(in oklab, var(--hux-ink) 14%, transparent) 100%)
`;

// CSS-variable overrides for paper (light) sections. Spread into the
// section root's style and any descendant reading --hux-fg / --hux-fg-line
// / --hux-fg-faint automatically picks up the inverted dark variants.
// No per-element color rewriting needed.
export const paperSection: CSSProperties = {
  background: `${ATMOSPHERE_PAPER}, var(--hux-paper)`,
  color: "var(--hux-ink)",
  ["--hux-fg" as string]: "var(--hux-ink)",
  ["--hux-fg-dim" as string]: "var(--hux-ink-dim)",
  ["--hux-fg-line" as string]: "var(--hux-ink-line)",
  ["--hux-fg-faint" as string]: "var(--hux-ink-faint)",
} as CSSProperties;

export const deepSection: CSSProperties = {
  background: `${ATMOSPHERE_DARK}, var(--hux-coral-xdk)`,
};

export const coralSection: CSSProperties = {
  background: `${ATMOSPHERE_DARK}, var(--hux-coral)`,
};

export const chipGhost: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "8px 14px",
  borderRadius: 999,
  border: "1px solid var(--hux-fg-line)",
  color: "var(--hux-fg)",
  textDecoration: "none",
  fontFamily: "var(--hux-sans)",
  fontSize: 12,
  letterSpacing: "0.04em",
};

export const chipSolid: CSSProperties = {
  ...chipGhost,
  background: "var(--hux-fg)",
  color: "var(--hux-coral)",
  border: "1px solid var(--hux-fg)",
  fontWeight: 500,
};

// ── Section header — eyebrow + serif title + italic subtitle ─────────────
interface SectionHeadProps {
  eyebrow: string;
  title: ReactNode;
  subtitle?: ReactNode;
}

export function SectionHead({ eyebrow, title, subtitle }: SectionHeadProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 18,
        maxWidth: 840,
      }}
    >
      <Reveal delay={0} y={12} duration={500}>
        <div className="eyebrow" style={{ opacity: 0.65 }}>
          {eyebrow}
        </div>
      </Reveal>
      <Reveal delay={90} y={28} duration={750}>
        <h2
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(40px, 5.2vw, 72px)",
            lineHeight: 1.02,
            letterSpacing: "-0.015em",
            margin: 0,
          }}
        >
          {title}
        </h2>
      </Reveal>
      {subtitle && (
        <Reveal delay={220} y={18} duration={650}>
          <div
            style={{
              fontFamily: "var(--hux-serif)",
              fontStyle: "italic",
              fontSize: 22,
              lineHeight: 1.4,
              // Was 0.78 — too faint on the dark-coral section backgrounds,
              // especially on mobile where everything is more compact.
              opacity: 0.92,
              maxWidth: 640,
            }}
          >
            {subtitle}
          </div>
        </Reveal>
      )}
    </div>
  );
}
