// Turn-sequencing timeline — three audio tracks (User / Model / Tool audio)
// over an 8s scenario, with segment labels above the bars (so they never
// overflow the segment box) and a moving playhead.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRegisterSection } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { SectionHead, coralSection } from "../components/Chrome.js";
import { Reveal } from "../components/Reveal.js";

interface Segment {
  start: number;
  end: number;
  /** translation key under timeline.segments */
  labelKey: string;
}

interface Track {
  /** translation key under timeline.tracks */
  nameKey: string;
  color: string;
  y: number;
  segs: Segment[];
}

const TRACKS: Track[] = [
  {
    nameKey: "user",
    color: "#fff",
    y: 50,
    segs: [{ start: 0.0, end: 0.19, labelKey: "userAsk" }],
  },
  {
    nameKey: "model",
    color: "oklch(0.85 0.15 55)",
    y: 115,
    segs: [
      { start: 0.28, end: 0.5, labelKey: "modelSetting" },
      { start: 0.66, end: 0.88, labelKey: "modelProactive" },
    ],
  },
  {
    nameKey: "tool",
    color: "oklch(0.75 0.18 140)",
    y: 180,
    segs: [{ start: 0.525, end: 0.625, labelKey: "chime" }],
  },
];

const W = 960;
const H = 260;
const L = 120;
const R = 40;
const usableW = W - L - R;

export function TurnTimeline() {
  const { t } = useTranslation();
  const sectionRef = useRegisterSection<HTMLElement>("timeline", "speaking");
  const { isMobile } = useViewport();
  const [phase, setPhase] = useState(0);
  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const loop = (now: number) => {
      setPhase(((now - start) / 8000) % 1);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <section
      ref={sectionRef}
      id="timeline"
      style={{
        ...coralSection,
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
      }}
    >
      <SectionHead
        eyebrow={t("timeline.eyebrow")}
        title={
          <>
            {t("timeline.titleA")}
            <br />
            <em style={{ fontStyle: "italic" }}>{t("timeline.titleB")}</em>
          </>
        }
        subtitle={t("timeline.subtitle")}
      />

      {isMobile ? (
        <TimelineMobileSummary />
      ) : (
        <Reveal delay={350} y={36} duration={800}>
          <div
            style={{
              marginTop: 56,
              background: "rgba(0,0,0,0.22)",
              border: "1px solid var(--hux-fg-line)",
              borderRadius: 20,
              padding: 24,
              overflow: "hidden",
            }}
          >
            <svg
              viewBox={`0 0 ${W} ${H}`}
              style={{ width: "100%", height: "auto", display: "block" }}
            >
              <line
                x1={L}
                y1={H - 38}
                x2={W - R}
                y2={H - 38}
                stroke="currentColor"
                opacity="0.35"
              />
              {Array.from({ length: 9 }).map((_, i) => {
                const x = L + (i / 8) * usableW;
                return (
                  <g key={i}>
                    <line
                      x1={x}
                      y1={H - 38}
                      x2={x}
                      y2={H - 32}
                      stroke="currentColor"
                      opacity="0.45"
                    />
                    <text
                      x={x}
                      y={H - 18}
                      textAnchor="middle"
                      style={{
                        fontFamily: "var(--hux-mono)",
                        fontSize: 9,
                        letterSpacing: "0.1em",
                      }}
                      fill="currentColor"
                      opacity="0.55"
                    >
                      {i}s
                    </text>
                  </g>
                );
              })}

              {TRACKS.map((track, ti) => (
                <g key={ti}>
                  <text
                    x={L - 14}
                    y={track.y + 5}
                    textAnchor="end"
                    style={{
                      fontFamily: "var(--hux-mono)",
                      fontSize: 10,
                      letterSpacing: "0.12em",
                      textTransform: "uppercase",
                    }}
                    fill="currentColor"
                    opacity="0.6"
                  >
                    {t(`timeline.tracks.${track.nameKey}`)}
                  </text>
                  <line
                    x1={L}
                    y1={track.y}
                    x2={W - R}
                    y2={track.y}
                    stroke="currentColor"
                    opacity="0.12"
                    strokeDasharray="2 3"
                  />
                  {track.segs.map((s, si) => {
                    const x0 = L + s.start * usableW;
                    const x1 = L + s.end * usableW;
                    const active = phase >= s.start && phase <= s.end;
                    return (
                      <g key={si}>
                        <rect
                          x={x0}
                          y={track.y - 9}
                          width={x1 - x0}
                          height={18}
                          rx="5"
                          fill={track.color}
                          opacity={active ? 1 : 0.45}
                          style={{
                            transition: "opacity 160ms ease",
                            filter: active
                              ? `drop-shadow(0 0 10px ${track.color})`
                              : "none",
                          }}
                        />
                        <text
                          x={x0}
                          y={track.y - 16}
                          style={{
                            fontFamily: "var(--hux-mono)",
                            fontSize: 10,
                            letterSpacing: "0.04em",
                          }}
                          fill="currentColor"
                          opacity={active ? 0.95 : 0.55}
                        >
                          {t(`timeline.segments.${s.labelKey}`)}
                        </text>
                      </g>
                    );
                  })}
                </g>
              ))}

              <line
                x1={L + phase * usableW}
                y1={10}
                x2={L + phase * usableW}
                y2={H - 30}
                stroke="var(--hux-fg)"
                strokeWidth="1.5"
                opacity="0.85"
              />
              <circle
                cx={L + phase * usableW}
                cy={12}
                r="4"
                fill="var(--hux-fg)"
              />
            </svg>
          </div>
        </Reveal>
      )}

      <div
        style={{
          marginTop: 32,
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "repeat(3, 1fr)",
          gap: 24,
        }}
      >
        {(["t1", "t2", "t3"] as const).map((calloutKey, i) => {
          const k = t(`timeline.callouts.${calloutKey}Title`);
          const v = t(`timeline.callouts.${calloutKey}Body`);
          return (
            <Reveal key={k} delay={i * 110} y={20} duration={650}>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                <div
                  className="mono"
                  style={{
                    fontSize: 10,
                    letterSpacing: "0.16em",
                    textTransform: "uppercase",
                    opacity: 0.55,
                  }}
                >
                  {k}
                </div>
                <div
                  style={{
                    fontFamily: "var(--hux-serif)",
                    fontSize: 20,
                    fontStyle: "italic",
                    lineHeight: 1.3,
                  }}
                >
                  {v}
                </div>
              </div>
            </Reveal>
          );
        })}
      </div>
    </section>
  );
}

