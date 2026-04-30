"use client";
// Architecture diagram — boxes connected by dashed lines, with labelled
// packets riding the same lines (so the moving circles always follow the
// drawn edges). Bottom row of three small concept blocks.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRegisterSection } from "../lib/voiceThread";
import { useViewport } from "../lib/useViewport";
import { SectionHead, deepSection } from "../components/Chrome";
import { Reveal } from "../components/Reveal";

interface Node {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  sub: string;
  big?: boolean;
}

const N: Record<string, Node> = {
  persona: {
    x: 80,
    y: 100,
    w: 200,
    h: 80,
    label: "persona.yaml",
    sub: "identity + skills",
  },
  core: {
    x: 340,
    y: 220,
    w: 280,
    h: 180,
    label: "Huxley core",
    sub: "coordinator · focus · registry",
    big: true,
  },
  voice: {
    x: 80,
    y: 340,
    w: 200,
    h: 80,
    label: "Voice provider",
    sub: "OpenAI Realtime",
  },
  skill: {
    x: 680,
    y: 120,
    w: 200,
    h: 80,
    label: "Skill",
    sub: "Python pkg",
  },
  skill2: {
    x: 680,
    y: 230,
    w: 200,
    h: 80,
    label: "Skill",
    sub: "lights · hue",
  },
  skill3: {
    x: 680,
    y: 340,
    w: 200,
    h: 80,
    label: "Skill",
    sub: "timers",
  },
  client: {
    x: 340,
    y: 460,
    w: 280,
    h: 70,
    label: "Client",
    sub: "browser · ESP32 · phone",
  },
};

const center = (id: string): [number, number] => {
  const n = N[id]!;
  return [n.x + n.w / 2, n.y + n.h / 2];
};

interface Flow {
  from: keyof typeof N;
  to: keyof typeof N;
  label: string;
  offset: number;
  dur: number;
}

const FLOWS: Flow[] = [
  { from: "client", to: "core", label: "audio", offset: 0.0, dur: 2.0 },
  { from: "core", to: "voice", label: "transcript", offset: 0.8, dur: 2.0 },
  { from: "voice", to: "core", label: "tool-call", offset: 2.0, dur: 1.8 },
  { from: "core", to: "skill2", label: "dispatch", offset: 2.8, dur: 1.8 },
  { from: "skill2", to: "core", label: "ToolResult", offset: 4.0, dur: 1.8 },
  { from: "core", to: "client", label: "audio-out", offset: 5.2, dur: 2.0 },
  { from: "persona", to: "core", label: "config", offset: 6.4, dur: 1.6 },
  { from: "core", to: "skill3", label: "timer.fire", offset: 3.4, dur: 1.8 },
];

const EDGES: Array<[string, string]> = [
  ["persona", "core"],
  ["voice", "core"],
  ["core", "skill"],
  ["core", "skill2"],
  ["core", "skill3"],
  ["core", "client"],
];

