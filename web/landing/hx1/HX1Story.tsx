"use client";
// The top of /hx-1: one 3D HX-1 that stays on screen while the page
// scrolls past it, after play.date. Four beats, in the order a hand meets
// the device:
//   hero    big and centred, turning slowly; a sideways drag turns it, and
//           a bubble says what the dots on its face say: "hola."
//   dots    close on the face; the braille letters light up in reading order
//   lever   the right side; the lever presses in, holds, lets go, and the
//           exchange it makes appears beside it
//   inside  the device comes apart, its main parts named
// The words of each beat live on the pinned stage, in a fixed place (a
// column on the left, or under the model on a phone or any portrait
// screen), and only fade in and out, when the model holds a pose: they
// never move, so they never cross the model. Colour blocks behind the
// transparent canvas slide past at each beat. On phones and with reduced
// motion the camera cuts between fixed poses. The hero's model is fitted
// between the site's nav and the hero's words (Stage.band).

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { chipGhost, chipSolid } from "../components/Chrome";
import { useViewport } from "../lib/useViewport";
import type { Pose, Stage, V3 } from "./stage";

// A part's name as a translation key: "Speaker, 40 mm 5 W" -> "speaker_40_mm_5_w".
export const partKey = (label: string) =>
  label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");

// Beat lengths, in screens. The stage is pinned for their sum less one.
const BEATS = { hero: 1.0, dots: 1.4, lever: 1.8, inside: 1.8 } as const;
const START = {
  hero: 0,
  dots: BEATS.hero,
  lever: BEATS.hero + BEATS.dots,
  inside: BEATS.hero + BEATS.dots + BEATS.lever,
};
const TOTAL = BEATS.hero + BEATS.dots + BEATS.lever + BEATS.inside;

// Poses: camera position and target in metres (the device's base centred
// on the origin, front toward +Z), the film shift, and what it is doing.
const base = { explode: 0, lever: 0, dots: 4, spin: 0, shift: 0.42, fit: 0 };
const POSE: Record<string, Pose> = {
  // The hero is fitted between the site's nav and its own words (fit, and
  // Stage.band), so the two never meet at any window size.
  hero: {
    ...base,
    pos: [0.16, 0.12, 0.43],
    tgt: [0, 0.031, 0],
    shift: 0,
    spin: 1,
    fit: 1,
  },
  dots: { ...base, pos: [0.06, 0.085, 0.3], tgt: [0, 0.056, 0], dots: 0 },
  dotsLit: { ...base, pos: [0.05, 0.083, 0.28], tgt: [0, 0.056, 0] },
  lever: { ...base, pos: [0.34, 0.1, 0.15], tgt: [0.008, 0.05, 0] },
  leverDown: {
    ...base,
    pos: [0.33, 0.1, 0.14],
    tgt: [0.008, 0.05, 0],
    lever: 1,
  },
  inside: { ...base, pos: [0.5, 0.3, 0.56], tgt: [0, 0.048, 0.07], explode: 1 },
};
// Keyframes along the scroll (in screens from the top of the story).
const PRESS = START.lever + 0.55; // the lever reaches the click
const RELEASE = START.lever + 0.95; // and is let go
const KEYS: [number, Pose][] = [
  [0, POSE.hero],
  [0.35, POSE.hero],
  [START.dots + 0.05, POSE.dots],
  [START.dots + 0.25, POSE.dots],
  [START.dots + 0.95, POSE.dotsLit],
  [START.lever - 0.1, POSE.dotsLit],
  [START.lever + 0.25, POSE.lever],
  [PRESS - 0.15, POSE.lever],
  [PRESS, POSE.leverDown],
  [RELEASE - 0.15, POSE.leverDown],
  [RELEASE, POSE.lever],
  [START.inside - 0.05, POSE.lever],
  [START.inside + 0.35, POSE.inside],
  [TOTAL, POSE.inside],
];
// When each beat's words show (in screens), while the model holds its pose.
// A colour block's edge sweeps up the screen as the page scrolls: each
// beat's words are gone before it reaches them, and come back once it has
// passed. The edges sit a little after the beats change (EDGE).
const EDGE = { lever: START.lever + 0.3, inside: START.inside + 0.5 };
// [top, bottom] of the words, as shares of the screen's height: measured from
// the boxes themselves (a longer language runs further down), else a guess.
function windows(band: [number, number]) {
  const [top, bottom] = band;
  const before = (edge: number) => edge - bottom - 0.03; // gone before the edge arrives
  const after = (edge: number) => edge - top + 0.03; // back once it is past
  return {
    dots: [START.dots - 0.15, before(EDGE.lever)],
    lever: [after(EDGE.lever), before(EDGE.inside)],
    inside: [after(EDGE.inside), TOTAL],
  } as Record<string, [number, number]>;
}
// Phones and reduced motion: one fixed pose per beat, cut, not flown.
const STILL: [number, Pose][] = [
  [0, POSE.hero],
  [START.dots - 0.3, POSE.dotsLit],
  [START.lever - 0.3, POSE.lever],
  [PRESS - 0.05, POSE.leverDown],
  [RELEASE, POSE.lever],
  [START.inside - 0.1, POSE.inside],
];

