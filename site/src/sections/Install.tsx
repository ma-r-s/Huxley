// Install + footer. Centered, calm close. Five-line install snippet.

import { useTranslation } from "react-i18next";
import { useRegisterSection } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { chipGhost, chipSolid } from "../components/Chrome.js";
import { Wordmark } from "../components/Wordmark.js";

export function Install() {
  const { t } = useTranslation();
  const sectionRef = useRegisterSection<HTMLElement>("install", "idle");
  const { isMobile } = useViewport();
  return (
    <section
      ref={sectionRef}
      id="install"
      style={{
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px 48px" : "120px 64px 64px",
        borderTop: "1px solid var(--hux-fg-line)",
        textAlign: "center",
      }}
    >
      <div style={{ maxWidth: 800, margin: "0 auto" }}>
        <div className="eyebrow" style={{ opacity: 0.6, marginBottom: 20 }}>
          {t("install.eyebrow")}
        </div>
        <h2
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(40px, 6vw, 88px)",
            lineHeight: 1,
            letterSpacing: "-0.015em",
            margin: "0 0 32px",
          }}
        >
          {t("install.titleA")}
          <br />
          <em style={{ fontStyle: "italic" }}>{t("install.titleB")}</em>
        </h2>
        <pre
          style={{
            margin: "0 auto",
            padding: isMobile ? 18 : 28,
            borderRadius: 16,
            background: "rgba(0,0,0,0.35)",
            border: "1px solid var(--hux-fg-line)",
            fontFamily: "var(--hux-mono)",
            fontSize: isMobile ? 11 : 13,
            lineHeight: 1.8,
            color: "var(--hux-fg)",
            overflow: "auto",
            textAlign: "left",
            maxWidth: 640,
          }}
        >
          {`$ git clone huxley && cd huxley
$ echo "HUXLEY_OPENAI_API_KEY=sk-..." > .env
$ uv sync && cd server/runtime && uv run huxley
$ cd ../../clients/pwa && bun install && bun dev
$ open http://localhost:5174   # hold the button, speak.`}
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
            {t("install.ctaGitHub")}
          </a>
          <a
            style={{ ...chipGhost, padding: "14px 24px", fontSize: 13 }}
            href="#"
          >
            {t("install.ctaDocs")}
          </a>
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  const { t } = useTranslation();
  const { isMobile } = useViewport();
  return (
    <footer
      style={{
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "32px 24px 40px" : "40px 64px 56px",
        borderTop: "1px solid var(--hux-fg-line)",
        display: "flex",
        flexDirection: isMobile ? "column" : "row",
        justifyContent: "space-between",
        alignItems: "center",
        gap: isMobile ? 16 : 0,
        fontFamily: "var(--hux-mono)",
        fontSize: 11,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        opacity: 0.6,
      }}
    >
      <Wordmark size={22} />
      <div
        style={{
          display: "flex",
          gap: isMobile ? 12 : 24,
          flexWrap: "wrap",
          justifyContent: "center",
        }}
      >
        <span>{t("footer.license")}</span>
        <span>·</span>
        <span>{t("footer.version")}</span>
        <span>·</span>
        <span>{t("footer.personasCount")}</span>
      </div>
    </footer>
  );
}