export function Architecture() {
  const { t: tt } = useTranslation();
  const sectionRef = useRegisterSection<HTMLElement>(
    "architecture",
    "thinking",
  );
  const { isMobile } = useViewport();
  const [t, setT] = useState(0);
  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const loop = (now: number) => {
      setT((now - start) / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const getPacketPos = (flow: Flow) => {
    const phase = ((t - flow.offset + 16) % 8) / flow.dur;
    if (phase < 0 || phase > 1) return null;
    const from = center(flow.from);
    const to = center(flow.to);
    return {
      x: from[0] + (to[0] - from[0]) * phase,
      y: from[1] + (to[1] - from[1]) * phase,
      opacity: phase < 0.1 ? phase * 10 : phase > 0.9 ? (1 - phase) * 10 : 1,
    };
  };

  return (
    <section
      ref={sectionRef}
      id="architecture"
      style={{
        ...deepSection,
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
      }}
    >
      <SectionHead
        eyebrow={tt("architecture.eyebrow")}
        title={
          <>
            {tt("architecture.titleA")}{" "}
            <em style={{ fontStyle: "italic" }}>{tt("architecture.titleB")}</em>
          </>
        }
        subtitle={tt("architecture.subtitle")}
      />

      {isMobile ? (
        <MobileSummary />
      ) : (
        <Reveal delay={350} y={36} duration={800}>
          <div
            style={{
              marginTop: 64,
              position: "relative",
              background: "rgba(0,0,0,0.18)",
              border: "1px solid var(--hux-fg-line)",
              borderRadius: 20,
              padding: 24,
              overflow: "hidden",
            }}
          >
            <svg
              viewBox="0 0 960 570"
              style={{ width: "100%", height: "auto", display: "block" }}
            >
              {EDGES.map(([a, b], i) => {
                const [ax, ay] = center(a);
                const [bx, by] = center(b);
                return (
                  <line
                    key={i}
                    x1={ax}
                    y1={ay}
                    x2={bx}
                    y2={by}
                    stroke="currentColor"
                    strokeWidth="1"
                    opacity="0.25"
                    strokeDasharray="3 4"
                  />
                );
              })}

              {Object.entries(N).map(([id, n]) => (
                <g key={id}>
                  <rect
                    x={n.x}
                    y={n.y}
                    width={n.w}
                    height={n.h}
                    rx="12"
                    fill={n.big ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.2)"}
                    stroke="currentColor"
                    strokeWidth={n.big ? 1.4 : 1}
                    opacity={n.big ? 1 : 0.9}
                  />
                  <text
                    x={n.x + 18}
                    y={n.y + 26}
                    style={{
                      fontFamily: "var(--hux-serif)",
                      fontSize: n.big ? 22 : 16,
                      fontStyle: n.big ? "italic" : "normal",
                    }}
                    fill="currentColor"
                  >
                    {n.label}
                  </text>
                  <text
                    x={n.x + 18}
                    y={n.y + 46}
                    style={{
                      fontFamily: "var(--hux-mono)",
                      fontSize: 10,
                      letterSpacing: "0.1em",
                      textTransform: "uppercase",
                    }}
                    fill="currentColor"
                    opacity="0.55"
                  >
                    {n.sub}
                  </text>
                </g>
              ))}

              {FLOWS.map((f, i) => {
                const p = getPacketPos(f);
                if (!p) return null;
                return (
                  <g key={i}>
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r="6"
                      fill="var(--hux-fg)"
                      opacity={p.opacity * 0.9}
                      style={{ filter: "drop-shadow(0 0 6px var(--hux-fg))" }}
                    />
                    <text
                      x={p.x + 10}
                      y={p.y - 8}
                      style={{
                        fontFamily: "var(--hux-mono)",
                        fontSize: 9,
                        letterSpacing: "0.1em",
                      }}
                      fill="currentColor"
                      opacity={p.opacity * 0.7}
                    >
                      {f.label}
                    </text>
                  </g>
                );
              })}

              <text
                x="20"
                y="30"
                style={{
                  fontFamily: "var(--hux-mono)",
                  fontSize: 10,
                  letterSpacing: "0.2em",
                  textTransform: "uppercase",
                }}
                fill="currentColor"
                opacity="0.4"
              >
                {tt("architecture.footerLeft")}
              </text>
              <text
                x="940"
                y="30"
                textAnchor="end"
                style={{
                  fontFamily: "var(--hux-mono)",
                  fontSize: 10,
                  letterSpacing: "0.2em",
                  textTransform: "uppercase",
                }}
                fill="currentColor"
                opacity="0.4"
              >
                {tt("architecture.footerRight")}
              </text>
            </svg>
          </div>
        </Reveal>
      )}

      <div
        style={{
          marginTop: 40,
          display: "grid",
          gridTemplateColumns: isMobile
            ? "1fr"
            : "repeat(auto-fit, minmax(220px, 1fr))",
          gap: isMobile ? 24 : 32,
        }}
      >
        {(
          [
            "turnCoord",
            "skillDispatch",
            "proactive",
            "bridging",
            "constraints",
            "provider",
          ] as const
        ).map((cardKey, i) => (
          <Reveal key={cardKey} delay={i * 120} y={20} duration={650}>
            <div
              style={{
                borderTop: "1px solid var(--hux-fg-line)",
                paddingTop: 16,
              }}
            >
              <div
                style={{
                  fontFamily: "var(--hux-serif)",
                  fontStyle: "italic",
                  fontSize: 20,
                  marginBottom: 8,
                }}
              >
                {tt(`architecture.cards.${cardKey}.k`)}
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.5, opacity: 0.78 }}>
                {tt(`architecture.cards.${cardKey}.v`)}
              </div>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

// Mobile-only fallback. The 960×570 SVG diagram becomes unreadable on a
// 340px-wide phone (10pt mono text scales to ~3pt). On mobile we render the
// same architectural relationships as a stacked list — same content, legible.
interface SummaryRow {
  k: string;
  v: string;
}

function MobileSummary() {
  const { t: tt } = useTranslation();
  const rows = tt("architecture.mobileSummary.rows", {
    returnObjects: true,
  }) as SummaryRow[];
  return (
    <div
      style={{
        marginTop: 40,
        background: "rgba(0,0,0,0.18)",
        border: "1px solid var(--hux-fg-line)",
        borderRadius: 16,
        padding: 20,
      }}
    >
      <div
        className="eyebrow"
        style={{ opacity: 0.6, marginBottom: 6, fontSize: 10 }}
      >
        {tt("architecture.mobileSummary.title")}
      </div>
      <p
        style={{
          fontFamily: "var(--hux-serif)",
          fontStyle: "italic",
          fontSize: 14,
          lineHeight: 1.45,
          opacity: 0.78,
          margin: "0 0 16px",
        }}
      >
        {tt("architecture.mobileSummary.intro")}
      </p>
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
                textTransform: "uppercase",
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
