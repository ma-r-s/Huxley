"use client";
// /hx-1: the walkie Huxley's first persona runs on. Explains the device to
// someone who has never seen it: what it is, why a lever, what's inside,
// how it talks to Huxley, how it's made. Same chrome and type as the landing.

import dynamic from "next/dynamic";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import {
  BalancedBackdrop,
  BalNav,
  SectionHead,
  chipGhost,
  chipSolid,
  coralSection,
  deepSection,
  paperSection,
} from "../components/Chrome";
import { Reveal } from "../components/Reveal";
import { useViewport } from "../lib/useViewport";
import { Footer } from "../sections/Install";
import model from "./model.json";

// three.js only in the browser, and only on this page.
const HX1Story = dynamic(() => import("./HX1Story").then((m) => m.HX1Story), {
  ssr: false,
});

const ENCLOSURE_SRC =
  "https://github.com/ma-r-s/Huxley/tree/main/clients/firmware/enclosure";
const FIRMWARE_SRC =
  "https://github.com/ma-r-s/Huxley/tree/main/clients/firmware";

type Item = { k: string; v: string };

export function HX1Page() {
  const { t } = useTranslation();
  const { isMobile, isTablet } = useViewport();
  const pad = isMobile ? "56px 24px" : "96px 64px";
  const S = model.specs;
  const how = t("hx1.how.steps", { returnObjects: true }) as Item[];
  const specs: Item[] = [
    {
      k: t("hx1.inside.specs.size"),
      v: `${S.size_mm.map((v) => Math.round(v)).join(" × ")} mm`,
    },
    {
      k: t("hx1.inside.specs.weight"),
      v: `~${Math.round(S.weight_g / 10) * 10} g`,
    },
    { k: t("hx1.inside.specs.battery"), v: `${S.cells} × 18650` },
    { k: t("hx1.inside.specs.speaker"), v: "40 mm" },
    { k: t("hx1.inside.specs.board"), v: "ESP32-S3" },
    { k: t("hx1.inside.specs.light"), v: "7 RGB LEDs" },
  ];
  const parts = t("hx1.inside.parts", { returnObjects: true }) as Item[];

  const body: CSSProperties = {
    fontSize: 17,
    lineHeight: 1.6,
    maxWidth: 640,
    margin: 0,
    opacity: 0.9,
  };
  const rowGrid = (cols: number): CSSProperties => ({
    display: "grid",
    gridTemplateColumns: isMobile ? "1fr" : `repeat(${cols}, minmax(0, 1fr))`,
    gap: isMobile ? 28 : 40,
    borderTop: "1px solid var(--hux-fg-line)",
    paddingTop: 28,
  });
  const itemK: CSSProperties = {
    fontFamily: "var(--hux-mono)",
    fontSize: 11,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    opacity: 0.75,
    marginBottom: 10,
  };
  const itemV: CSSProperties = {
    fontSize: 16,
    lineHeight: 1.55,
    margin: 0,
    opacity: 0.9,
  };

  return (
    <div
      className="hux-landing"
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

      {/* The story: the device pinned while the page scrolls past it. */}
      <HX1Story />

      {/* What's inside, in detail: the story's last beat names the parts */}
      <section
        style={{
          ...paperSection,
          position: "relative",
          zIndex: 2,
          padding: pad,
          display: "grid",
          gap: 48,
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: isMobile
              ? "repeat(2, minmax(0, 1fr))"
              : isTablet
                ? "repeat(3, minmax(0, 1fr))"
                : "repeat(6, minmax(0, 1fr))",
            borderTop: "1px solid var(--hux-fg)",
          }}
        >
          {specs.map((s, i) => (
            <div
              key={i}
              style={{
                padding: "16px 12px 18px 0",
                borderBottom: "1px solid var(--hux-fg-line)",
                minWidth: 0,
              }}
            >
              <div style={{ ...itemK, marginBottom: 6 }}>{s.k}</div>
              <div
                style={{
                  fontFamily: "var(--hux-serif)",
                  fontSize: "clamp(22px, 2.2vw, 30px)",
                  lineHeight: 1.1,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {s.v}
              </div>
            </div>
          ))}
        </div>
        <div style={rowGrid(3)}>
          {parts.map((p, i) => (
            <Reveal key={i} delay={(i % 3) * 90}>
              <div style={itemK}>{p.k}</div>
              <p style={itemV}>{p.v}</p>
            </Reveal>
          ))}
        </div>
      </section>

      {/* How it talks to Huxley */}
      <section
        id="how"
        style={{
          ...deepSection,
          position: "relative",
          zIndex: 2,
          padding: pad,
          display: "grid",
          gap: 48,
        }}
      >
        <SectionHead
          eyebrow={t("hx1.how.eyebrow")}
          title={t("hx1.how.title")}
          subtitle={t("hx1.how.subtitle")}
        />
        <ol
          style={{
            ...rowGrid(3),
            listStyle: "none",
            margin: 0,
            paddingLeft: 0,
          }}
        >
          {how.map((p, i) => (
            <Reveal as="li" key={i} delay={i * 90}>
              <div style={itemK}>
                {String(i + 1).padStart(2, "0")} · {p.k}
              </div>
              <p style={itemV}>{p.v}</p>
            </Reveal>
          ))}
        </ol>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          <a style={chipGhost} href="/docs/concepts/personas">
            {t("hx1.how.linkPersonas")}
          </a>
          <a style={chipGhost} href={FIRMWARE_SRC}>
            {t("hx1.how.linkFirmware")} ↗
          </a>
        </div>
      </section>

      {/* Made to be made */}
      <section
        style={{
          ...coralSection,
          position: "relative",
          zIndex: 2,
          padding: pad,
          display: "grid",
          gap: 32,
        }}
      >
        <SectionHead
          eyebrow={t("hx1.made.eyebrow")}
          title={t("hx1.made.title")}
        />
        <p style={body}>{t("hx1.made.body1")}</p>
        <p style={body}>{t("hx1.made.body2")}</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          <a style={chipSolid} href="/hx-1/build">
            {t("hx1.made.guide")}
          </a>
          <a style={chipGhost} href={ENCLOSURE_SRC}>
            {t("hx1.made.link")} ↗
          </a>
        </div>
      </section>

      <Footer />
    </div>
  );
}
