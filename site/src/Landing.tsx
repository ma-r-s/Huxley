// Top-level Huxley landing composition. Order mirrors the prototype's
// balanced-app.jsx exactly:
//   nav → sticky voice thread → hero → problem → architecture → timeline →
//   skills → grows → personas → install → footer.

import { useTranslation } from "react-i18next";
import { BalancedBackdrop, BalNav } from "./components/Chrome.js";
import { VoiceThread } from "./components/VoiceThread.js";
import { useVoiceState } from "./lib/voiceThread.js";
import { useViewport } from "./lib/useViewport.js";
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
  const { t } = useTranslation();
  const { isMobile } = useViewport();
  const sections = [
    { id: "hero", label: t("voiceThread.chapters.hero"), position: 0.02 },
    {
      id: "problem",
      label: t("voiceThread.chapters.problem"),
      position: 0.14,
    },
    {
      id: "architecture",
      label: t("voiceThread.chapters.architecture"),
      position: 0.28,
    },
    {
      id: "timeline",
      label: t("voiceThread.chapters.timeline"),
      position: 0.42,
    },
    {
      id: "skills",
      label: t("voiceThread.chapters.skills"),
      position: 0.56,
    },
    { id: "grows", label: t("voiceThread.chapters.grows"), position: 0.7 },
    {
      id: "persona",
      label: t("voiceThread.chapters.personas"),
      position: 0.83,
    },
    {
      id: "install",
      label: t("voiceThread.chapters.install"),
      position: 0.95,
    },
  ];

  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        zIndex: 40,
        padding: isMobile ? "6px 16px 10px" : "6px 48px 10px",
        background: "color-mix(in oklab, var(--hux-coral) 82%, black)",
        backdropFilter: "blur(10px) saturate(140%)",
        WebkitBackdropFilter: "blur(10px) saturate(140%)",
        borderTop: "1px solid var(--hux-fg-line)",
        borderBottom: "1px solid var(--hux-fg-line)",
      }}
    >
      <VoiceThreadHeader />
      <VoiceThread sections={sections} height={isMobile ? 32 : 44} />
    </div>
  );
}

// Status line above the waveform — names what Huxley is "doing" as you read.
function VoiceThreadHeader() {
  const { t } = useTranslation();
  const { state, id } = useVoiceState();
  const cur = {
    tag: t(`voiceThread.states.${state}.tag`),
    sub: t(`voiceThread.states.${state}.sub`),
  };
  const { isMobile } = useViewport();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent: "space-between",
        paddingBottom: 6,
        color: "var(--hux-fg)",
        gap: 8,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: isMobile ? 8 : 14,
          minWidth: 0,
        }}
      >
        {!isMobile && (
          <span
            style={{
              fontFamily: "var(--hux-mono)",
              fontSize: 10,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              opacity: 0.55,
            }}
          >
            {t("voiceThread.labelState")}
          </span>
        )}
        <span
          style={{
            fontFamily: "var(--hux-mono)",
            fontSize: isMobile ? 9 : 11,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            opacity: 0.95,
          }}
        >
          {cur.tag}
        </span>
        {!isMobile && (
          <span
            style={{
              fontFamily: "var(--hux-serif)",
              fontStyle: "italic",
              fontSize: 14,
              opacity: 0.72,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {cur.sub}
          </span>
        )}
      </div>
      <span
        style={{
          fontFamily: "var(--hux-mono)",
          fontSize: isMobile ? 8 : 10,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          opacity: 0.45,
          whiteSpace: "nowrap",
        }}
      >
        {t("voiceThread.turnPrefix")} · {id}
      </span>
    </div>
  );
}
