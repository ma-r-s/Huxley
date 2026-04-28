// Huxley landing orb — canonical product orb, ported from the design
// prototype's shared.jsx (which itself was copied from the product Huxley.html).
// Canvas 2D, 3-octave value-noise ring blended with a synthetic audio envelope.
// Lives at /src/components/Orb.tsx so the rest of the landing imports from one
// stable place. Behavior is identical to the live PWA's Orb in Huxley/web — we
// duplicate it here rather than share across repos because it's one canvas
// component and a sister-app extraction would be premature.

import { useEffect, useRef } from "react";

type OrbState = "idle" | "listening" | "thinking" | "speaking";

// ── Pseudo-noise (matches product) ────────────────────────────────────────
function makeNoise(seed = 1): (x: number, y: number) => number {
  const p = new Uint8Array(512);
  let s = seed * 9301 + 49297;
  const rand = () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
  const perm: number[] = Array.from({ length: 256 }, (_, i) => i);
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [perm[i], perm[j]] = [perm[j]!, perm[i]!];
  }
  for (let i = 0; i < 512; i++) p[i] = perm[i & 255]!;
  const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);
  const lerp = (a: number, b: number, t: number) => a + t * (b - a);
  const grad = (h: number, x: number) => ((h & 1) === 0 ? x : -x);
  return (x: number, y: number) => {
    const X = Math.floor(x) & 255;
    const Y = Math.floor(y) & 255;
    const xf = x - Math.floor(x);
    const yf = y - Math.floor(y);
    const u = fade(xf);
    const v = fade(yf);
    const aa = p[p[X]! + Y]!;
    const ab = p[p[X]! + Y + 1]!;
    const ba = p[p[X + 1]! + Y]!;
    const bb = p[p[X + 1]! + Y + 1]!;
    const x1 = lerp(grad(aa, xf), grad(ba, xf - 1), u);
    const x2 = lerp(grad(ab, xf), grad(bb, xf - 1), u);
    return lerp(x1, x2, v);
  };
}
const NOISE_A = makeNoise(7);
const NOISE_B = makeNoise(31);
const NOISE_C = makeNoise(53);

// ── Synthetic audio envelope (no mic — landing is presentational) ─────────
type AudioMode = "speak" | "think" | "listen" | null;
type AudioFrame = { level: number; bands: [number, number, number] };

const landingAudio = {
  mode: null as AudioMode,
  set(m: AudioMode) {
    this.mode = m;
  },
  read(now: number): AudioFrame {
    const t = now / 1000;
    if (this.mode === "speak") {
      const env =
        0.45 +
        0.35 * Math.sin(t * 6.3) * Math.sin(t * 2.1) +
        0.2 * Math.sin(t * 11) * Math.cos(t * 3.7);
      const burst =
        Math.max(0, Math.sin(t * 1.3 + Math.sin(t * 0.4) * 2)) * 0.3;
      const lvl = Math.min(1, Math.max(0.05, Math.abs(env) + burst));
      return {
        level: lvl,
        bands: [
          Math.max(0, Math.min(1, (0.5 + 0.4 * Math.sin(t * 3.2)) * lvl)),
          Math.max(0, Math.min(1, (0.5 + 0.4 * Math.sin(t * 5.7 + 1)) * lvl)),
          Math.max(0, Math.min(1, (0.5 + 0.4 * Math.sin(t * 9.1 + 2)) * lvl)),
        ],
      };
    }
    if (this.mode === "think") {
      const lvl = 0.15 + 0.1 * Math.sin(t * 1.2) + 0.05 * Math.sin(t * 0.4);
      return { level: lvl, bands: [lvl, lvl * 0.7, lvl * 0.4] };
    }
    if (this.mode === "listen") {
      const lvl = Math.max(
        0,
        0.35 + 0.25 * Math.sin(t * 2.1) + 0.15 * Math.sin(t * 5.3),
      );
      return { level: Math.min(1, lvl), bands: [lvl * 0.9, lvl, lvl * 0.7] };
    }
    return { level: 0, bands: [0, 0, 0] };
  },
};

interface OrbProps {
  size?: number;
  state?: OrbState;
  color?: string;
  expressiveness?: number;
}

