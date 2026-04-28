// Sticky waveform under the nav. Driven by the active VoiceState (most-
// visible section) and the page scroll progress. Past-of-playhead is bright,
// future is dimmed — reads like a podcast scrubber. Each section gets a
// chapter marker. Ported from the prototype.

import { useEffect, useRef } from "react";
import { useVoiceState, type VoiceState } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";

interface ChapterMeta {
  id: string;
  label: string;
  position: number; // 0..1
}

interface VoiceThreadProps {
  sections: ChapterMeta[];
  height?: number;
}

interface StateParams {
  amp: number;
  freq: number;
  speed: number;
  density: number;
  jitter: number;
}

const TARGET: Record<VoiceState, StateParams> = {
  idle: { amp: 3, freq: 0.022, speed: 0.35, density: 1.0, jitter: 0.1 },
  listening: {
    amp: 10,
    freq: 0.08,
    speed: 1.8,
    density: 2.2,
    jitter: 0.5,
  },
  thinking: {
    amp: 6,
    freq: 0.012,
    speed: 0.5,
    density: 0.6,
    jitter: 0.15,
  },
  speaking: { amp: 14, freq: 0.06, speed: 1.4, density: 2.5, jitter: 0.9 },
  interrupt: {
    amp: 18,
    freq: 0.14,
    speed: 3.0,
    density: 3.0,
    jitter: 1.6,
  },
};