const smooth = (x: number) => x * x * (3 - 2 * x);
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const mix = (a: number, b: number, k: number) => a + (b - a) * k;
function poseAt(s: number): Pose {
  let i = 0;
  while (i < KEYS.length - 2 && s > KEYS[i + 1][0]) i++;
  const [s0, a] = KEYS[i];
  const [s1, b] = KEYS[i + 1];
  const k = smooth(clamp01((s - s0) / Math.max(1e-6, s1 - s0)));
  const v3 = (x: V3, y: V3): V3 => [
    mix(x[0], y[0], k),
    mix(x[1], y[1], k),
    mix(x[2], y[2], k),
  ];
  return {
    pos: v3(a.pos, b.pos),
    tgt: v3(a.tgt, b.tgt),
    shift: mix(a.shift, b.shift, k),
    explode: mix(a.explode, b.explode, k),
    lever: mix(a.lever, b.lever, k),
    dots: mix(a.dots, b.dots, k),
    spin: mix(a.spin, b.spin, k),
    fit: mix(a.fit, b.fit, k),
  };
}
function stillAt(s: number): Pose {
  let p = STILL[0][1];
  for (const [at, q] of STILL) if (s >= at) p = q;
  return p;
}
// On a phone the model has the top of a narrow screen to itself: centred,
// and further back so it fits the width.
function onPhone(p: Pose): Pose {
  const k = p.spin > 0.5 ? 1.12 : 1.45; // the hero fills its part of the screen
  const pos = p.pos.map((v, i) => p.tgt[i] + (v - p.tgt[i]) * k) as V3;
  return { ...p, pos, shift: 0 };
}

// The poses are framed for a 1280 x 800 window; a narrower one (a tablet,
// a half-width window) moves the camera back so the model still fits.
function fitWidth(p: Pose): Pose {
  const aspect = window.innerWidth / Math.max(1, window.innerHeight);
  const k = Math.max(1, 1.6 / aspect);
  if (k === 1) return p;
  const pos = p.pos.map((v, i) => p.tgt[i] + (v - p.tgt[i]) * k) as V3;
  return { ...p, pos };
}

// The page's light-section variables: chips and lines read dark on paper.
const ON_PAPER = {
  "--hux-fg": "var(--hux-ink)",
  "--hux-fg-dim": "var(--hux-ink-dim)",
  "--hux-fg-line": "var(--hux-ink-line)",
  "--hux-fg-faint": "var(--hux-ink-faint)",
} as CSSProperties;