export function Orb({
  size = 240,
  state = "idle",
  color = "#fff",
  expressiveness = 1.0,
}: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef<OrbState>(state);
  const smoothLevelRef = useRef(0);

  useEffect(() => {
    stateRef.current = state;
    if (state === "speaking") landingAudio.set("speak");
    else if (state === "thinking") landingAudio.set("think");
    else if (state === "listening") landingAudio.set("listen");
    else landingAudio.set(null);
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    let raf = 0;
    const start = performance.now();
    const POINTS = 96;

    const draw = (now: number) => {
      const t = (now - start) / 1000;
      const cx = size / 2;
      const cy = size / 2;
      const baseR = size * 0.34;
      ctx.clearRect(0, 0, size, size);

      const cur = stateRef.current;
      const exp = expressiveness;

      const a = landingAudio.read(now);
      smoothLevelRef.current += (a.level - smoothLevelRef.current) * 0.25;
      const level = smoothLevelRef.current;

      let breath = 0;
      let noiseAmp = 0;
      let noiseFreq = 1.2;
      let noiseSpeed = 0.4;
      let audioInfluence = 0;
      let lobes = 0;
      let lobeAmp = 0;
      let stroke = 4.0;
      let glow = 0.15;
      let extraRotate = 0;

      switch (cur) {
        case "idle":
          breath = Math.sin(t * 1.2) * 0.012;
          noiseAmp = 0.018 * exp;
          noiseFreq = 0.9;
          noiseSpeed = 0.3;
          stroke = 4.0;
          glow = 0.15 + Math.sin(t * 1.2) * 0.05;
          break;
        case "listening":
          breath = 0.04 + Math.sin(t * 2.4) * 0.015;
          noiseAmp = (0.03 + level * 0.18) * exp;
          noiseFreq = 1.6;
          noiseSpeed = 0.9 + level * 1.4;
          audioInfluence = level * 0.25;
          stroke = 4.4 + level * 1.2;
          glow = 0.3 + level * 0.5;
          break;
        case "thinking":
          breath = Math.sin(t * 0.7) * 0.025;
          noiseAmp = 0.025 * exp;
          noiseFreq = 0.7;
          noiseSpeed = 0.25;
          lobes = 3;
          lobeAmp = 0.04 + Math.sin(t * 0.9) * 0.02;
          extraRotate = t * 0.2;
          stroke = 4.2;
          glow = 0.2;
          break;
        case "speaking":
          breath = 0.02 + Math.sin(t * 3.1) * 0.01;
          noiseAmp = (0.04 + level * 0.22) * exp;
          noiseFreq = 1.3;
          noiseSpeed = 1.1 + level * 0.8;
          audioInfluence = level * 0.35;
          stroke = 4.6 + level * 1.5;
          glow = 0.4 + level * 0.6;
          break;
      }

      const r = baseR * (1 + breath);
      const px = cx;
      const py = cy;

      if (glow > 0) {
        const gr = r * (1 + 0.55 + glow * 0.4);
        const grad = ctx.createRadialGradient(px, py, r * 0.6, px, py, gr);
        grad.addColorStop(0, `rgba(255,255,255,${0.08 * glow})`);
        grad.addColorStop(0.5, `rgba(255,255,255,${0.04 * glow})`);
        grad.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(px, py, gr, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.beginPath();
      const ringPts: Array<[number, number]> = [];
      for (let i = 0; i <= POINTS; i++) {
        const ang = (i / POINTS) * Math.PI * 2 + extraRotate;
        const cosA = Math.cos(ang);
        const sinA = Math.sin(ang);
        let n = 0;
        n += NOISE_A(cosA * noiseFreq, t * noiseSpeed) * 1.0;
        n +=
          NOISE_B(
            cosA * noiseFreq * 2.1,
            sinA * noiseFreq * 2.1 + t * noiseSpeed * 1.3,
          ) * 0.5;
        n += NOISE_C(sinA * noiseFreq * 0.7, t * noiseSpeed * 0.8) * 0.4;
        n /= 1.9;

        let aMod = 0;
        if (audioInfluence > 0) {
          const band = (Math.sin(ang * 3 + t * 2) + 1) * 0.5;
          const bandIdx = Math.floor(band * 3) % 3;
          aMod = (a.bands[bandIdx]! - 0.4) * audioInfluence;
        }
        let lMod = 0;
        if (lobes > 0) lMod = Math.sin(ang * lobes + t * 1.4) * lobeAmp;

        const rr = r * (1 + n * noiseAmp + aMod + lMod);
        ringPts.push([px + cosA * rr, py + sinA * rr]);
      }

      ctx.moveTo(
        (ringPts[0]![0] + ringPts[1]![0]) / 2,
        (ringPts[0]![1] + ringPts[1]![1]) / 2,
      );
      for (let i = 1; i < ringPts.length - 1; i++) {
        const xc = (ringPts[i]![0] + ringPts[i + 1]![0]) / 2;
        const yc = (ringPts[i]![1] + ringPts[i + 1]![1]) / 2;
        ctx.quadraticCurveTo(ringPts[i]![0], ringPts[i]![1], xc, yc);
      }
      ctx.closePath();

      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.strokeStyle = color;
      ctx.lineWidth = stroke;
      ctx.shadowColor = color;
      ctx.shadowBlur = 8 + glow * 12;
      ctx.stroke();
      ctx.shadowBlur = 0;

      if (cur === "thinking") {
        const pulseR = 3 + Math.sin(t * 3) * 1.2;
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.6;
        ctx.beginPath();
        ctx.arc(px, py, pulseR, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size, color, expressiveness]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size, display: "block" }}
    />
  );
}

export type { OrbState };
