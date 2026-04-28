// Install + footer. Centered, calm close. Five-line install snippet.

import { useRegisterSection } from "../lib/voiceThread.js";
import { chipGhost, chipSolid } from "../components/Chrome.js";
import { Wordmark } from "../components/Wordmark.js";

export function Install() {
  const sectionRef = useRegisterSection<HTMLElement>("install", "idle");
  return (
    <section
      ref={sectionRef}
      id="install"
      style={{
        position: "relative",
        zIndex: 2,
        padding: "120px 64px 64px",
        borderTop: "1px solid var(--hux-fg-line)",
        textAlign: "center",
      }}
    >
      <div style={{ maxWidth: 800, margin: "0 auto" }}>
        <div className="eyebrow" style={{ opacity: 0.6, marginBottom: 20 }}>
          § 07 — Get started
        </div>
        <h2
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(48px, 6vw, 88px)",
            lineHeight: 1,
            letterSpacing: "-0.015em",
            margin: "0 0 32px",
          }}
        >
          Five lines.
          <br />
          <em style={{ fontStyle: "italic" }}>A voice of your own.</em>
        </h2>
        <pre
          style={{
            margin: "0 auto",
            padding: 28,
            borderRadius: 16,
            background: "rgba(0,0,0,0.35)",
            border: "1px solid var(--hux-fg-line)",
            fontFamily: "var(--hux-mono)",
            fontSize: 13,
            lineHeight: 1.8,
            color: "var(--hux-fg)",
            overflow: "auto",
            textAlign: "left",
            maxWidth: 640,
          }}
        >
          {`$ git clone huxley && cd huxley
$ echo "HUXLEY_OPENAI_API_KEY=sk-..." > .env
$ uv sync && uv run huxley
$ cd web && bun install && bun dev
$ open http://localhost:5173   # hold the button, speak.`}
        </pre>
        <div
          style={{
            marginTop: 40,
            display: "flex",
            gap: 12,
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          <a
            style={{ ...chipSolid, padding: "14px 24px", fontSize: 13 }}
            href="https://github.com/ma-r-s/Huxley"
          >
            GitHub ↗
          </a>
          <a
            style={{ ...chipGhost, padding: "14px 24px", fontSize: 13 }}
            href="#"
          >
            Read the docs
          </a>
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer
      style={{
        position: "relative",
        zIndex: 2,
        padding: "40px 64px 56px",
        borderTop: "1px solid var(--hux-fg-line)",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        fontFamily: "var(--hux-mono)",
        fontSize: 11,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        opacity: 0.6,
      }}
    >
      <Wordmark size={22} />
      <div style={{ display: "flex", gap: 24 }}>
        <span>MIT licensed</span>
        <span>·</span>
        <span>Pre-1.0</span>
        <span>·</span>
        <span>Six personas in the wild</span>
      </div>
    </footer>
  );
}
