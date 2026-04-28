// § 05 — Huxley-market + Huxley-grows.
// A live-transcript demo with four variants (Found / Built / Needs config /
// No API). Left: a chat transcript that auto-plays when the section enters
// view. Right: a unified job card showing the market or grows pipeline.

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useRegisterSection } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { SectionHead } from "../components/Chrome.js";
import { Reveal } from "../components/Reveal.js";

type BeatKind = "user" | "model" | "thought" | "job" | "skill" | "gap";

interface Beat {
  t: number;
  kind: BeatKind;
  text?: string;
  phase?: PhaseKey;
  proactive?: boolean;
  emphasis?: boolean;
}

interface Variant {
  label: string;
  dot: string;
  skill: string;
  beats: Beat[];
}

const V_FOUND: Variant = {
  label: "Found it",
  dot: "oklch(0.82 0.17 210)",
  skill: "hacker-news",
  beats: [
    {
      t: 0,
      kind: "user",
      text: "Hey Huxley — play me the top of Hacker News.",
    },
    {
      t: 900,
      kind: "thought",
      text: "Not installed. Searching huxley-market…",
    },
    { t: 1600, kind: "job", phase: "searching" },
    { t: 2800, kind: "job", phase: "match" },
    {
      t: 3400,
      kind: "model",
      text: "Found one — hacker-news by @merrill, 4.8★. Queued; I’ll ping you when it’s in.",
    },
    { t: 5200, kind: "gap", text: "— a few seconds later —" },
    { t: 6400, kind: "job", phase: "installing" },
    { t: 7400, kind: "job", phase: "ready" },
    {
      t: 8000,
      kind: "model",
      proactive: true,
      emphasis: true,
      text: "Hacker-news is installed. Want me to read the top five?",
    },
    { t: 9600, kind: "user", text: "Go." },
    { t: 10100, kind: "skill", text: "▶ hacker-news · reading top 5 stories" },
  ],
};

const V_BUILT: Variant = {
  label: "Built it",
  dot: "oklch(0.82 0.17 130)",
  skill: "local-paper",
  beats: [
    {
      t: 0,
      kind: "user",
      text: "Hey Huxley — read me the latest from my local paper.",
    },
    { t: 900, kind: "thought", text: "Searching huxley-market…" },
    { t: 1500, kind: "job", phase: "searching" },
    { t: 2500, kind: "job", phase: "nomatch" },
    {
      t: 3100,
      kind: "model",
      text: "Nothing in the registry. I can try to build one — should I?",
    },
    { t: 4700, kind: "user", text: "Yeah, queue it." },
    {
      t: 5200,
      kind: "model",
      text: "Queued. I’ll jump in when it’s ready — carry on.",
    },
    { t: 5800, kind: "job", phase: "drafting" },

    { t: 7000, kind: "gap", text: "— a couple of minutes later —" },
    { t: 7500, kind: "user", text: "Huxley, weather tomorrow?" },
    {
      t: 8800,
      kind: "model",
      text: "Seventeen and cloudy, rain by afternoon.",
    },

    { t: 10000, kind: "job", phase: "writing" },
    { t: 11400, kind: "job", phase: "testing" },
    { t: 12600, kind: "job", phase: "installing" },
    { t: 13600, kind: "job", phase: "ready" },
    {
      t: 14100,
      kind: "model",
      proactive: true,
      emphasis: true,
      text: "Sorry to jump in — local-paper is ready, and I published it to huxley-market too. Read it now?",
    },
    { t: 16000, kind: "user", text: "Go." },
    { t: 16500, kind: "skill", text: "▶ local-paper · reading 6 stories" },
  ],
};