// The parts named in the inside beat, each on a fixed side of its part so
// the names don't pile up.
const LABELS: { label: string; side: "left" | "right" }[] = [
  { label: "ESP32-S3 audio board", side: "left" },
  { label: "18650 cell", side: "left" },
  { label: "12 × 12 tact switch", side: "right" },
  { label: "Talk lever", side: "right" },
  { label: "Speaker, 40 mm 5 W", side: "right" },
  { label: "Braille dot, glued", side: "right" },
];
type Box = { l: number; t: number; r: number; b: number };
const hits = (a: Box, b: Box, gap = 0) =>
  a.l < b.r + gap && a.r + gap > b.l && a.t < b.b + gap && a.b + gap > b.t;

export function HX1Story() {
  const { t, i18n } = useTranslation();
  const { isMobile, isTablet } = useViewport();
  const stillRef = useRef(isMobile);
  stillRef.current = isMobile;
  // A portrait screen (a tablet held upright) stacks like a phone, the model
  // above its words, but keeps the flown camera.
  const [portrait, setPortrait] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-aspect-ratio: 1/1)");
    const on = () => setPortrait(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const stacked = isMobile || portrait;
  const stackedRef = useRef(stacked);
  stackedRef.current = stacked;
  // Reduced motion: cut between fixed poses instead of flying the camera.
  const reduced =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const root = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  const heroText = useRef<HTMLDivElement>(null);
  const beatText = useRef<Record<string, HTMLDivElement | null>>({});
  const chat = useRef<(HTMLDivElement | null)[]>([]);
  const labelRefs = useRef<(HTMLDivElement | null)[]>([]);
  const stage = useRef<Stage | null>(null);
  const sRef = useRef(0);
  // Sizes read once per resize or language, not every frame: the labels'
  // pills, the inside words' box, and the band the beats' words fill.
  const sizes = useRef<{
    pills: { w: number; h: number }[];
    words: Box;
    hero: [number, number]; // the band the hero's model fills (host pixels)
  } | null>(null);
  const band = useRef<[number, number]>(isMobile ? [0.61, 0.95] : [0.24, 0.72]);
  const [status, setStatus] = useState<"loading" | "ready" | "failed">(
    "loading",
  );
  const [progress, setProgress] = useState(0);
  // How far down the page the story starts (the site's nav): the hero's
  // words sit that much higher, so they are on the first screen.
  const [lead, setLead] = useState(0);
  const leadRef = useRef(0);
  leadRef.current = lead;
  useEffect(() => {
    const measureLead = () => {
      const r = root.current;
      if (r) setLead(Math.max(0, Math.round(r.getBoundingClientRect().top + window.scrollY)));
    };
    measureLead();
    window.addEventListener("resize", measureLead);
    return () => window.removeEventListener("resize", measureLead);
  }, []);

  // The stage: built once, posed from the scroll on every frame it draws
  // (so a scroll event that slipped by can't leave it in an old pose).
  useEffect(() => {
    let dead = false;
    const el = host.current;
    if (!el) return;
    import("./stage")
      .then(({ createStage }) =>
        createStage(el, {
          onProgress: (pct) => !dead && setProgress(pct),
          onReady: () => !dead && setStatus("ready"),
          onFail: () => !dead && setStatus("failed"),
          onFrame: () => {
            update();
            place();
          },
        }),
      )
      .then((st) => {
        if (dead) return st.dispose();
        stage.current = st;
        update(true);
      })
      .catch(() => !dead && setStatus("failed"));
    return () => {
      dead = true;
      stage.current?.dispose();
      stage.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Overlays that follow the model: the "hola." bubble and the part names.
  function place() {
    const st = stage.current;
    if (!st) return;
    const s = sRef.current;
    const b = bubble.current;
    if (b) {
      const at = st.project(st.hola());
      if (at) b.style.transform = `translate(${at.x}px, ${at.y}px)`;
      // Only while the dots face the camera (the hero turns), and only once
      // there is a model (facing is 0 before it loads, or if it failed).
      const op = at
        ? (1 - smooth(clamp01(s / 0.22))) * smooth(clamp01((st.facing() - 0.35) / 0.35))
        : 0;
      b.style.opacity = String(op);
      b.style.visibility = op > 0.01 ? "visible" : "hidden";
    }
    // Names show once the parts are out where they point, not just once the
    // page is there: a jump (End, a scrollbar drag) would otherwise name a
    // closed case while it is still coming apart.
    const shown =
      smooth(clamp01((s - START.inside - 0.25) / 0.15)) *
      smooth(clamp01((st.exploded() - 0.85) / 0.15));
    if (shown > 0 && !sizes.current) measure();
    const placed: Box[] = [];
    LABELS.forEach(({ label, side }, i) => {
      const el = labelRefs.current[i];
      if (!el) return;
      const a = shown > 0 ? st.anchor(label) : null;
      const at = a && st.project(a);
      let fits = !!at;
      if (at && sizes.current) {
        el.style.transform = `translate(${at.x}px, ${at.y}px)`;
        // The pill's box, from where it hangs off its point (see its style).
        const { w, h } = sizes.current.pills[i];
        const l = side === "left" ? at.x + 4 - w : at.x - 4;
        const r: Box = { l, t: at.y - 12, r: l + w, b: at.y - 12 + h };
        // A name that would sit on the words, on another name or off the
        // screen is left out (the list is in order of importance).
        fits =
          !hits(r, sizes.current.words, 12) &&
          !placed.some((q) => hits(r, q, 6)) &&
          r.l >= 8 &&
          r.r <= window.innerWidth - 8;
        if (fits) placed.push(r);
      }
      el.style.opacity = String(fits ? shown : 0);
    });
  }

  function measure() {
    const words = beatText.current.inside;
    sizes.current = {
      pills: labelRefs.current.map((el) => {
        const pill = el?.firstElementChild as HTMLElement | null | undefined;
        return { w: pill?.offsetWidth ?? 0, h: pill?.offsetHeight ?? 0 };
      }),
      // From under the nav (the host's top, before the stage pins) to just
      // above the hero's words.
      hero: [
        16,
        Math.max(
          48,
          Math.min(
            (heroText.current?.offsetTop ?? window.innerHeight) - 28,
            (host.current?.clientHeight ?? window.innerHeight) - 16,
          ),
        ),
      ],
      words: words
        ? {
            l: words.offsetLeft,
            t: words.offsetTop,
            r: words.offsetLeft + words.offsetWidth,
            b: words.offsetTop + words.offsetHeight,
          }
        : { l: 0, t: 0, r: 0, b: 0 },
    };
    // The band the beats' words fill, as shares of the stage's height.
    const boxes = Object.values(beatText.current).filter(Boolean) as HTMLElement[];
    const h = boxes[0]?.offsetParent?.clientHeight || window.innerHeight;
    if (boxes.length)
      band.current = [
        Math.min(...boxes.map((x) => x.offsetTop / h)),
        Math.max(...boxes.map((x) => (x.offsetTop + x.offsetHeight) / h)),
      ];
  }

  // Words that are not showing can't be clicked, focused or selected.
  function show(el: HTMLElement | null | undefined, on: boolean) {
    if (!el || el.dataset.on === String(on)) return;
    el.dataset.on = String(on);
    el.style.opacity = on ? "1" : "0";
    el.style.visibility = on ? "visible" : "hidden";
    el.style.pointerEvents = on ? "auto" : "none";
    if (!reduced) el.style.transform = `translateY(${on ? 0 : 12}px)`;
  }

  function update(instant = false) {
    const r = root.current;
    if (!r) return;
    const s = Math.min(
      TOTAL,
      Math.max(0, -r.getBoundingClientRect().top / window.innerHeight),
    );
    sRef.current = s;
    // The hero's words leave before they could slide over the device.
    const h = heroText.current;
    if (h) {
      // Scrolled so far; the words ride up with the model until the stage
      // pins (lead), and are gone by the time they would reach it.
      const scrolled = leadRef.current - r.getBoundingClientRect().top;
      const op = 1 - smooth(clamp01(scrolled / (leadRef.current + 28)));
      h.style.opacity = String(op);
      // gone, not just transparent: its links sit over the device
      h.style.visibility = op > 0.01 ? "visible" : "hidden";
    }
    const st = stage.current;
    const phone = stillRef.current;
    const cut = phone || reduced;
    const stack = stackedRef.current;
    if (st) {
      st.pose(
        stack ? onPhone(cut ? stillAt(s) : poseAt(s)) : fitWidth(cut ? stillAt(s) : poseAt(s)),
        instant || cut,
      );
      st.drag(s < 0.3);
    }
    if (!sizes.current) measure();
    if (st && sizes.current) st.band(...sizes.current.hero);
    for (const [key, [a, b]] of Object.entries(windows(band.current)))
      show(beatText.current[key], s >= a && s < b);
    // The exchange: asked while the lever is held, answered once let go.
    show(chat.current[0], s >= PRESS - 0.05);
    show(chat.current[1], s >= RELEASE + 0.05);
  }

  useEffect(() => {
    let raf = 0;
    const onScroll = (e?: Event) => {
      if (e?.type === "resize") sizes.current = null;
      if (!raf)
        raf = requestAnimationFrame(() => {
          raf = 0;
          update();
        });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    sizes.current = null;
    update(true);
    // the words' heights change once the web fonts arrive
    document.fonts?.ready.then(() => {
      sizes.current = null;
      onScroll();
    });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(raf);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMobile, isTablet, stacked, i18n.language, lead]);

  const vh = (n: number) => `${n * 100}svh`;
  // Where the words sit: a left column beside the model, or under it.
  const textBox: CSSProperties = stacked
    ? { position: "absolute", left: 24, right: 24, top: "61svh", maxWidth: 560 }
    : {
        position: "absolute",
        left: 64,
        top: "24svh",
        width: isTablet ? 360 : 460,
      };
  const fade: CSSProperties = {
    opacity: 0,
    transform: reduced ? "none" : "translateY(12px)",
    visibility: "hidden",
    transition:
      "opacity .45s cubic-bezier(.2,.7,.2,1), transform .45s cubic-bezier(.2,.7,.2,1), visibility .45s",
  };
  const eyebrow: CSSProperties = {
    fontFamily: "var(--hux-mono)",
    fontSize: 11,
    letterSpacing: "0.14em",
    textTransform: "uppercase",
    opacity: 0.75,
    marginBottom: isMobile ? 12 : 18,
  };
  const h2: CSSProperties = {
    fontFamily: "var(--hux-serif)",
    fontWeight: 400,
    fontSize: isMobile ? 36 : "clamp(48px, 5vw, 76px)",
    lineHeight: 0.98,
    letterSpacing: "-0.015em",
    margin: isMobile ? "0 0 12px" : "0 0 20px",
    textWrap: "balance",
  };
  const body: CSSProperties = {
    fontSize: isMobile ? 15 : 18,
    lineHeight: 1.5,
    margin: 0,
    opacity: 0.88,
    textWrap: "pretty",
  };
  const said = (mine: boolean): CSSProperties => ({
    ...fade,
    alignSelf: mine ? "flex-start" : "flex-end",
    maxWidth: "80%",
    padding: isMobile ? "9px 14px" : "12px 16px",
    borderRadius: 18,
    borderBottomLeftRadius: mine ? 4 : 18,
    borderBottomRightRadius: mine ? 18 : 4,
    background: mine ? "var(--hux-paper)" : "var(--hux-ink)",
    color: mine ? "var(--hux-ink)" : "var(--hux-paper)",
    fontSize: isMobile ? 15 : 17,
    lineHeight: 1.35,
  });

  return (
    <section
      ref={root}
      aria-label={t("hx1.story.aria")}
      style={{ position: "relative", height: vh(TOTAL), zIndex: 2 }}
    >
      {/* Colour blocks behind the transparent canvas. */}
      <div aria-hidden style={{ position: "absolute", inset: 0, zIndex: 0 }}>
        <div
          style={{
            height: vh(BEATS.hero),
            background: "var(--hux-paper)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          {/* The giant wordmark behind the device. */}
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: isMobile ? "8svh" : "6svh",
              textAlign: "center",
              fontFamily: "var(--hux-serif)",
              fontSize: isMobile ? "44vw" : "min(34vw, 62svh)",
              lineHeight: 0.8,
              letterSpacing: "-0.04em",
              whiteSpace: "nowrap",
              userSelect: "none",
            }}
          >
            <span style={{ color: "var(--hux-coral)" }}>HX</span>
            <span
              style={{
                color:
                  "color-mix(in oklab, var(--hux-coral) 45%, var(--hux-paper))",
              }}
            >
              -1
            </span>
          </div>
        </div>
        <div
          style={{
            height: vh(EDGE.lever - START.dots),
            background: "var(--hux-paper)",
          }}
        />
        <div
          style={{
            height: vh(EDGE.inside - EDGE.lever),
            background: "var(--hux-coral)",
          }}
        />
        <div
          style={{
            height: vh(TOTAL - EDGE.inside),
            background: "var(--hux-coral-xdk)",
          }}
        />
      </div>

      {/* The hero's words, in the page: they leave as soon as it scrolls. */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: vh(BEATS.hero),
          zIndex: 2,
          pointerEvents: "none",
        }}
      >
        <div
          ref={heroText}
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            // under the device: on a phone, right below it; else at the bottom
            ...(isMobile ? { top: "57svh" } : { bottom: 40 + lead }),
            display: "grid",
            justifyItems: "center",
            textAlign: "center",
            padding: "0 24px",
            color: "var(--hux-ink)",
            ...ON_PAPER,
          }}
        >
          <h1
            style={{
              ...h2,
              fontSize: isMobile ? 38 : "clamp(34px, min(4.6vw, 7.5svh), 72px)",
              margin: "0 0 14px",
            }}
          >
            {t("hx1.titleLine1")} <em>{t("hx1.titleLine2")}</em>
          </h1>
          <p
            style={{
              ...body,
              fontSize: isMobile ? 15 : "clamp(15px, 2.3svh, 18px)",
              maxWidth: 620,
              margin: "0 0 18px",
            }}
          >
            {t("hx1.subtitle")}
          </p>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 10,
              justifyContent: "center",
              pointerEvents: "auto",
            }}
          >
            <a style={{ ...chipSolid, color: "var(--hux-paper)" }} href="#how">
              {t("hx1.ctaHow")}
            </a>
            <a style={chipGhost} href="/hx-1/build">
              {t("hx1.ctaBuild")}
            </a>
          </div>
        </div>
      </div>

      {/* The pinned stage: the model, what follows it, and the beats' words. */}
      <div
        style={{
          position: "sticky",
          top: 0,
          height: "100svh",
          zIndex: 1,
          pointerEvents: "none",
        }}
      >
        <div
          ref={host}
          role="img"
          aria-label={t("hx1.viewer.aria")}
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            top: 0,
            height: stacked ? "60%" : "100%",
            pointerEvents: "auto",
          }}
        />
        {status !== "ready" && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "grid",
              placeItems: "center",
              fontFamily: "var(--hux-mono)",
              fontSize: 11,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              color: "var(--hux-ink)",
              opacity: 0.6,
            }}
          >
            {status === "failed" ? (
              <span role="alert">{t("hx1.viewer.failed")}</span>
            ) : (
              <span>
                {t("hx1.viewer.loading")} {progress}%
              </span>
            )}
          </div>
        )}

        {/* "hola." beside the dots. */}
        <div
          ref={bubble}
          aria-hidden
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            opacity: 0,
            willChange: "transform",
          }}
        >
          <div
            style={{
              position: "absolute",
              left: 4,
              bottom: 4,
              padding: "10px 16px 12px",
              background: "var(--hux-ink)",
              color: "var(--hux-paper)",
              borderRadius: 18,
              borderBottomLeftRadius: 4,
              fontFamily: "var(--hux-serif)",
              fontSize: isMobile ? 24 : 32,
              lineHeight: 1,
              whiteSpace: "nowrap",
              boxShadow: "0 10px 30px rgba(0,0,0,0.18)",
            }}
          >
            hola.
          </div>
        </div>

        {/* Part names for the inside beat. */}
        {!isMobile &&
          LABELS.map(({ label, side }, i) => (
            <div
              key={label}
              ref={(el) => {
                labelRefs.current[i] = el;
              }}
              aria-hidden
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                opacity: 0,
                willChange: "transform",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: -12,
                  ...(side === "left" ? { right: -4 } : { left: -4 }),
                  display: "flex",
                  flexDirection: side === "left" ? "row-reverse" : "row",
                  alignItems: "center",
                  gap: 8,
                  whiteSpace: "nowrap",
                  fontFamily: "var(--hux-mono)",
                  fontSize: 12,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--hux-ink)",
                }}
              >
                <span
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: 99,
                    background: "var(--hux-paper)",
                    border: "2px solid var(--hux-coral)",
                    flex: "none",
                  }}
                />
                <span
                  style={{
                    background: "var(--hux-paper)",
                    padding: "5px 9px",
                    borderRadius: 99,
                    boxShadow: "0 6px 18px rgba(0,0,0,0.25)",
                  }}
                >
                  {t(`hx1.partNames.${partKey(label)}`, {
                    defaultValue: label,
                  })}
                </span>
              </div>
            </div>
          ))}

        {/* The beats' words: fixed in place, fading with the beat. */}
        <div
          ref={(el) => {
            beatText.current.dots = el;
          }}
          style={{ ...textBox, ...fade, color: "var(--hux-ink)" }}
        >
          <div style={eyebrow}>{t("hx1.story.gap.eyebrow")}</div>
          <h2 style={h2}>{t("hx1.story.gap.title")}</h2>
          <p style={body}>{t("hx1.story.gap.body")}</p>
        </div>
        <div
          ref={(el) => {
            beatText.current.lever = el;
          }}
          style={{ ...textBox, ...fade, color: "var(--hux-ink)", ...ON_PAPER }}
        >
          {/* Ink at full strength on the coral: 4.9:1, where white is 3.8:1. */}
          <div style={{ ...eyebrow, opacity: 1 }}>
            {t("hx1.story.lever.eyebrow")}
          </div>
          <h2 style={h2}>{t("hx1.story.lever.title")}</h2>
          <p style={{ ...body, opacity: 1 }}>{t("hx1.story.lever.body")}</p>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 8,
              marginTop: isMobile ? 14 : 24,
            }}
          >
            <div
              ref={(el) => {
                chat.current[0] = el;
              }}
              style={said(true)}
            >
              {t("hx1.story.lever.ask")}
            </div>
            <div
              ref={(el) => {
                chat.current[1] = el;
              }}
              style={said(false)}
            >
              {t("hx1.story.lever.answer")}
            </div>
          </div>
        </div>
        <div
          ref={(el) => {
            beatText.current.inside = el;
          }}
          style={{ ...textBox, ...fade, color: "var(--hux-fg)" }}
        >
          <div style={eyebrow}>{t("hx1.inside.eyebrow")}</div>
          <h2 style={h2}>{t("hx1.inside.title")}</h2>
          <p style={body}>{t("hx1.story.inside.body")}</p>
        </div>
      </div>
    </section>
  );
}