// Mobile-only fallback. The 960×260 SVG timeline becomes unreadable on a
// narrow phone (10pt text shrinks to ~3pt). On mobile we render the same
// 8-second scenario as a stacked time-stamped list.
interface TLRow {
  k: string;
  v: string;
}

function TimelineMobileSummary() {
  const { t } = useTranslation();
  const rows = t("timeline.mobileSummary.rows", {
    returnObjects: true,
  }) as TLRow[];
  return (
    <div
      style={{
        marginTop: 40,
        background: "rgba(0,0,0,0.22)",
        border: "1px solid var(--hux-fg-line)",
        borderRadius: 16,
        padding: 20,
      }}
    >
      <div
        className="eyebrow"
        style={{ opacity: 0.6, marginBottom: 12, fontSize: 10 }}
      >
        {t("timeline.mobileSummary.title")}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {rows.map((r, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr",
              gap: 4,
              padding: "12px 0",
              borderTop: i === 0 ? "none" : "1px solid var(--hux-fg-line)",
            }}
          >
            <div
              className="mono"
              style={{
                fontSize: 10,
                letterSpacing: "0.14em",
                opacity: 0.65,
              }}
            >
              {r.k}
            </div>
            <div
              style={{
                fontFamily: "var(--hux-sans)",
                fontSize: 14,
                lineHeight: 1.45,
              }}
            >
              {r.v}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
