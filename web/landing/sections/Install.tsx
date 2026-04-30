"use client";
// Install + footer. Centered, calm close. Five-line install snippet.

import { useTranslation } from "react-i18next";
import { useRegisterSection } from "../lib/voiceThread";
import { useViewport } from "../lib/useViewport";
import { chipGhost, chipSolid, deepSection } from "../components/Chrome";
import { Wordmark } from "../components/Wordmark";

export function Install() {
  const { t } = useTranslation();
  const sectionRef = useRegisterSection<HTMLElement>("install", "idle");
  const { isMobile } = useViewport();
  return (
    <section
      ref={sectionRef}
      id="install"
      style={{
        ...deepSection,
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px 48px" : "120px 64px 64px",
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
        <p
          style={{
            fontFamily: "var(--hux-serif)",
            fontStyle: "italic",
            fontSize: 16,
            lineHeight: 1.5,
            opacity: 0.78,
            margin: "0 auto 28px",
            maxWidth: 560,
          }}
        >
          {t("install.cost")}
        </p>
        <pre
          style={{
            margin: "0 auto",
            padding: isMobile ? 16 : 28,
            borderRadius: 16,
            background: "rgba(0,0,0,0.35)",
            border: "1px solid var(--hux-fg-line)",
            fontFamily: "var(--hux-mono)",
            fontSize: isMobile ? 10 : 13,
            lineHeight: 1.8,
            color: "var(--hux-fg)",
            overflowX: "auto",
            // pre-wrap lets long words like the GitHub URL wrap on mobile so
            // nothing clips. Desktop keeps the original block layout.
            whiteSpace: isMobile ? "pre-wrap" : "pre",
            wordBreak: isMobile ? "break-all" : "normal",
            textAlign: "left",
            maxWidth: 640,
          }}
        >
          {isMobile
            ? `$ git clone github.com/ma-r-s/Huxley && cd Huxley
$ echo "OPENAI_KEY=sk-..." > server/runtime/.env
# terminal 1
$ uv sync && cd server/runtime && uv run huxley
# terminal 2
$ cd clients/pwa && bun install && bun dev
$ open http://localhost:5174`
            : `$ git clone https://github.com/ma-r-s/Huxley.git && cd Huxley
$ echo "HUXLEY_OPENAI_API_KEY=sk-..." > server/runtime/.env
$ uv sync && cd server/runtime && uv run huxley   # terminal 1
$ cd clients/pwa && bun install && bun dev         # terminal 2
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
            href="/docs"
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
  const linkStyle = {
    color: "inherit",
    textDecoration: "none",
    opacity: 0.85,
  } as const;
  return (
    <footer
      style={{
        ...deepSection,
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "40px 24px 48px" : "48px 64px 56px",
        display: "flex",
        flexDirection: "column",
        gap: isMobile ? 24 : 28,
        fontFamily: "var(--hux-mono)",
        fontSize: 11,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        opacity: 0.7,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: isMobile ? "column" : "row",
          justifyContent: "space-between",
          alignItems: isMobile ? "flex-start" : "center",
          gap: isMobile ? 20 : 0,
        }}
      >
        <Wordmark size={22} />
        <div
          style={{
            display: "flex",
            gap: isMobile ? 16 : 22,
            flexWrap: "wrap",
          }}
        >
          <a style={linkStyle} href="https://github.com/ma-r-s/Huxley">
            {t("footer.linkRepo")}
          </a>
          <a style={linkStyle} href="https://github.com/ma-r-s/Huxley/issues">
            {t("footer.linkIssues")}
          </a>
          <a
            style={linkStyle}
            href="https://github.com/ma-r-s/Huxley/discussions"
          >
            {t("footer.linkDiscussions")}
          </a>
          <a style={linkStyle} href="/docs">
            {t("footer.linkDocs")}
          </a>
        </div>
      </div>
      <div
        style={{
          display: "flex",
          gap: isMobile ? 12 : 24,
          flexWrap: "wrap",
          justifyContent: isMobile ? "flex-start" : "center",
          opacity: 0.75,
        }}
      >
        {/* Personal name + GitHub handle stay outside i18n — proper nouns
            don't translate, and a missing key in es.json/fr.json shouldn't
            ever silently drop attribution. */}
        <span>
          {t("footer.builtBy")}{" "}
          <a style={linkStyle} href="https://ma-r-s.com">
            Mario Ruiz
          </a>
        </span>
        <span>·</span>
        <span>{t("footer.license")}</span>
        <span>·</span>
        <span>
          <a style={linkStyle} href="https://github.com/ma-r-s">
            @ma-r-s
          </a>
        </span>
        <span>·</span>
        <span>{t("footer.personasCount")}</span>
      </div>
    </footer>
  );
}