const V_NEEDSCONFIG: Variant = {
  label: "Needs config",
  dot: "oklch(0.82 0.14 70)",
  skill: "spotify",
  beats: [
    {
      t: 0,
      kind: "user",
      text: "Huxley, play “Come Away With Me” on Spotify.",
    },
    { t: 900, kind: "thought", text: "Searching huxley-market…" },
    { t: 1500, kind: "job", phase: "searching" },
    { t: 2500, kind: "job", phase: "match" },
    {
      t: 3100,
      kind: "model",
      text: "Found spotify by @lena. Queued; I’ll let you know.",
    },
    { t: 4800, kind: "gap", text: "— a few seconds later —" },
    { t: 5400, kind: "job", phase: "installing" },
    { t: 6400, kind: "job", phase: "needsconfig" },
    {
      t: 7000,
      kind: "model",
      proactive: true,
      emphasis: true,
      text: "Spotify is installed — but it needs a one-time sign-in before it can play anything. Open huxley-web → Installed → Spotify → Settings to link your account.",
    },
    {
      t: 10500,
      kind: "thought",
      text: "Skills configure themselves. Huxley never holds your keys.",
    },
  ],
};

const V_NOAPI: Variant = {
  label: "No API",
  dot: "oklch(0.75 0.17 45)",
  skill: "bernardos",
  beats: [
    { t: 0, kind: "user", text: "Book me a table at Bernardo’s for 7pm." },
    { t: 900, kind: "thought", text: "Searching huxley-market…" },
    { t: 1500, kind: "job", phase: "searching" },
    { t: 2500, kind: "job", phase: "nomatch" },
    {
      t: 3100,
      kind: "model",
      text: "No skill for this. Want me to try to build one?",
    },
    { t: 4600, kind: "user", text: "Yeah." },
    { t: 5100, kind: "model", text: "Queued." },
    { t: 5800, kind: "job", phase: "drafting" },

    { t: 7000, kind: "gap", text: "— a minute later —" },
    { t: 7600, kind: "job", phase: "researching" },
    { t: 9000, kind: "job", phase: "failed" },
    {
      t: 9600,
      kind: "model",
      proactive: true,
      emphasis: true,
      text: "I couldn’t build this one — there’s no public API for Bernardo’s reservation system. Nothing to wrap.",
    },
    {
      t: 12200,
      kind: "thought",
      text: "Huxley stops here. No fake capability gets installed.",
    },
  ],
};

type VariantKey = "found" | "built" | "needsconfig" | "noapi";
const VARIANTS: Record<VariantKey, Variant> = {
  found: V_FOUND,
  built: V_BUILT,
  needsconfig: V_NEEDSCONFIG,
  noapi: V_NOAPI,
};

type PhaseKey =
  | "searching"
  | "match"
  | "nomatch"
  | "drafting"
  | "writing"
  | "testing"
  | "researching"
  | "installing"
  | "ready"
  | "needsconfig"
  | "failed";

interface Phase {
  label: string;
  detail: string;
  pct: number;
  channel: "market" | "grows";
}

const JOB_PHASES: Record<PhaseKey, Phase> = {
  searching: {
    label: "searching huxley-market",
    detail: "1,247 skills · semantic + tag query",
    pct: 0.18,
    channel: "market",
  },
  match: {
    label: "match found",
    detail: "queued for install",
    pct: 0.5,
    channel: "market",
  },
  nomatch: {
    label: "no match",
    detail: "nothing in registry",
    pct: 0.3,
    channel: "market",
  },
  drafting: {
    label: "drafting plan",
    detail: "clawbot · reviewing skill guide",
    pct: 0.42,
    channel: "grows",
  },
  writing: {
    label: "writing skill/",
    detail: "stt.py · persona.py · entry_points.toml",
    pct: 0.62,
    channel: "grows",
  },
  testing: {
    label: "running tests",
    detail: "12 passed · coordinator handshake OK",
    pct: 0.78,
    channel: "grows",
  },
  researching: {
    label: "scouting APIs",
    detail: "scanning public endpoints · no auth wall",
    pct: 0.4,
    channel: "grows",
  },
  installing: {
    label: "registering skill",
    detail: "pip install -e . · reloading entry-points",
    pct: 0.92,
    channel: "grows",
  },
  ready: {
    label: "ready",
    detail: "installed · reload complete",
    pct: 1.0,
    channel: "grows",
  },
  needsconfig: {
    label: "awaiting config",
    detail: "installed · needs settings in huxley-web",
    pct: 0.96,
    channel: "grows",
  },
  failed: {
    label: "stopped",
    detail: "no API found · nothing to install",
    pct: 0.55,
    channel: "grows",
  },
};

