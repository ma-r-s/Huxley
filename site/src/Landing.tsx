// Top-level Huxley landing composition.
// Order: nav → hero → problem → architecture → timeline → skills →
// today → personas → install → footer.

import { BalancedBackdrop, BalNav } from "./components/Chrome.js";
import { Hero } from "./sections/Hero.js";
import { Problem } from "./sections/Problem.js";
import { Architecture } from "./sections/Architecture.js";
import { TurnTimeline } from "./sections/TurnTimeline.js";
import { Skills } from "./sections/Skills.js";
import { Today } from "./sections/Today.js";
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
