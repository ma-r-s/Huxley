// Hero — orb + headline + install chip. The orb follows the active scroll
// state, so as you read down it literally narrates each section.

import { useTranslation } from "react-i18next";
import { useRegisterSection, useVoiceState } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { Orb, type OrbState } from "../components/Orb.js";
import { chipGhost, chipSolid } from "../components/Chrome.js";

export function Hero() {
  const { t } = useTranslation();
  const heroRef = useRegisterSection<HTMLElement>("hero", "idle");
  const { state: activeState } = useVoiceState();
  const { isMobile, isTablet } = useViewport();
  // The orb has no "interrupt" state — fall back to listening for that one beat.
  const orbState: OrbState =
    activeState === "interrupt" ? "listening" : (activeState as OrbState);
  const orbSize = isMobile ? 220 : isTablet ? 280 : 360;
  const statusKey = activeState as
    | "idle"
    | "listening"
    | "thinking"
    | "speaking"
    | "interrupt";
  const statusLine = t(`hero.status.${statusKey}.line`);
  const statusSub = t(`hero.status.${statusKey}.sub`);

  return (
    <section
      ref={heroRef}
      style={{
        position: "relative",
        zIndex: 2,
        minHeight: isMobile ? "auto" : 720,
        display: "grid",
        gridTemplateColumns: isMobile || isTablet ? "1fr" : "1.2fr 1fr",
        alignItems: "center",
        gap: isMobile ? 32 : 64,
        padding: isMobile ? "32px 24px 56px" : "64px 64px 96px",
      }}
    >
      <div style={{ order: isMobile ? 2 : 1 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 12px",
            borderRadius: 999,
            border: "1px solid var(--hux-fg-line)",
            fontSize: 11,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            marginBottom: 28,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background: "var(--hux-fg)",
              boxShadow: "0 0 8px var(--hux-fg)",
            }}
          />
          {t("hero.pill")}
        </div>
        <h1
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(56px, 7vw, 104px)",
            lineHeight: 0.98,
            letterSpacing: "-0.015em",
            margin: "0 0 28px",
            textWrap: "balance",
          }}
        >
          {t("hero.titleLine1")}
          <br />
          <em style={{ fontStyle: "italic" }}>{t("hero.titleLine2")}</em>
        </h1>
        <p
          style={{
            fontFamily: "var(--hux-serif)",
            fontStyle: "italic",
            fontSize: 22,
            lineHeight: 1.4,
            maxWidth: 520,
            margin: "0 0 40px",
            opacity: 0.88,
            textWrap: "pretty",
          }}
        >
          {t("hero.subtitle")}
        </p>
        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <a
            style={{ ...chipSolid, padding: "12px 20px", fontSize: 13 }}
            href="#install"
          >
            {t("hero.ctaInstall")}
          </a>
          <a
            style={{ ...chipGhost, padding: "12px 20px", fontSize: 13 }}
            href="#architecture"
          >
            {t("hero.ctaSeeHow")}
          </a>
          <code
            style={{
              fontFamily: "var(--hux-mono)",
              fontSize: 12,
              padding: "10px 14px",
              borderRadius: 10,
              background: "rgba(0,0,0,0.22)",
              color: "var(--hux-fg)",
              letterSpacing: "0.01em",
              border: "1px solid var(--hux-fg-line)",
            }}
          >
            {t("hero.installSnippet")}
          </code>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          order: isMobile ? 1 : 2,
        }}
      >
        <Orb
          size={orbSize}
          state={orbState}
          color="var(--hux-fg)"
          expressiveness={1.1}
        />
        <div
          style={{
            marginTop: 28,
            textAlign: "center",
            fontFamily: "var(--hux-serif)",
            fontStyle: "italic",
            fontSize: 22,
            letterSpacing: "-0.005em",
            minHeight: 32,
            opacity: 0.88,
            transition: "opacity 300ms ease",
          }}
        >
          {statusLine}
        </div>
        <div
          style={{
            marginTop: 10,
            fontFamily: "var(--hux-mono)",
            fontSize: 10,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            opacity: 0.55,
          }}
        >
          {statusSub} · {activeState}
        </div>
      </div>
    </section>
  );
}