export function HuxleyGrows() {
  const sectionRef = useRegisterSection<HTMLElement>("grows", "thinking");
  const { isMobile } = useViewport();
  const [variant, setVariant] = useState<VariantKey>("built");
  const spec = VARIANTS[variant];

  const [elapsed, setElapsed] = useState(0);
  const [inView, setInView] = useState(false);
  const playStartRef = useRef(0);
  const pausedAtRef = useRef(0);

  // Section-local in-view: gates whether the transcript is "playing".
  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e) setInView(e.intersectionRatio >= 0.25);
      },
      { threshold: [0.25] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [sectionRef]);

  // Reset play state when the user picks a new variant tab.
  useEffect(() => {
    setElapsed(0);
    playStartRef.current = 0;
    pausedAtRef.current = 0;
  }, [variant]);

  // Animation loop — runs only while the section is on screen.
  useEffect(() => {
    if (!inView) return;
    let raf = 0;
    const resumeFrom = pausedAtRef.current;
    playStartRef.current = performance.now() - resumeFrom;
    const loop = (now: number) => {
      setElapsed(now - playStartRef.current);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      pausedAtRef.current = performance.now() - playStartRef.current;
      cancelAnimationFrame(raf);
    };
  }, [inView, variant]);

  const lastBeat = spec.beats[spec.beats.length - 1]!;
  const duration = lastBeat.t + 2500;
  const restart = () => {
    setElapsed(0);
    playStartRef.current = performance.now();
    pausedAtRef.current = 0;
  };

  const visible = spec.beats.filter((b) => b.t <= elapsed);
  const currentJob = [...spec.beats]
    .reverse()
    .find((b) => b.kind === "job" && b.t <= elapsed);

  return (
    <section
      ref={sectionRef}
      id="grows"
      style={{
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
        borderTop: "1px solid var(--hux-fg-line)",
        background: "color-mix(in oklab, var(--hux-coral) 94%, black)",
      }}
    >
      <SectionHead
        eyebrow="§ 05 — Huxley-market + Huxley-grows"
        title={
          <>
            You ask.
            <br />
            <em style={{ fontStyle: "italic" }}>It finds, or builds.</em>
          </>
        }
        subtitle="First it checks huxley-market — a registry of free, open-source skills built by other users. If nothing fits, huxley-grows writes one from scratch. Either way, no terminal. Install from voice, or tap it through the Huxley-web app on your phone."
      />

      <div
        style={{
          marginTop: isMobile ? 40 : 64,
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "1.15fr 0.85fr",
          gap: isMobile ? 24 : 32,
          alignItems: "start",
        }}
      >
        <Reveal delay={200} y={28} duration={750}>
          <div
            style={{
              position: "relative",
              borderRadius: 18,
              border: "1px solid var(--hux-fg-line)",
              background: "rgba(0,0,0,0.28)",
              padding: "22px 26px 64px",
              minHeight: 560,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                paddingBottom: 14,
                marginBottom: 18,
                borderBottom: "1px solid var(--hux-fg-line)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 999,
                    background: spec.dot,
                    boxShadow: "0 0 12px currentColor",
                    color: spec.dot,
                  }}
                />
                <span
                  className="mono"
                  style={{
                    fontSize: 10,
                    letterSpacing: "0.18em",
                    textTransform: "uppercase",
                    opacity: 0.7,
                  }}
                >
                  live turn
                </span>
              </div>
              <div style={{ display: "flex", gap: 2 }}>
                <VariantTab
                  active={variant === "found"}
                  onClick={() => setVariant("found")}
                >
                  Found it
                </VariantTab>
                <VariantTab
                  active={variant === "built"}
                  onClick={() => setVariant("built")}
                >
                  Built it
                </VariantTab>
                <VariantTab
                  active={variant === "needsconfig"}
                  onClick={() => setVariant("needsconfig")}
                >
                  Needs config
                </VariantTab>
                <VariantTab
                  active={variant === "noapi"}
                  onClick={() => setVariant("noapi")}
                >
                  No API
                </VariantTab>
              </div>
            </div>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              {visible.map((b, i) => (
                <BeatRow key={i} beat={b} />
              ))}
              {visible.length < spec.beats.length && (
                <TypingDots nextKind={spec.beats[visible.length]!.kind} />
              )}
            </div>

            <div
              style={{
                position: "absolute",
                right: 18,
                bottom: 14,
                display: "flex",
                alignItems: "center",
                gap: 10,
                fontFamily: "var(--hux-mono)",
                fontSize: 10,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
                opacity: 0.55,
              }}
            >
              <span>
                {(Math.min(elapsed, duration) / 1000) | 0}s /{" "}
                {(duration / 1000) | 0}s
              </span>
              <button
                onClick={restart}
                style={{
                  border: "1px solid var(--hux-fg-line)",
                  background: "transparent",
                  color: "var(--hux-fg)",
                  fontFamily: "inherit",
                  fontSize: "inherit",
                  letterSpacing: "inherit",
                  textTransform: "inherit",
                  padding: "6px 10px",
                  borderRadius: 999,
                  cursor: "pointer",
                }}
              >
                ↻ replay
              </button>
            </div>
          </div>
        </Reveal>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 28,
          }}
        >
          <Reveal delay={350} y={28} duration={750}>
            <JobCard job={currentJob ?? null} variant={variant} spec={spec} />
          </Reveal>
        </div>
      </div>

      <div
        style={{
          marginTop: isMobile ? 56 : 96,
          paddingTop: 32,
          borderTop: "1px solid var(--hux-fg-line)",
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "repeat(3, 1fr)",
          gap: isMobile ? 24 : 40,
        }}
      >
        {(
          [
            {
              k: "Find or build — same ceremony",
              v: (
                <>
                  Market install and build-from-scratch end the same way: a new
                  skill on your box, same API, same permissions model, same
                  voice command.
                </>
              ),
            },
            {
              k: "No terminal, ever",
              v: (
                <>
                  Install from voice, or tap it through huxley-web on your
                  phone. The CLI still works — you just never have to touch it.
                </>
              ),
            },
            {
              k: "Failure is a feature",
              v: (
                <>
                  If neither path works — no market hit and no buildable shape —
                  Huxley says so, precisely, and stops. No hallucinated
                  capability.
                </>
              ),
            },
          ] as Array<{ k: string; v: ReactNode }>
        ).map((x, i) => (
          <Reveal key={x.k} delay={i * 110} y={18} duration={650}>
            <div>
              <div
                style={{
                  fontFamily: "var(--hux-serif)",
                  fontStyle: "italic",
                  fontSize: 22,
                  lineHeight: 1.2,
                  marginBottom: 10,
                }}
              >
                {x.k}
              </div>
              <div
                style={{
                  fontFamily: "var(--hux-sans)",
                  fontSize: 14,
                  lineHeight: 1.55,
                  opacity: 0.78,
                  textWrap: "pretty",
                }}
              >
                {x.v}
              </div>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

// ── Subcomponents ────────────────────────────────────────────────────────

function VariantTab({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        border: "none",
        background: "transparent",
        padding: "6px 10px",
        cursor: "pointer",
        fontFamily: "var(--hux-mono)",
        fontSize: 9,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
        color: "var(--hux-fg)",
        opacity: active ? 1 : 0.5,
        borderBottom: active
          ? "1px solid var(--hux-fg)"
          : "1px solid transparent",
        transition: "opacity 200ms ease, border-color 200ms ease",
      }}
    >
      {children}
    </button>
  );
}

function BeatRow({ beat }: { beat: Beat }) {
  if (beat.kind === "user") return <Bubble who="user">{beat.text}</Bubble>;
  if (beat.kind === "model")
    return (
      <Bubble who="huxley" emphasis={beat.emphasis} proactive={beat.proactive}>
        {beat.text}
      </Bubble>
    );
  if (beat.kind === "gap") {
    return (
      <div
        className="hux-beat-in"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          margin: "12px 0",
          fontFamily: "var(--hux-mono)",
          fontSize: 10,
          letterSpacing: "0.22em",
          textTransform: "uppercase",
          opacity: 0.45,
        }}
      >
        <span
          style={{
            flex: 1,
            height: 1,
            background: "currentColor",
            opacity: 0.45,
          }}
        />
        <span style={{ whiteSpace: "nowrap" }}>{beat.text}</span>
        <span
          style={{
            flex: 1,
            height: 1,
            background: "currentColor",
            opacity: 0.45,
          }}
        />
      </div>
    );
  }
  if (beat.kind === "thought") {
    return (
      <div
        className="hux-beat-in"
        style={{
          fontFamily: "var(--hux-mono)",
          fontSize: 11,
          letterSpacing: "0.02em",
          opacity: 0.55,
          padding: "2px 0 2px 38px",
          textWrap: "pretty",
        }}
      >
        ⊙ {beat.text}
      </div>
    );
  }
  if (beat.kind === "job") {
    const p = JOB_PHASES[beat.phase!];
    const isMarket = p.channel === "market";
    const prefix = isMarket ? "huxley-market" : "huxley-grows";
    const color =
      beat.phase === "failed"
        ? "oklch(0.78 0.17 45)"
        : beat.phase === "nomatch"
          ? "oklch(0.78 0.1 60)"
          : isMarket
            ? "oklch(0.82 0.14 210)"
            : "var(--hux-fg)";
    return (
      <div
        className="hux-beat-in"
        style={{
          fontFamily: "var(--hux-mono)",
          fontSize: 10,
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          padding: "4px 0 4px 38px",
          opacity: 0.85,
          color,
        }}
      >
        <span style={{ opacity: 0.55, marginRight: 10 }}>{prefix} ›</span>
        {p.label}
      </div>
    );
  }
  if (beat.kind === "skill") {
    return (
      <div
        className="hux-beat-in"
        style={{
          marginTop: 8,
          padding: "10px 14px",
          border: "1px solid var(--hux-fg-line)",
          borderRadius: 12,
          background: "color-mix(in oklab, var(--hux-fg) 10%, transparent)",
          fontFamily: "var(--hux-mono)",
          fontSize: 11,
          letterSpacing: "0.08em",
        }}
      >
        {beat.text}
      </div>
    );
  }
  return null;
}

