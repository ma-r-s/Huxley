// Top-level Huxley landing composition. Order mirrors the prototype's
// balanced-app.jsx exactly:
//   nav → sticky voice thread → hero → problem → architecture → timeline →
//   skills → grows → personas → install → footer.

import { BalancedBackdrop, BalNav } from "./components/Chrome.js";
import { VoiceThread } from "./components/VoiceThread.js";
import { useVoiceState } from "./lib/voiceThread.js";
import { Hero } from "./sections/Hero.js";
import { Problem } from "./sections/Problem.js";
import { Architecture } from "./sections/Architecture.js";
import { TurnTimeline } from "./sections/TurnTimeline.js";
import { Skills } from "./sections/Skills.js";
import { HuxleyGrows } from "./sections/HuxleyGrows.js";
import { Personas } from "./sections/Personas.js";
import { Install, Footer } from "./sections/Install.js";

export function Landing() {
  return (
    <div
      style={{
        background: "var(--hux-coral)",
        color: "var(--hux-fg)",
        fontFamily: "var(--hux-sans)",
        position: "relative",
        minHeight: "100%",
      }}
    >
      <BalancedBackdrop />
      <BalNav />
      <VoiceThreadBar />
      <Hero />
      <Problem />
      <Architecture />
      <TurnTimeline />
      <Skills />
      <HuxleyGrows />
      <Personas />
      <Install />
      <Footer />
    </div>
  );
}

// Sticky voice-thread bar — sits just under the nav. Section IDs MUST match
// the strings each section passes to useRegisterSection().
function VoiceThreadBar() {
  const sections = [
    { id: "hero", label: "Hero", position: 0.02 },
    { id: "problem", label: "§ 01 · Why", position: 0.14 },
    { id: "architecture", label: "§ 02 · Core", position: 0.28 },
    { id: "timeline", label: "§ 03 · Turns", position: 0.42 },
    { id: "skills", label: "§ 04 · Skills", position: 0.56 },
    { id: "grows", label: "§ 05 · Grows", position: 0.7 },
    { id: "persona", label: "§ 06 · Personas", position: 0.83 },
    { id: "install", label: "§ 07 · Install", position: 0.95 },
  ];

  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        zIndex: 40,
        padding: "6px 48px 10px",
        background: "color-mix(in oklab, var(--hux-coral) 82%, black)",
        backdropFilter: "blur(10px) saturate(140%)",
        WebkitBackdropFilter: "blur(10px) saturate(140%)",
        borderTop: "1px solid var(--hux-fg-line)",
        borderBottom: "1px solid var(--hux-fg-line)",
      }}
    >
      <VoiceThreadHeader />
      <VoiceThread sections={sections} height={44} />
    </div>
  );
}

// Status line above the waveform — names what Huxley is "doing" as you read.
function VoiceThreadHeader() {
  const { state, id } = useVoiceState();
  const LABELS: Record<string, { tag: string; sub: string }> = {
    idle: { tag: "Idle", sub: "Held, listening for the hold." },
    listening: {
      tag: "Listening",
      sub: "Capturing audio — waiting for release.",
    },
    thinking: {
      tag: "Thinking",
      sub: "Routing through coordinator and skills.",
    },
    speaking: {
      tag: "Speaking",
      sub: "Draining the audio channel to one voice.",
    },
    interrupt: {
      tag: "Interrupt",
      sub: "Atomic drop. Queue cleared. Channel flushed.",
    },
  };
  const cur = LABELS[state] ?? LABELS.idle!;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent: "space-between",
        paddingBottom: 6,
        color: "var(--hux-fg)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
        <span
          style={{
            fontFamily: "var(--hux-mono)",
            fontSize: 10,
            letterSpacing: "0.22em",
            textTransform: "uppercase",
            opacity: 0.55,
          }}
        >
          state
        </span>
        <span
          style={{
            fontFamily: "var(--hux-mono)",
            fontSize: 11,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            opacity: 0.95,
          }}
        >
          {cur.tag}
        </span>
        <span
          style={{
            fontFamily: "var(--hux-serif)",
            fontStyle: "italic",
            fontSize: 14,
            opacity: 0.72,
          }}
        >
          {cur.sub}
        </span>
      </div>
      <span
        style={{
          fontFamily: "var(--hux-mono)",
          fontSize: 10,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          opacity: 0.45,
        }}
      >
        turn · {id}
      </span>
    </div>
  );
}
