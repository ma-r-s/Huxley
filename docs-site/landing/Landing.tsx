"use client";
// Top-level Huxley landing composition.
// Order: nav → hero → problem → architecture → timeline → skills →
// today → personas → install → footer.

import { BalancedBackdrop, BalNav } from "./components/Chrome";
import { Hero } from "./sections/Hero";
import { Problem } from "./sections/Problem";
import { Architecture } from "./sections/Architecture";
import { TurnTimeline } from "./sections/TurnTimeline";
import { Skills } from "./sections/Skills";
import { Today } from "./sections/Today";
import { Personas } from "./sections/Personas";
import { Install, Footer } from "./sections/Install";

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
      <Hero />
      <Problem />
      <Architecture />
      <TurnTimeline />
      <Skills />
      <Today />
      <Personas />
      <Install />
      <Footer />
    </div>
  );
}