function Bubble({
  who,
  emphasis,
  proactive,
  children,
}: {
  who: "user" | "huxley";
  emphasis?: boolean;
  proactive?: boolean;
  children: ReactNode;
}) {
  const isUser = who === "user";
  return (
    <div
      className="hux-beat-in"
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
      }}
    >
      <div
        style={{
          maxWidth: "82%",
          padding: "10px 14px",
          borderRadius: 16,
          fontFamily: "var(--hux-serif)",
          fontSize: 16,
          lineHeight: 1.45,
          textWrap: "pretty",
          ...(isUser
            ? {
                background:
                  "color-mix(in oklab, var(--hux-fg) 14%, transparent)",
                borderTopRightRadius: 4,
              }
            : {
                background: emphasis
                  ? "color-mix(in oklab, var(--hux-fg) 22%, transparent)"
                  : "color-mix(in oklab, var(--hux-bg) 40%, transparent)",
                border: "1px solid var(--hux-fg-line)",
                borderTopLeftRadius: 4,
                boxShadow: emphasis
                  ? "0 0 0 1px var(--hux-fg-line), 0 0 24px color-mix(in oklab, var(--hux-fg) 20%, transparent)"
                  : "none",
                fontStyle: emphasis ? "italic" : "normal",
              }),
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontFamily: "var(--hux-mono)",
            fontSize: 9,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            opacity: 0.6,
            marginBottom: 4,
          }}
        >
          <span>{isUser ? "you" : "huxley"}</span>
          {proactive && (
            <span
              style={{
                padding: "1px 6px",
                borderRadius: 999,
                border: "1px solid currentColor",
                fontSize: 8,
                letterSpacing: "0.2em",
              }}
            >
              proactive
            </span>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

function TypingDots({ nextKind }: { nextKind: BeatKind }) {
  const isUser = nextKind === "user";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        opacity: 0.55,
      }}
    >
      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "8px 12px",
          borderRadius: 12,
        }}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            style={{
              width: 5,
              height: 5,
              borderRadius: 999,
              background: "currentColor",
              animation: `hux-dot 1200ms ease-in-out ${i * 180}ms infinite`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

// Unified job card — shows market phases or grows phases depending on state.
function JobCard({
  job,
  variant,
  spec,
}: {
  job: Beat | null;
  variant: VariantKey;
  spec: Variant;
}) {
  const phase: PhaseKey | "idle" = job ? (job.phase as PhaseKey) : "idle";
  const p =
    phase === "idle"
      ? {
          label: "waiting",
          detail: "no job queued",
          pct: 0,
          channel: "market" as const,
        }
      : JOB_PHASES[phase];
  const failed = phase === "failed";
  const ready = phase === "ready" || phase === "needsconfig";
  const needsConfig = phase === "needsconfig";
  const nomatch = phase === "nomatch";
  const isGrowsPhase = p.channel === "grows";

  const showingMarket = !isGrowsPhase && !ready && !failed;
  const marketPhases: PhaseKey[] = ["searching", "match", "nomatch"];
  const growsPhases: PhaseKey[] = needsConfig
    ? ["drafting", "writing", "testing", "installing", "needsconfig"]
    : ["drafting", "writing", "testing", "installing", "ready"];

  const title = showingMarket
    ? "huxley-market · query"
    : "huxley-grows · background job";
  const dotColor = needsConfig
    ? "oklch(0.82 0.14 70)"
    : ready
      ? "oklch(0.82 0.17 130)"
      : failed
        ? "oklch(0.75 0.17 45)"
        : showingMarket
          ? "oklch(0.82 0.14 210)"
          : "oklch(0.8 0.14 80)";

  const displayPhases = showingMarket ? marketPhases : growsPhases;
  const matchesVariant: Partial<Record<VariantKey, PhaseKey[]>> = {
    found: ["match"],
    built: ["nomatch"],
  };

  return (
    <div
      style={{
        position: "relative",
        borderRadius: 18,
        border: "1px solid var(--hux-fg-line)",
        background: "rgba(0,0,0,0.35)",
        padding: 22,
        fontFamily: "var(--hux-mono)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: 999,
              background: dotColor,
              boxShadow: "0 0 12px currentColor",
              color: dotColor,
              animation:
                !ready && !failed && phase !== "idle"
                  ? "hux-pulse-dot 1400ms ease-in-out infinite"
                  : "none",
            }}
          />
          <span
            style={{
              fontSize: 10,
              letterSpacing: "0.2em",
              textTransform: "uppercase",
              opacity: 0.8,
            }}
          >
            {title}
          </span>
        </div>
        <span
          style={{
            fontSize: 9,
            letterSpacing: "0.2em",
            textTransform: "uppercase",
            opacity: 0.5,
          }}
        >
          {spec.skill}
        </span>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 10,
          marginBottom: 18,
        }}
      >
        {displayPhases.map((ph) => {
          const phData = JOB_PHASES[ph];
          const reachable = showingMarket
            ? (matchesVariant[variant] ?? []).includes(ph) || ph === "searching"
            : true;
          const phasePct =
            phase === "idle" ? 0 : JOB_PHASES[phase as PhaseKey].pct;
          const phaseChannel =
            phase === "idle" ? "market" : JOB_PHASES[phase as PhaseKey].channel;
          const done =
            phase !== "idle" &&
            phData.pct <= phasePct &&
            (showingMarket
              ? phaseChannel === "market"
              : phaseChannel === "grows");
          const current = phase === ph;
          const isBlocked =
            failed &&
            (["testing", "installing", "ready"] as PhaseKey[]).includes(ph);
          return (
            <div
              key={ph}
              style={{
                display: "grid",
                gridTemplateColumns: "20px 1fr auto",
                alignItems: "center",
                gap: 12,
                fontSize: 11,
                letterSpacing: "0.04em",
                opacity: isBlocked
                  ? 0.3
                  : !reachable && showingMarket
                    ? 0.35
                    : done || current
                      ? 1
                      : 0.5,
                color: current ? "var(--hux-fg)" : "inherit",
              }}
            >
              <span
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 999,
                  border: "1px solid var(--hux-fg-line)",
                  background: done ? "var(--hux-fg)" : "transparent",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 8,
                  color: "var(--hux-bg)",
                }}
              >
                {done ? "✓" : ""}
              </span>
              <span style={{ textTransform: current ? "uppercase" : "none" }}>
                {phData.label}
              </span>
              <span style={{ opacity: 0.6, fontSize: 10, textAlign: "right" }}>
                {current ? phData.detail : ""}
              </span>
            </div>
          );
        })}
        {failed && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "20px 1fr",
              gap: 12,
              marginTop: 4,
              fontSize: 11,
              letterSpacing: "0.04em",
              color: "oklch(0.78 0.17 45)",
            }}
          >
            <span
              style={{
                width: 14,
                height: 14,
                borderRadius: 999,
                border: "1px solid currentColor",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 9,
              }}
            >
              !
            </span>
            <span style={{ textTransform: "uppercase" }}>
              {JOB_PHASES.failed.label} — {JOB_PHASES.failed.detail}
            </span>
          </div>
        )}
        {nomatch && (
          <div
            style={{
              fontSize: 10,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              opacity: 0.65,
              marginTop: 4,
              paddingLeft: 32,
            }}
          >
            → handing off to huxley-grows…
          </div>
        )}
      </div>

      <div
        style={{
          height: 3,
          borderRadius: 2,
          overflow: "hidden",
          background: "color-mix(in oklab, var(--hux-fg) 12%, transparent)",
          marginBottom: 14,
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${(p.pct * 100) | 0}%`,
            background: failed
              ? "oklch(0.75 0.17 45)"
              : needsConfig
                ? "oklch(0.82 0.14 70)"
                : ready
                  ? "oklch(0.82 0.17 130)"
                  : showingMarket
                    ? "oklch(0.82 0.14 210)"
                    : "var(--hux-fg)",
            transition:
              "width 600ms cubic-bezier(.22,.9,.27,1), background 300ms",
          }}
        />
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 9,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          opacity: 0.55,
        }}
      >
        <span>
          {showingMarket ? "origin · huxley-market" : "origin · clawbot@local"}
        </span>
        <span>
          {ready && !needsConfig
            ? "reloaded"
            : needsConfig
              ? "awaiting sign-in"
              : failed
                ? "halted"
                : phase === "idle"
                  ? "waiting"
                  : showingMarket
                    ? "querying"
                    : "building"}
        </span>
      </div>
    </div>
  );
}
