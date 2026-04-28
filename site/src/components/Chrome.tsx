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
export function BalNav() {
  const { t } = useTranslation();
  const { isMobile, isTablet } = useViewport();
  const showLinks = !isMobile && !isTablet;
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
      {showLinks && (
        <div
          style={{
            display: "flex",
            gap: 32,
            fontSize: 13,
            letterSpacing: "0.02em",
            opacity: 0.85,
          }}
        >
          <a style={navA} href="#problem">
            {t("nav.why")}
          </a>
          <a style={navA} href="#architecture">
            {t("nav.architecture")}
          </a>
          <a style={navA} href="#skills">
            {t("nav.skills")}
          </a>
          <a style={navA} href="#today">
            {t("nav.today")}
          </a>
          <a style={navA} href="#grows">
            {t("nav.grows")}
          </a>
          <a style={navA} href="#persona">
            {t("nav.personas")}
          </a>
          <a style={navA} href="#install">
            {t("nav.install")}
          </a>
        </div>
      )}
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

const navA: CSSProperties = {
  color: "inherit",
  textDecoration: "none",
  fontFamily: "var(--hux-sans)",
};

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
              opacity: 0.78,
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
