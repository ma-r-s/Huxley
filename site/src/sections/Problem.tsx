// Problem comparison — five rows. As the section scrolls into view,
// competitor rows desaturate while the Huxley row warms up + glows.
// Reinforces the argument visually as you read it.

import { useEffect, useState } from "react";
import { useRegisterSection, useVoiceState } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { SectionHead } from "../components/Chrome.js";

interface Row {
  name: string;
  bad: string[];
  good: string[];
  hero?: boolean;
}

const ROWS: Row[] = [
  {
    name: "Alexa / Google Home",
    bad: ["Walled garden", "Cloud-only", "Certification fees"],
    good: [],
  },
  {
    name: "OpenAI voice mode",
    bad: ["One voice, one brain", "No self-hosting", "No custom skills"],
    good: [],
  },
  {
    name: "Pipecat / LiveKit",
    bad: [
      "Blank-slate pipelines",
      "You solve turns, interrupts, audio collisions",
    ],
    good: [],
  },
  {
    name: "Build it yourself",
    bad: ["Six months of plumbing before the first feature"],
    good: [],
  },
  {
    name: "Huxley",
    hero: true,
    bad: [],
    good: [
      "Opinionated on turn sequencing and audio flow",
      "Open for skills, personas, and clients",
      "Self-hosted Python — your box, your keys",
    ],
  },
];

export function Problem() {
  const sectionRef = useRegisterSection<HTMLElement>("problem", "listening");
  const { id: activeSection, scrollProgress } = useVoiceState();
  const { isMobile, isTablet } = useViewport();

  // Re-derive a per-section reveal ramp 0..1 from scroll progress while this
  // section is the active one. We freeze it once we leave so the row state
  // doesn't snap back as you scroll past.
  const [reveal, setReveal] = useState(0);
  useEffect(() => {
    if (activeSection === "problem") setReveal(scrollProgress);
  }, [activeSection, scrollProgress]);

  return (
    <section
      ref={sectionRef}
      id="problem"
      style={{
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
        borderTop: "1px solid var(--hux-fg-line)",
        background: "color-mix(in oklab, var(--hux-coral) 92%, black)",
      }}
    >
      <SectionHead
        eyebrow="§ 01 — The problem"
        title={
          <>
            Every voice platform asks you to
            <br />
            <em style={{ fontStyle: "italic" }}>
              give up something essential.
            </em>
          </>
        }
        subtitle="Ownership. Extensibility. Or the first six months of your life."
      />
      <div
        style={{
          marginTop: isMobile ? 40 : 72,
          display: "grid",
          gridTemplateColumns: isMobile
            ? "1fr"
            : isTablet
              ? "repeat(2, 1fr)"
              : "repeat(5, 1fr)",
          gap: 0,
          borderTop: "1px solid var(--hux-fg-line)",
          borderLeft: "1px solid var(--hux-fg-line)",
        }}
      >
        {ROWS.map((r, i) => {
          // Stagger row engagement across the section's first 60% scroll.
          const rowStart = (i / ROWS.length) * 0.6;
          const rowEnd = rowStart + 0.25;
          const rowP = Math.max(
            0,
            Math.min(1, (reveal - rowStart) / (rowEnd - rowStart)),
          );
          const isHero = !!r.hero;
          const compDim = isHero ? 0 : rowP * 0.35;
          const heroLift = isHero ? rowP : 0;

          return (
            <div
              key={r.name}
              style={{
                borderRight: "1px solid var(--hux-fg-line)",
                borderBottom: "1px solid var(--hux-fg-line)",
                padding: "28px 24px",
                background: isHero
                  ? `color-mix(in oklab, var(--hux-fg) ${8 + heroLift * 14}%, transparent)`
                  : "transparent",
                boxShadow:
                  isHero && heroLift > 0
                    ? `inset 0 0 0 1px color-mix(in oklab, var(--hux-fg) ${heroLift * 40}%, transparent)`
                    : "none",
                minHeight: 280,
                display: "flex",
                flexDirection: "column",
                gap: 16,
                opacity: 1 - compDim,
                filter: compDim > 0 ? `saturate(${1 - compDim * 1.4})` : "none",
                transition:
                  "background 500ms ease, box-shadow 500ms ease, opacity 500ms ease, filter 500ms ease",
              }}
            >
              <div
                style={{
                  fontFamily: r.hero ? "var(--hux-serif)" : "var(--hux-sans)",
                  fontStyle: r.hero ? "italic" : "normal",
                  fontSize: r.hero ? 28 : 14,
                  fontWeight: r.hero ? 400 : 500,
                  letterSpacing: r.hero ? "-0.01em" : "0.02em",
                  lineHeight: 1.15,
                  textShadow:
                    isHero && heroLift > 0.5
                      ? `0 0 ${heroLift * 18}px color-mix(in oklab, var(--hux-fg) 60%, transparent)`
                      : "none",
                  transition: "text-shadow 500ms ease",
                }}
              >
                {r.name}
              </div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                }}
              >
                {r.bad.map((b) => (
                  <div
                    key={b}
                    style={{
                      display: "flex",
                      gap: 10,
                      alignItems: "flex-start",
                      fontSize: 13,
                      opacity: 0.78,
                    }}
                  >
                    <span
                      style={{
                        marginTop: 7,
                        width: 6,
                        height: 1,
                        background: "currentColor",
                        flexShrink: 0,
                        opacity: 0.4,
                      }}
                    />
                    <span>{b}</span>
                  </div>
                ))}
                {r.good.map((g) => (
                  <div
                    key={g}
                    style={{
                      display: "flex",
                      gap: 10,
                      alignItems: "flex-start",
                      fontSize: 13,
                    }}
                  >
                    <span
                      style={{
                        marginTop: 6,
                        width: 7,
                        height: 7,
                        borderRadius: 999,
                        background: "var(--hux-fg)",
                        flexShrink: 0,
                        boxShadow: `0 0 ${8 + heroLift * 12}px var(--hux-fg)`,
                      }}
                    />
                    <span>{g}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
