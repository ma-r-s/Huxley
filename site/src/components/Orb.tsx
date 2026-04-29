// Huxley landing orb — canonical product orb, ported from the design
// prototype's shared.jsx (which itself was copied from the product Huxley.html).
// Canvas 2D, 3-octave value-noise ring blended with a synthetic audio envelope.
// Lives at /src/components/Orb.tsx so the rest of the landing imports from one
// stable place. Behavior is identical to the live PWA's Orb in Huxley/web — we
// duplicate it here rather than share across repos because it's one canvas
// component and a sister-app extraction would be premature.

import { useEffect, useRef } from "react";

type OrbState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "gaze"
  | "slosh"
  | "spiky"
  | "mitosis";

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

// Canvas APIs (strokeStyle/fillStyle/shadowColor) don't resolve CSS variables —
// passing "var(--hux-fg)" silently falls back to black. Resolve it once here.
function resolveColor(input: string, ref: HTMLElement | null): string {
  if (!input.startsWith("var(") || !ref) return input;
  const name = input.slice(4, -1).trim();
  const computed = getComputedStyle(ref).getPropertyValue(name).trim();
  return computed || "#fff";
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
    const drawColor = resolveColor(color, canvas);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    let raf = 0;
    const start = performance.now();
    const POINTS = 96;

    // Per-point spring state — each ring point has its own current radius
    // multiplier and velocity. Spring forces pull toward the target each
    // frame; damping bleeds energy. Result: the surface ripples and lags
    // input, like a rubber band instead of a static clone of the noise
    // function.
    const springR = new Float32Array(POINTS + 1);
    const springV = new Float32Array(POINTS + 1);
    for (let i = 0; i <= POINTS; i++) springR[i] = 1;

    // Gaze vector — off-center "weight" that pulls the orb's mass toward a
    // moving point. Springs toward `gazeTarget`; bulge + center-translation
    // are derived from the resulting (gx, gy). When the active state isn't
    // a gaze/slosh state the target is (0,0), so the orb glides back home
    // instead of snapping.
    let gx = 0;
    let gy = 0;
    let gazeTargetX = 0;
    let gazeTargetY = 0;
    let lastSaccade = -1;
    let saccadeInterval = 1800;

    // State-change tracking. `stateEnterT` is captured the frame a new
    // state becomes active; used to phase-align mitosis's internal cycle
    // and to ramp spiky's amplitude in over a few hundred ms so the
    // crown emerges instead of popping.
    let stateEnterT = 0;
    let prevState: OrbState | null = null;

    // Trace a metaball iso-surface by ray-marching from a point inside
    // the field. Plain binary search assumes the field is monotonic along
    // each ray, which breaks down near the split critical point — a ray
    // from one cell toward the other crosses the boundary multiple times
    // (exit first cell, traverse low-field gap, enter second cell, exit
    // second cell). We instead walk outward in fixed steps to find the
    // FIRST inside→outside transition, then refine that bracket with
    // binary search.
    const traceMetaballRing = (
      ox: number,
      oy: number,
      field: (x: number, y: number) => number,
      maxR: number,
      n = POINTS,
    ): Array<[number, number]> => {
      const pts: Array<[number, number]> = [];
      const STEPS = 64;
      const stepR = maxR / STEPS;
      for (let i = 0; i <= n; i++) {
        const ang = (i / n) * Math.PI * 2;
        const dx = Math.cos(ang);
        const dy = Math.sin(ang);
        let prevR = 0;
        let prevInside = field(ox, oy) > 1;
        let lo = 0;
        let hi = maxR;
        let found = false;
        for (let s = 1; s <= STEPS; s++) {
          const r = s * stepR;
          const inside = field(ox + r * dx, oy + r * dy) > 1;
          if (prevInside && !inside) {
            lo = prevR;
            hi = r;
            found = true;
            break;
          }
          prevR = r;
          prevInside = inside;
        }
        if (!found) {
          // Origin was outside, or the surface never crossed within maxR.
          // Fall back to the origin so the path stays well-defined.
          pts.push([ox, oy]);
          continue;
        }
        for (let iter = 0; iter < 12; iter++) {
          const mid = (lo + hi) * 0.5;
          if (field(ox + mid * dx, oy + mid * dy) > 1) lo = mid;
          else hi = mid;
        }
        const rr = (lo + hi) * 0.5;
        pts.push([ox + rr * dx, oy + rr * dy]);
      }
      return pts;
    };

    const strokeRing = (
      ctx2: CanvasRenderingContext2D,
      pts: Array<[number, number]>,
    ) => {
      ctx2.beginPath();
      ctx2.moveTo((pts[0]![0] + pts[1]![0]) / 2, (pts[0]![1] + pts[1]![1]) / 2);
      for (let i = 1; i < pts.length - 1; i++) {
        const xc = (pts[i]![0] + pts[i + 1]![0]) / 2;
        const yc = (pts[i]![1] + pts[i + 1]![1]) / 2;
        ctx2.quadraticCurveTo(pts[i]![0], pts[i]![1], xc, yc);
      }
      ctx2.closePath();
      ctx2.stroke();
    };

    const drawBlobGlow = (
      x: number,
      y: number,
      r: number,
      intensity: number,
    ) => {
      const gr = r * (1 + 0.55 + intensity * 0.4);
      const grad = ctx.createRadialGradient(x, y, r * 0.6, x, y, gr);
      grad.addColorStop(0, `rgba(255,255,255,${0.08 * intensity})`);
      grad.addColorStop(0.5, `rgba(255,255,255,${0.04 * intensity})`);
      grad.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(x, y, gr, 0, Math.PI * 2);
      ctx.fill();
    };

    const draw = (now: number) => {
      const t = (now - start) / 1000;
      const cx = size / 2;
      const cy = size / 2;
      const baseR = size * 0.34;
      ctx.clearRect(0, 0, size, size);

      const cur = stateRef.current;
      if (cur !== prevState) {
        stateEnterT = t;
        prevState = cur;
      }
      const stateAge = t - stateEnterT;
      const exp = expressiveness;

      const a = landingAudio.read(now);
      smoothLevelRef.current += (a.level - smoothLevelRef.current) * 0.25;
      const level = smoothLevelRef.current;

      // ── Mitosis ────────────────────────────────────────────────────
      // The orb starts as one, splits diagonally into two smaller blobs
      // (top-right = caller, bottom-left = called), each pulsing on its
      // own beat with synthetic voice activity (alternating speakers),
      // then merges back. Rendering uses a 2-circle metaball field so
      // the moment of split looks like a continuous separation rather
      // than a hard pop.
      if (cur === "mitosis") {
        // Drift per-point springs toward neutral so when the demo exits
        // mitosis the next per-point state starts with clean springs.
        for (let i = 0; i <= POINTS; i++) {
          springR[i]! += (1 - springR[i]!) * 0.1;
          springV[i]! *= 0.85;
        }
        // Cycle: 0–1.6s split, 1.6–8s talking, 8–9.6s merge, 9.6–10s held single.
        const cycle = (stateAge * 0.1) % 1;
        let raw: number;
        if (cycle < 0.16) raw = cycle / 0.16;
        else if (cycle < 0.8) raw = 1;
        else if (cycle < 0.96) raw = 1 - (cycle - 0.8) / 0.16;
        else raw = 0;
        // smoothstep for nicer easing on the split/merge.
        const split = raw * raw * (3 - 2 * raw);

        // Synthetic alternating-speaker activity. `speakerCycle` swings
        // between caller and called every ~7s. Each "voice" pulses fast
        // when active and decays toward 0 when idle. Voice modulation
        // is scaled by `split` so at the merged moments (split=0) the
        // blob has no extra pulsing — important for clean transitions.
        const speakerCycle = Math.sin(stateAge * 0.45);
        const aSpeaking = Math.max(0, speakerCycle);
        const bSpeaking = Math.max(0, -speakerCycle);
        const aLevel =
          split *
          aSpeaking *
          (0.45 +
            0.55 *
              Math.abs(Math.sin(stateAge * 7.3) * Math.cos(stateAge * 2.1)));
        const bLevel =
          split *
          bSpeaking *
          (0.45 +
            0.55 *
              Math.abs(
                Math.sin(stateAge * 6.1 + 1) * Math.cos(stateAge * 1.7),
              ));

        // Blob centres along the top-right ↔ bottom-left diagonal.
        const offset = baseR * 0.95 * split;
        const cAx = cx + offset * 0.707;
        const cAy = cy - offset * 0.707;
        const cBx = cx - offset * 0.707;
        const cBy = cy + offset * 0.707;

        // Per-cell radius. Sized so that at split=0 (cells coincident)
        // the metaball iso-surface evaluates to baseR — matching the
        // regular orb's size, so entering/exiting mitosis is a smooth
        // transition. Math: for two coincident equal circles of radius r
        // at threshold 1, the iso-surface is at distance r·√2. Setting
        // r = baseR/√2 ≈ 0.707 makes the visible blob exactly baseR.
        // As split grows, radius shrinks toward ~0.5·baseR for two
        // distinct smaller blobs.
        const rBase = baseR * (0.707 - 0.207 * split);
        const rA = rBase * (1 + aLevel * 0.18);
        const rB = rBase * (1 + bLevel * 0.18);

        // Metaball field: sum of inverse-square-distance contributions
        // from the two cells. Iso-surface at field = 1.
        const field = (px2: number, py2: number) => {
          const dA = (px2 - cAx) ** 2 + (py2 - cAy) ** 2 + 1;
          const dB = (px2 - cBx) ** 2 + (py2 - cBy) ** 2 + 1;
          return (rA * rA) / dA + (rB * rB) / dB;
        };

        // If the geometric midpoint is still inside the field, the two
        // blobs are connected — trace one path around both. Otherwise
        // they've separated; trace each blob independently from its own
        // centre.
        const connected = field(cx, cy) > 1;

        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.strokeStyle = drawColor;
        ctx.lineWidth = 4.0;

        if (connected) {
          drawBlobGlow(cx, cy, rBase, 0.18 + (aLevel + bLevel) * 0.15);
          ctx.shadowColor = drawColor;
          ctx.shadowBlur = 10;
          // Use a higher sample density so the pinched neck near the
          // split moment renders as a smooth curve instead of a polygon.
          const ring = traceMetaballRing(cx, cy, field, baseR * 3, 192);
          strokeRing(ctx, ring);
          ctx.shadowBlur = 0;
        } else {
          drawBlobGlow(cAx, cAy, rA, 0.2 + aLevel * 0.3);
          drawBlobGlow(cBx, cBy, rB, 0.2 + bLevel * 0.3);
          ctx.shadowColor = drawColor;
          ctx.shadowBlur = 8;
          const ringA = traceMetaballRing(cAx, cAy, field, baseR * 1.4, 144);
          const ringB = traceMetaballRing(cBx, cBy, field, baseR * 1.4, 144);
          strokeRing(ctx, ringA);
          strokeRing(ctx, ringB);
          ctx.shadowBlur = 0;
        }

        raf = requestAnimationFrame(draw);
        return;
      }

      // Per-state animation parameters
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
      // Traveling waves around the perimeter — mostly for speaking. Two
      // wavefronts at different periods so the ring pulses with direction
      // instead of static lumps.
      let waveAmp = 0;
      let waveSpeed = 0;
      // Asymmetric squash & stretch — the silhouette itself oscillates
      // (wider one moment, taller the next) instead of every point breathing
      // uniformly. Applied as separate x/y scale factors after radius.
      let squashAmp = 0;
      // Spring physics tuning. Lower stiffness = floaty, more wobble after
      // input changes. Lower damping = oscillates longer. Idle is held;
      // speaking is bouncy.
      let springK = 0.16;
      let springD = 0.84;
      // Weight-shift parameters (limaçon model). The orb's radius is
      // modulated as r(θ) = R · (1 + e·cos(θ − θ_gaze)), which is the
      // simplest mathematical asymmetric closed curve — fat on the
      // gaze-aligned side, smoothly thinning to the opposite side, no
      // discontinuities. `bulgeAmp` is the eccentricity e at gMag=1.
      // `translateAmp` shifts the orb's center toward the gaze. `useGaze`
      // gates whether this state drives the gaze target at all.
      let bulgeAmp = 0;
      let translateAmp = 0;
      let gazeSpring = 0.06;
      let useGaze = false;
      let gazeIsSaccade = false;
      let wanderSpeed = 0.3;
      // Spike parameters. `spikeCount` is the number of peaks around the
      // perimeter; `spikeAmp` is each peak's max radius gain; `spikePower`
      // sharpens them (higher = narrower, more dagger-like); `spikeRot`
      // rotates the whole crown over time; `spikeWobble` modulates the
      // peak heights so the spikes pulse in and out instead of holding
      // a static silhouette.
      let spikeCount = 0;
      let spikeAmp = 0;
      let spikePower = 1;
      let spikeRot = 0;
      let spikeWobble = 0;

      switch (cur) {
        case "idle":
          breath = Math.sin(t * 1.2) * 0.012;
          noiseAmp = 0.018 * exp;
          noiseFreq = 0.9;
          noiseSpeed = 0.3;
          stroke = 4.0;
          glow = 0.15 + Math.sin(t * 1.2) * 0.05;
          squashAmp = 0.014;
          springK = 0.09;
          springD = 0.92;
          break;
        case "listening":
          breath = 0.04 + Math.sin(t * 2.4) * 0.015;
          noiseAmp = (0.03 + level * 0.18) * exp;
          noiseFreq = 1.6;
          noiseSpeed = 0.9 + level * 1.4;
          audioInfluence = level * 0.28;
          stroke = 4.4 + level * 1.2;
          glow = 0.3 + level * 0.5;
          waveAmp = 0.012 + level * 0.04;
          waveSpeed = 1.5 + level * 1.6;
          squashAmp = 0.018 + level * 0.02;
          springK = 0.18;
          springD = 0.8;
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
          squashAmp = 0.022;
          springK = 0.1;
          springD = 0.88;
          break;
        case "speaking":
          breath = 0.02 + Math.sin(t * 3.1) * 0.01;
          noiseAmp = (0.04 + level * 0.22) * exp;
          noiseFreq = 1.3;
          noiseSpeed = 1.1 + level * 0.8;
          audioInfluence = level * 0.4;
          stroke = 4.6 + level * 1.5;
          glow = 0.4 + level * 0.6;
          waveAmp = 0.02 + level * 0.07;
          waveSpeed = 2.5 + level * 2.0;
          squashAmp = 0.024 + level * 0.03;
          springK = 0.22;
          springD = 0.78;
          break;
        // Eyeball — focused bulge that snaps to a new target every couple
        // seconds. Tight spring + sharp falloff so it reads as "looking
        // somewhere," not "drifting."
        case "gaze":
          breath = Math.sin(t * 1.0) * 0.01;
          noiseAmp = 0.01 * exp;
          noiseFreq = 0.9;
          noiseSpeed = 0.3;
          stroke = 4.2;
          glow = 0.22;
          squashAmp = 0.012;
          springK = 0.18;
          springD = 0.82;
          useGaze = true;
          gazeIsSaccade = true;
          gazeSpring = 0.14;
          bulgeAmp = 0.55;
          // No translation — the orb stays anchored at the canvas center.
          // The limaçon's asymmetry creates the "pulled to one side"
          // illusion while the trailing edge keeps a visual tether to
          // where the center was.
          translateAmp = 0;
          break;
        // Water in a bowl — slow continuous wander, soft wide bulge, big
        // center translation that lags behind the target. The asymmetric
        // squash also opens up so the silhouette deforms like liquid.
        // Spiky — sharp crown of peaks slowly rotating, each peak pulsing
        // in and out so the silhouette never feels static. Sharpness comes
        // from raising max(0, cos) to a high power, which gives narrow
        // peaks and wide quiet valleys.
        case "spiky": {
          // Ramp spike amplitude in over ~400ms after entry and out over
          // the final ~400ms before exit, so the crown emerges/retracts
          // instead of popping. Total dwell hardcoded to match the demo
          // cycle in useOrbDemoState (`spiky: 3600`).
          const SPIKY_DUR = 3.6;
          const rampIn = Math.min(1, stateAge / 0.4);
          const rampOut = Math.max(
            0,
            Math.min(1, (SPIKY_DUR - stateAge) / 0.4),
          );
          const ramp = Math.min(rampIn, rampOut);
          breath = Math.sin(t * 1.3) * 0.018;
          noiseAmp = 0.012 * exp;
          noiseFreq = 0.7;
          noiseSpeed = 0.5;
          stroke = 3.6;
          glow = 0.22 + Math.sin(t * 1.4) * 0.06;
          squashAmp = 0.012;
          springK = 0.22;
          springD = 0.8;
          spikeCount = 14;
          spikeAmp = 0.32 * ramp;
          spikePower = 4;
          spikeRot = t * 0.45;
          spikeWobble = 0.4 + Math.sin(t * 1.7) * 0.25;
          break;
        }
        case "slosh":
          breath = Math.sin(t * 0.8) * 0.018;
          noiseAmp = 0.022 * exp;
          noiseFreq = 0.8;
          noiseSpeed = 0.4;
          stroke = 4.2;
          glow = 0.22;
          squashAmp = 0.028;
          springK = 0.06;
          springD = 0.94;
          useGaze = true;
          gazeIsSaccade = false;
          gazeSpring = 0.025;
          bulgeAmp = 0.32;
          translateAmp = 0.18;
          wanderSpeed = 0.35;
          break;
      }

      // ── Gaze target update ───────────────────────────────────────────
      // `gaze` mode jumps to a new random direction at a randomized
      // interval (real eyes do saccades). `slosh` traces a smooth
      // lissajous-ish wander. Anything else targets (0,0) so the spring
      // pulls the bulge back to centered.
      if (useGaze && gazeIsSaccade) {
        if (lastSaccade < 0 || now - lastSaccade > saccadeInterval) {
          const angle = Math.random() * Math.PI * 2;
          const dist = 0.55 + Math.random() * 0.4;
          gazeTargetX = Math.cos(angle) * dist;
          gazeTargetY = Math.sin(angle) * dist;
          lastSaccade = now;
          saccadeInterval = 1700 + Math.random() * 1500;
        }
      } else if (useGaze) {
        gazeTargetX =
          Math.sin(t * wanderSpeed) * 0.7 +
          Math.sin(t * wanderSpeed * 1.7 + 0.5) * 0.3;
        gazeTargetY =
          Math.cos(t * wanderSpeed * 0.9 + 1) * 0.65 +
          Math.sin(t * wanderSpeed * 2.1) * 0.3;
      } else {
        gazeTargetX = 0;
        gazeTargetY = 0;
      }
      gx += (gazeTargetX - gx) * gazeSpring;
      gy += (gazeTargetY - gy) * gazeSpring;

      const r = baseR * (1 + breath);
      // Center shifted toward the gaze direction. When gaze is at rest
      // (gx=gy=0) this collapses to (cx, cy), so non-gaze states are
      // unchanged.
      const px = cx + gx * baseR * translateAmp;
      const py = cy + gy * baseR * translateAmp;
      const gMag = Math.hypot(gx, gy);
      const gnx = gMag > 1e-4 ? gx / gMag : 0;
      const gny = gMag > 1e-4 ? gy / gMag : 0;

      // Asymmetric squash factors. Two oscillators per axis at different
      // periods so the silhouette never repeats the same shape, and the
      // x/y axes are out of phase so it reads as a soft squeeze rather
      // than a uniform pulse.
      const sx =
        1 +
        Math.sin(t * 0.71) * squashAmp +
        Math.sin(t * 0.43 + 1.2) * squashAmp * 0.5;
      const sy =
        1 +
        Math.cos(t * 0.61 + 0.8) * squashAmp +
        Math.sin(t * 0.39 + 2.1) * squashAmp * 0.4;

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

      if (cur === "gaze") {
        // Drift per-point springs toward neutral so transitions out of
        // gaze start with clean springs.
        for (let i = 0; i <= POINTS; i++) {
          springR[i]! += (1 - springR[i]!) * 0.1;
          springV[i]! *= 0.85;
        }
        // Convex hull of two circles: a small static "anchor" centered at
        // the canvas center plus a bigger "head" circle that the gaze pulls
        // around. The silhouette is the outer envelope (two external tangent
        // lines + back arc of the small circle + front arc of the big one).
        // Both radii are gMag-dependent so at gMag=0 the hull collapses to
        // a single circle of size baseR — matches the regular orb so the
        // entry into gaze is a smooth deformation, not a size pop.
        const r1 = r * (0.45 + 0.27 * gMag); // 0.45→0.72 as gaze engages
        const r2 = r * (1 - 0.15 * gMag); // 1.0→0.85 as gaze engages
        // Both circles move along the gaze axis in opposite directions so
        // the silhouette's centroid stays at the canvas center. The head
        // (C2) leads further; the anchor (C1) trails by less, like a
        // counterbalance.
        const headOffset = r * 0.42 * gMag;
        const anchorOffset = r * 0.22 * gMag;
        const c1x = cx - gnx * anchorOffset;
        const c1y = cy - gny * anchorOffset;
        const c2x = cx + gnx * headOffset;
        const c2y = cy + gny * headOffset;
        const D = headOffset + anchorOffset;

        if (D < r2 - r1 + 1e-3) {
          // Degenerate: anchor is fully inside the head circle. Just
          // draw the head circle with the same multi-octave noise the
          // tangent path uses, so the entry into gaze is continuous.
          for (let i = 0; i <= POINTS; i++) {
            const a2 = (i / POINTS) * Math.PI * 2;
            const cA2 = Math.cos(a2);
            const sA2 = Math.sin(a2);
            let nv = 0;
            nv += NOISE_A(cA2 * 0.9, t * 0.3) * 1.0;
            nv += NOISE_B(cA2 * 1.9, sA2 * 1.9 + t * 0.4) * 0.5;
            nv += NOISE_C(sA2 * 0.6, t * 0.24) * 0.4;
            nv /= 1.9;
            const rN = r2 * (1 + nv * 0.02);
            ringPts.push([
              cx + cA2 * rN * sx + gnx * headOffset,
              cy + sA2 * rN * sy + gny * headOffset,
            ]);
          }
        } else {
          // External tangent angle (relative to the C1→C2 axis). cos α =
          // (r1 − r2)/D < 0 since r1 < r2, so α ∈ (π/2, π) — tangent points
          // sit on the back side of each circle.
          const cosA = (r1 - r2) / D;
          const sinA = Math.sqrt(Math.max(0, 1 - cosA * cosA));
          const alpha = Math.acos(cosA);
          // Perpendicular to (gnx, gny), CCW 90°.
          const vx = -gny;
          const vy = gnx;
          // Upper / lower tangent direction unit vectors (in global frame).
          const tdx = cosA * gnx + sinA * vx;
          const tdy = cosA * gny + sinA * vy;
          const bdx = cosA * gnx - sinA * vx;
          const bdy = cosA * gny - sinA * vy;
          // Tangent points on each circle.
          const P1ux = c1x + r1 * tdx;
          const P1uy = c1y + r1 * tdy;
          const P1lx = c1x + r1 * bdx;
          const P1ly = c1y + r1 * bdy;
          const P2ux = c2x + r2 * tdx;
          const P2uy = c2y + r2 * tdy;
          const P2lx = c2x + r2 * bdx;
          const P2ly = c2y + r2 * bdy;
          // Outline segment lengths (perimeter parameterisation).
          const arcC2Len = r2 * 2 * alpha;
          const arcC1Len = r1 * (2 * Math.PI - 2 * alpha);
          const tanLen = Math.sqrt(D * D - (r2 - r1) * (r2 - r1));
          const totalP = arcC2Len + arcC1Len + 2 * tanLen;

          for (let i = 0; i <= POINTS; i++) {
            const s = (i / POINTS) * totalP;
            let xx = 0;
            let yy = 0;
            if (s < arcC2Len) {
              // Front arc of the big circle: local angle α → −α through 0.
              const f = s / arcC2Len;
              const la = alpha - f * 2 * alpha;
              const lc = Math.cos(la);
              const ls = Math.sin(la);
              xx = c2x + r2 * (lc * gnx + ls * vx);
              yy = c2y + r2 * (lc * gny + ls * vy);
            } else if (s < arcC2Len + tanLen) {
              // Lower tangent line: P2_lower → P1_lower.
              const f = (s - arcC2Len) / tanLen;
              xx = P2lx + f * (P1lx - P2lx);
              yy = P2ly + f * (P1ly - P2ly);
            } else if (s < arcC2Len + tanLen + arcC1Len) {
              // Back arc of the small circle: local angle −α → α through π.
              const f = (s - arcC2Len - tanLen) / arcC1Len;
              const la = -alpha - f * (2 * Math.PI - 2 * alpha);
              const lc = Math.cos(la);
              const ls = Math.sin(la);
              xx = c1x + r1 * (lc * gnx + ls * vx);
              yy = c1y + r1 * (lc * gny + ls * vy);
            } else {
              // Upper tangent line: P1_upper → P2_upper.
              const f = (s - arcC2Len - tanLen - arcC1Len) / tanLen;
              xx = P1ux + f * (P2ux - P1ux);
              yy = P1uy + f * (P2uy - P1uy);
            }
            // Multi-octave noise displacement (radial). Without this the
            // convex-hull silhouette is mathematically perfect and reads
            // as "rigid" next to the noisy other states. Sampled by angle
            // around the canvas center so it stays consistent across the
            // ring as samples migrate around the perimeter.
            const ndx = xx - cx;
            const ndy = yy - cy;
            const nDist = Math.hypot(ndx, ndy) || 1;
            const nnx = ndx / nDist;
            const nny = ndy / nDist;
            const nAng = Math.atan2(ndy, ndx);
            const nC = Math.cos(nAng);
            const nS = Math.sin(nAng);
            let nv = 0;
            nv += NOISE_A(nC * 0.9, t * 0.3) * 1.0;
            nv += NOISE_B(nC * 1.9, nS * 1.9 + t * 0.4) * 0.5;
            nv += NOISE_C(nS * 0.6, t * 0.24) * 0.4;
            nv /= 1.9;
            const nOff = nv * 0.02 * baseR;
            const xn = xx + nnx * nOff;
            const yn = yy + nny * nOff;
            // Asymmetric squash applied around the canvas center.
            ringPts.push([cx + (xn - cx) * sx, cy + (yn - cy) * sy]);
          }
        }
      } else {
        for (let i = 0; i <= POINTS; i++) {
          const ang = (i / POINTS) * Math.PI * 2 + extraRotate;
          const cosA = Math.cos(ang);
          const sinA = Math.sin(ang);

          // Multi-octave noise — same as before, gives organic surface texture.
          let n = 0;
          n += NOISE_A(cosA * noiseFreq, t * noiseSpeed) * 1.0;
          n +=
            NOISE_B(
              cosA * noiseFreq * 2.1,
              sinA * noiseFreq * 2.1 + t * noiseSpeed * 1.3,
            ) * 0.5;
          n += NOISE_C(sinA * noiseFreq * 0.7, t * noiseSpeed * 0.8) * 0.4;
          n /= 1.9;

          // Audio-band modulation — smooth interpolation across 3 bands
          // instead of snapping to one (the old `Math.floor(band * 3) % 3`
          // produced visible step artifacts). Now reads as a continuous
          // wave sweeping around the ring.
          let aMod = 0;
          if (audioInfluence > 0) {
            const band = (Math.sin(ang * 3 + t * 2) + 1) * 0.5; // 0..1
            const bf = band * 3; // 0..3
            const i0 = Math.floor(bf) % 3;
            const i1 = (i0 + 1) % 3;
            const f = bf - Math.floor(bf);
            const bandVal = a.bands[i0]! * (1 - f) + a.bands[i1]! * f;
            aMod = (bandVal - 0.4) * audioInfluence;
          }

          // Traveling waves — two wavefronts at different speeds.
          let waveMod = 0;
          if (waveAmp > 0) {
            waveMod += Math.sin(ang * 2 - t * waveSpeed) * waveAmp;
            waveMod +=
              Math.sin(ang * 5 - t * waveSpeed * 1.6 + 1.3) * waveAmp * 0.45;
          }

          // Slow contemplative lobes (thinking only).
          let lMod = 0;
          if (lobes > 0) lMod = Math.sin(ang * lobes + t * 1.4) * lobeAmp;

          // Target radius multiplier (pre-spring).
          const target = 1 + n * noiseAmp + aMod + waveMod + lMod;

          // Spring step per point — vel += force × stiffness, vel *= damping.
          const cur1 = springR[i]!;
          const vel = springV[i]!;
          const force = (target - cur1) * springK;
          const newVel = (vel + force) * springD;
          springV[i] = newVel;
          const next = cur1 + newVel;
          springR[i] = next;

          // Limaçon-style mass shift (used by `slosh` and any non-gaze
          // state with bulgeAmp > 0). Single smooth sinusoidal modulation.
          let bulgeMod = 0;
          if (bulgeAmp > 0 && gMag > 1e-4) {
            const dot = cosA * gnx + sinA * gny;
            bulgeMod = dot * bulgeAmp * gMag;
          }

          // Spike modulation — narrow peaks at every 2π/spikeCount around
          // the ring. cos(spikeCount·θ + spikeRot) crests at each peak;
          // raising max(0, ·) to spikePower sharpens it to a dagger.
          // spikeWobble pulses the peaks' max height so the crown breathes.
          let spikeMod = 0;
          if (spikeAmp > 0) {
            const wave = Math.cos(spikeCount * ang + spikeRot);
            const peak = Math.pow(Math.max(0, wave), spikePower);
            spikeMod = peak * spikeAmp * (0.7 + spikeWobble);
          }

          const finalMul = Math.max(0.2, next + bulgeMod + spikeMod);
          const rr = r * finalMul;
          ringPts.push([px + cosA * rr * sx, py + sinA * rr * sy]);
        }
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
      ctx.strokeStyle = drawColor;
      ctx.lineWidth = stroke;
      ctx.shadowColor = drawColor;
      ctx.shadowBlur = 8 + glow * 12;
      ctx.stroke();
      ctx.shadowBlur = 0;

      if (cur === "thinking") {
        const pulseR = 3 + Math.sin(t * 3) * 1.2;
        ctx.fillStyle = drawColor;
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