export function VoiceThread({ sections, height = 58 }: VoiceThreadProps) {
  const { state: activeState, scrollProgress } = useVoiceState();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const stateRef = useRef<VoiceState>(activeState);
  const pulseRef = useRef(0);
  useEffect(() => {
    if (stateRef.current !== activeState) pulseRef.current = 1;
    stateRef.current = activeState;
  }, [activeState]);

  const progressRef = useRef(scrollProgress);
  useEffect(() => {
    progressRef.current = scrollProgress;
  }, [scrollProgress]);

  const paramsRef = useRef<StateParams>({
    amp: 4,
    freq: 0.04,
    speed: 0.5,
    density: 1,
    jitter: 0,
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let w = wrap.clientWidth;
    const h = height;

    const resize = () => {
      w = wrap.clientWidth;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    const start = performance.now();
    const draw = (now: number) => {
      const t = (now - start) / 1000;
      const p = paramsRef.current;
      const tgt = TARGET[stateRef.current] ?? TARGET.idle;
      // smooth lerp toward target params
      (Object.keys(p) as (keyof StateParams)[]).forEach((k) => {
        p[k] += (tgt[k] - p[k]) * 0.06;
      });

      pulseRef.current *= 0.92;
      const pulse = pulseRef.current;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const mid = h / 2;
      const playhead = Math.max(0, Math.min(1, progressRef.current)) * w;

      // Cap amplitude so the waveform never touches the edges.
      const MAX_AMP = Math.max(2, h / 2 - 5);

      const steps = Math.max(120, Math.floor(w / 2));
      ctx.lineJoin = "round";
      ctx.lineCap = "round";

      const pts: Array<[number, number]> = [];
      for (let i = 0; i <= steps; i++) {
        const x = (i / steps) * w;
        const phase = x * p.freq + t * p.speed;
        let y = 0;
        y += Math.sin(phase) * 1.0;
        y += Math.sin(phase * 2.1 + 0.7) * 0.5 * p.density;
        y += Math.sin(phase * 3.7 + 1.3) * 0.28 * p.density;
        y +=
          Math.sin(phase * 7.3 + Math.sin(phase * 0.9) * 2) * 0.35 * p.jitter;
        if (pulse > 0.01) {
          const dx = Math.abs(x - playhead) / Math.max(40, w * 0.08);
          const falloff = Math.exp(-dx * dx);
          y += falloff * pulse * 3.5 * Math.sin(phase * 1.5 + t * 4);
        }
        let amp = p.amp * (0.9 + 0.1 * Math.sin(t * 0.5 + x * 0.01));
        amp *= 1 + pulse * 0.4;
        const yPx = y * amp;
        const clipped = Math.max(-MAX_AMP, Math.min(MAX_AMP, yPx));
        pts.push([x, mid + clipped]);
      }

      // Future (post-playhead) — thin, low-contrast
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(pts[0]![0], pts[0]![1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i]![0], pts[i]![1]);
      ctx.strokeStyle = "color-mix(in oklab, currentColor 30%, transparent)";
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.restore();

      // Past (pre-playhead) — solid, bright, clipped at playhead
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, playhead, h);
      ctx.clip();
      ctx.beginPath();
      ctx.moveTo(pts[0]![0], pts[0]![1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i]![0], pts[i]![1]);
      ctx.strokeStyle = "currentColor";
      ctx.globalAlpha = 0.95;
      ctx.lineWidth = 1.6;
      ctx.shadowColor = "currentColor";
      ctx.shadowBlur = 4;
      ctx.stroke();
      ctx.restore();

      // Centerline
      ctx.save();
      ctx.strokeStyle = "currentColor";
      ctx.globalAlpha = 0.12;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();
      ctx.restore();

      // Playhead
      ctx.save();
      ctx.strokeStyle = "currentColor";
      ctx.globalAlpha = 0.9;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(playhead, 2);
      ctx.lineTo(playhead, h - 2);
      ctx.stroke();
      ctx.fillStyle = "currentColor";
      ctx.beginPath();
      ctx.arc(playhead, mid, 3.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [height]);

  return (
    <div
      ref={wrapRef}
      style={{
        position: "relative",
        width: "100%",
        height,
        color: "var(--hux-fg)",
      }}
    >
      <canvas
        ref={canvasRef}
        style={{ display: "block", width: "100%", height }}
      />
      <ChapterLayer sections={sections} />
    </div>
  );
}

function ChapterLayer({ sections }: { sections: ChapterMeta[] }) {
  const { id: activeSection } = useVoiceState();
  const { isMobile } = useViewport();
  if (!sections || !sections.length) return null;
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
      }}
    >
      {sections.map((s) => {
        const active = s.id === activeSection;
        // Earn the bar's vertical real estate: chapter markers are clickable
        // anchors that scroll-to-section, not pure decoration. Per critic P1-3.
        const onClick = (e: React.MouseEvent) => {
          e.preventDefault();
          if (s.id === "hero") {
            window.scrollTo({ top: 0, behavior: "smooth" });
            return;
          }
          const el = document.getElementById(s.id);
          if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        };
        return (
          <a
            key={s.id}
            href={s.id === "hero" ? "#" : `#${s.id}`}
            onClick={onClick}
            aria-label={s.label}
            style={{
              position: "absolute",
              left: `${s.position * 100}%`,
              top: 0,
              bottom: 0,
              transform: "translateX(-50%)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "4px 0",
              minWidth: isMobile ? 28 : 0,
              pointerEvents: "auto",
              cursor: "pointer",
              textDecoration: "none",
              color: "inherit",
            }}
          >
            <span
              style={{
                width: 1,
                height: 8,
                background: "currentColor",
                opacity: active ? 0.9 : 0.3,
                transition: "opacity 300ms ease",
              }}
            />
            {!isMobile && (
              <span
                style={{
                  fontFamily: "var(--hux-mono)",
                  fontSize: 9,
                  letterSpacing: "0.14em",
                  textTransform: "uppercase",
                  color: "var(--hux-fg)",
                  opacity: active ? 0.95 : 0.4,
                  whiteSpace: "nowrap",
                  transition: "opacity 300ms ease, transform 300ms ease",
                  transform: active ? "translateY(-1px)" : "translateY(0)",
                }}
              >
                {s.label}
              </span>
            )}
            <span
              style={{
                width: 1,
                height: 8,
                background: "currentColor",
                opacity: active ? 0.9 : 0.3,
                transition: "opacity 300ms ease",
              }}
            />
          </a>
        );
      })}
    </div>
  );
}
