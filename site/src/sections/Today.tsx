// § 06 — Today: real metrics + curated roadmap.
//
// Numbers come straight from the codebase as of the build date. Drift over
// time, so worth refreshing periodically (or wiring to a generator script).
// Roadmap items are curated highlights from docs/roadmap.md — not exhaustive.

import { useTranslation } from "react-i18next";
import { useRegisterSection } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { SectionHead, paperSection } from "../components/Chrome.js";
import { Reveal } from "../components/Reveal.js";

interface Metric {
  /** translation key under today.metrics — points at { value, label } */
  id: string;
  /** the proof number itself (literal — same in every locale) */
  value: string;
}

const METRICS: Metric[] = [
  { id: "tests", value: "678" },
  { id: "loc", value: "15K" },
  { id: "skills", value: "6" },
  { id: "personas", value: "2" },
  { id: "adrs", value: "17" },
  { id: "license", value: "MIT" },
];

interface RoadmapItem {
  id: string;
  /** Tier label shown as a tiny pill */
  tier: "P1" | "Later" | "Firmware";
}

const ROADMAP: RoadmapItem[] = [
  { id: "cookbook", tier: "P1" },
  { id: "secrets", tier: "P1" },
  { id: "providerAbstraction", tier: "Later" },
  { id: "esp32", tier: "Firmware" },
];

export function Today() {
  const { t } = useTranslation();
  const sectionRef = useRegisterSection<HTMLElement>("today", "thinking");
  const { isMobile } = useViewport();

  return (
    <section
      ref={sectionRef}
      id="today"
      style={{
        ...paperSection,
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
      }}
    >
      <SectionHead
        eyebrow={t("today.eyebrow")}
        title={
          <>
            {t("today.titleA")}
            <br />
            <em style={{ fontStyle: "italic" }}>{t("today.titleB")}</em>
          </>
        }
        subtitle={t("today.subtitle")}
      />

      {/* Metrics row */}
      <Reveal delay={250} y={28} duration={750}>
        <div
          style={{
            marginTop: isMobile ? 40 : 64,
            display: "grid",
            gridTemplateColumns: isMobile ? "repeat(2, 1fr)" : "repeat(6, 1fr)",
            gap: 0,
            borderTop: "1px solid var(--hux-fg-line)",
            borderLeft: "1px solid var(--hux-fg-line)",
          }}
        >
          {METRICS.map((m) => (
            <div
              key={m.id}
              style={{
                borderRight: "1px solid var(--hux-fg-line)",
                borderBottom: "1px solid var(--hux-fg-line)",
                padding: "28px 24px",
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <div
                style={{
                  fontFamily: "var(--hux-serif)",
                  fontSize: isMobile ? 40 : 56,
                  lineHeight: 1,
                  letterSpacing: "-0.02em",
                  fontStyle: "italic",
                  textShadow:
                    "0 0 24px color-mix(in oklab, var(--hux-fg) 25%, transparent)",
                }}
              >
                {m.value}
              </div>
              <div
                className="mono"
                style={{
                  fontSize: 10,
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                  opacity: 0.7,
                }}
              >
                {t(`today.metrics.${m.id}`)}
              </div>
            </div>
          ))}
        </div>
      </Reveal>

      {/* Roadmap */}
      <div
        style={{
          marginTop: isMobile ? 56 : 88,
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "0.8fr 1.2fr",
          gap: isMobile ? 24 : 64,
          alignItems: "start",
        }}
      >
        <div>
          <div className="eyebrow" style={{ opacity: 0.6, marginBottom: 12 }}>
            {t("today.roadmapEyebrow")}
          </div>
          <h3
            style={{
              fontFamily: "var(--hux-serif)",
              fontWeight: 400,
              fontSize: 32,
              lineHeight: 1.1,
              letterSpacing: "-0.01em",
              margin: "0 0 12px",
            }}
          >
            {t("today.roadmapTitle")}
          </h3>
          <p
            style={{
              fontFamily: "var(--hux-serif)",
              fontStyle: "italic",
              fontSize: 16,
              lineHeight: 1.5,
              opacity: 0.78,
              maxWidth: 360,
            }}
          >
            {t("today.roadmapSubtitle")}
          </p>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 0,
            borderTop: "1px solid var(--hux-fg-line)",
          }}
        >
          {ROADMAP.map((item, i) => (
            <Reveal key={item.id} delay={i * 90} y={14} duration={600}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: isMobile ? "1fr auto" : "auto 1fr auto",
                  gap: isMobile ? 12 : 20,
                  alignItems: "baseline",
                  padding: "18px 0",
                  borderBottom: "1px solid var(--hux-fg-line)",
                }}
              >
                {!isMobile && (
                  <div
                    className="mono"
                    style={{
                      fontSize: 10,
                      letterSpacing: "0.18em",
                      textTransform: "uppercase",
                      opacity: 0.5,
                      minWidth: 30,
                    }}
                  >
                    {String(i + 1).padStart(2, "0")}
                  </div>
                )}
                <div
                  style={{
                    fontFamily: "var(--hux-serif)",
                    fontSize: isMobile ? 18 : 22,
                    lineHeight: 1.3,
                  }}
                >
                  {t(`today.roadmap.${item.id}.k`)}
                  <div
                    style={{
                      fontFamily: "var(--hux-sans)",
                      fontStyle: "normal",
                      fontSize: 13,
                      lineHeight: 1.5,
                      opacity: 0.72,
                      marginTop: 4,
                      textWrap: "pretty",
                    }}
                  >
                    {t(`today.roadmap.${item.id}.v`)}
                  </div>
                </div>
                <span
                  style={{
                    fontFamily: "var(--hux-mono)",
                    fontSize: 9,
                    letterSpacing: "0.18em",
                    textTransform: "uppercase",
                    padding: "4px 9px",
                    border: "1px solid var(--hux-fg-line)",
                    borderRadius: 999,
                    opacity: 0.75,
                    whiteSpace: "nowrap",
                    alignSelf: "start",
                  }}
                >
                  {item.tier}
                </span>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
