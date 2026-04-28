import { useEffect, useRef } from "react";
import { NOISE_A, NOISE_B, NOISE_C } from "../lib/noise.js";
import { AudioEngine } from "../lib/audioEngine.js";
import type { OrbState } from "../types.js";

// Singleton audio engine — drives the orb's visual animation only.
// Real audio I/O uses separate MicCapture / AudioPlayback instances.
export const orbAudioEngine = new AudioEngine();

interface OrbProps {
  state?: OrbState;
  size?: number;
  color?: string;
  expressiveness?: number;
  onPointerDown?: (e: React.PointerEvent) => void;
  onPointerUp?: (e: React.PointerEvent) => void;
  pressed?: boolean;
  // Called each frame when in "playing" state to get N frequency bands (0..1).
  // If absent the waveform bars animate with synthesized data.
  getPlaybackBands?: () => number[];
  // Returns true once any pre-roll earcon has drained from the audio buffer.
  // Bars stay hidden until this returns true (prevents earcon showing in the visualizer).
  isPrerollDone?: () => boolean;
}

export function Orb({
  state = "idle",
  size = 280,
  color = "#FFFFFF",
  expressiveness = 1.0,
  onPointerDown,
  onPointerUp,
  pressed = false,
  getPlaybackBands,
  isPrerollDone,
}: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<OrbState>(state);
  const expRef = useRef(expressiveness);
  const pressedRef = useRef(pressed);
  const smoothLevelRef = useRef(0);
  const smoothSpeakingLevelRef = useRef(0);
  const wakeStartRef = useRef<number | null>(null);
  const errorStartRef = useRef<number | null>(null);
  const pausedStartRef = useRef<number | null>(null);
  // "playing" morph: 0 = full ring, 1 = flat line + waveform bars
  const playingProgressRef = useRef(0);
  const playingTargetRef = useRef(0);
  const prevNowRef = useRef<number | null>(null);
  // Keep latest callbacks accessible inside the RAF loop
  const getPlaybackBandsRef = useRef<(() => number[]) | undefined>(undefined);
  const isPrerollDoneRef = useRef<(() => boolean) | undefined>(undefined);
  // Gate: bars hidden until pre-roll earcon drains from the audio buffer
  const barsEnabledRef = useRef(false);

  useEffect(() => {
    getPlaybackBandsRef.current = getPlaybackBands;
  }, [getPlaybackBands]);

  useEffect(() => {
    isPrerollDoneRef.current = isPrerollDone;
  }, [isPrerollDone]);

  useEffect(() => {
    stateRef.current = state;
    playingTargetRef.current = state === "playing" ? 1 : 0;
    if (state === "wake") wakeStartRef.current = performance.now();
    if (state === "error") errorStartRef.current = performance.now();
    if (state === "paused") pausedStartRef.current = performance.now();
    // Drive synth audio for visual animation
    if (state === "speaking") orbAudioEngine.startSynth("speak");
    else if (state === "thinking") orbAudioEngine.startSynth("think");
    else if (state === "listening") orbAudioEngine.startSynth("listen");
    // "live" and "playing" both drive the orb from real playback audio
    // via `getPlaybackBands` — peer voice (during a call) or content
    // stream (audiobook / radio). No synth needed.
    else orbAudioEngine.stopSynth();
    // Reset bars gate on every state transition; RAF loop re-enables when preroll drains
    barsEnabledRef.current = false;
  }, [state]);

  useEffect(() => {
    expRef.current = expressiveness;
  }, [expressiveness]);
  useEffect(() => {
    pressedRef.current = pressed;
  }, [pressed]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    let raf: number;
    const start = performance.now();
    const POINTS = 96;
    const PLAYING_LERP_SPEED = 2.8; // progress units per second (0→1 in ~360ms)

    const draw = (now: number) => {
      const t = (now - start) / 1000;
      const cx = size / 2;
      const cy = size / 2;
      const baseR = size * 0.34;

      // Lerp playing morph progress toward target
      const dtSec =
        prevNowRef.current !== null
          ? Math.min((now - prevNowRef.current) / 1000, 0.1)
          : 0;
      prevNowRef.current = now;
      const target = playingTargetRef.current;
      const cur0 = playingProgressRef.current;
      if (cur0 < target) {
        playingProgressRef.current = Math.min(
          target,
          cur0 + PLAYING_LERP_SPEED * dtSec,
        );
      } else if (cur0 > target) {
        playingProgressRef.current = Math.max(
          target,
          cur0 - PLAYING_LERP_SPEED * dtSec,
        );
      }
      const playingProgress = playingProgressRef.current;

      // Unlock bars once collapse is complete and the pre-roll earcon has drained
      if (
        !barsEnabledRef.current &&
        playingProgress >= 0.99 &&
        isPrerollDoneRef.current?.()
      ) {
        barsEnabledRef.current = true;
      }

      ctx.clearRect(0, 0, size, size);

      const cur = stateRef.current;
      const exp = expRef.current;

      const a = orbAudioEngine.read(now);
      smoothLevelRef.current += (a.level - smoothLevelRef.current) * 0.25;
      const level = smoothLevelRef.current;

      // Real playback FFT — drives speaking animation with actual voice data
      const activeBands = getPlaybackBandsRef.current?.() ?? [];
      const activeBandsAvg =
        activeBands.length > 0
          ? activeBands.reduce((s, v) => s + v, 0) / activeBands.length
          : 0;
      smoothSpeakingLevelRef.current +=
        (activeBandsAvg - smoothSpeakingLevelRef.current) * 0.3;
      const speakLevel = smoothSpeakingLevelRef.current;

      // State-specific motion parameters
      let breath = 0;
      let noiseAmp = 0;
      let noiseFreq = 1.2;
      let noiseSpeed = 0.4;
      let audioInfluence = 0;
      let lobes = 0;
      let lobeAmp = 0;
      let stroke = 2.4;
      let glow = 0;
      let extraRotate = 0;
      let collapseScale = 1;
      let stutterX = 0;
      let stutterY = 0;
      let infinity = 0;

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
          noiseAmp = (0.04 + speakLevel * 0.22) * exp;
          noiseFreq = 1.3;
          noiseSpeed = 1.1 + speakLevel * 0.8;
          audioInfluence = speakLevel * 0.35;
          stroke = 4.6 + speakLevel * 1.5;
          glow = 0.4 + speakLevel * 0.6;
          break;
        case "live": {
          // Continuous skill claim (active call) — reacts to the other
          // person's voice via `speakLevel` (real-playback FFT
          // analyser). Peer audio flows through the same master gain
          // as LLM audio so the analyser picks it up automatically.
          //
          // Telephony-grade audio (Telegram's Opus compression, your
          // phone mic's noise gate) is ~3-5x quieter at the analyser
          // than LLM TTS. Apply a non-linear boost so typical
          // speaking volume produces visibly-animated output without
          // making loud audio distort. `pow(x, 0.5) = sqrt` lifts
          // quiet-but-real values (0.1 → 0.32, 0.2 → 0.45) while
          // still capping near 1.0 for genuinely loud input.
          const liveLevel = Math.sqrt(Math.max(0, speakLevel));
          breath = Math.sin(t * 0.8) * 0.02;
          noiseAmp = (0.03 + liveLevel * 0.42) * exp;
          noiseFreq = 1.3;
          noiseSpeed = 0.9 + liveLevel * 2.0;
          audioInfluence = liveLevel * 0.55;
          stroke = 4.2 + liveLevel * 2.6;
          glow = 0.25 + Math.sin(t * 0.8) * 0.1 + liveLevel * 1.0;
          break;
        }
        case "playing":
          // Ring morphs toward a flat line (Y-collapse handled below).
          breath = 0;
          noiseAmp = 0;
          noiseFreq = 0.9;
          noiseSpeed = 0.3;
          stroke = 4.0;
          glow = 0.12 + playingProgress * 0.08;
          break;
        case "error": {
          const dt = (now - (errorStartRef.current ?? now)) / 1000;
          const decay = Math.exp(-dt * 1.2);
          stutterX = Math.sin(dt * 38) * 4 * decay;
          stutterY = Math.cos(dt * 31) * 3 * decay;
          breath = -0.02;
          noiseAmp = 0.05 + 0.04 * decay;
          noiseFreq = 2.2;
          noiseSpeed = 2.0;
          stroke = 4.0;
          glow = 0.15;
          break;
        }
        case "paused": {
          const dt = (now - (pausedStartRef.current ?? now)) / 1000;
          collapseScale = 1 - 0.18 * Math.min(1, dt * 1.5);
          breath = Math.sin(t * 0.6) * 0.005;
          noiseAmp = 0.01;
          noiseSpeed = 0.15;
          stroke = 3.2;
          glow = 0.08;
          break;
        }
        case "wake": {
          const dt = (now - (wakeStartRef.current ?? now)) / 1000;
          infinity = Math.max(0, 1 - Math.max(0, dt - 1.4) / 0.8);
          breath = 0;
          noiseAmp = 0.005;
          noiseSpeed = 0.5;
          stroke = 4.0;
          glow = 0.15 + 0.2 * Math.max(0, 1 - dt * 0.5);
          break;
        }
      }

      const r = baseR * (1 + breath) * collapseScale;
      const px = cx + stutterX;
      const py = cy + stutterY;

      // Outer glow
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

      // Build the organic ring
      ctx.beginPath();
      const ringPts: [number, number][] = [];
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
          // Real-playback FFT deforms the ring with actual voice
          // frequency content. Used by `speaking` (LLM audio) and
          // `live` (peer audio on an active call) — both flow
          // through the same playback analyser. `live` applies a
          // sqrt boost to compensate for quieter telephony-grade
          // peer audio; the bias subtraction also drops from 0.3 to
          // 0.15 because the quiet baseline is already closer to 0.
          if (
            (cur === "speaking" || cur === "live") &&
            activeBands.length > 0
          ) {
            const bandIdx =
              Math.floor(band * activeBands.length) % activeBands.length;
            const rawBand = activeBands[bandIdx] ?? 0;
            const band01 =
              cur === "live" ? Math.sqrt(Math.max(0, rawBand)) : rawBand;
            const bias = cur === "live" ? 0.15 : 0.3;
            aMod = (band01 - bias) * audioInfluence;
          } else {
            const bandIdx = Math.floor(band * 3) % 3;
            aMod = ((a.bands[bandIdx] ?? 0) - 0.4) * audioInfluence;
          }
        }

        let lMod = 0;
        if (lobes > 0) {
          lMod = Math.sin(ang * lobes + t * 1.4) * lobeAmp;
        }

        let rr = r * (1 + n * noiseAmp + aMod + lMod);

        if (infinity > 0) {
          const a2 = r * 1.3;
          const denom = 1 + sinA * sinA;
          const lx = (a2 * cosA) / denom;
          const ly = (a2 * sinA * cosA) / denom;
          const cxp = cosA * rr;
          const cyp = sinA * rr;
          ringPts.push([
            px + lx * infinity + cxp * (1 - infinity),
            py + ly * infinity + cyp * (1 - infinity),
          ]);
        } else {
          ringPts.push([px + cosA * rr, py + sinA * rr]);
        }
      }

      // "playing" morph: lerp every ring-point's Y toward the centre line.
      // At playingProgress=1 the ring is a flat horizontal line.
      if (playingProgress > 0.001) {
        for (let i = 0; i < ringPts.length; i++) {
          const pt = ringPts[i];
          if (pt) pt[1] = py + (pt[1] - py) * (1 - playingProgress);
        }
      }

      // Draw smooth path (Catmull-Rom-ish via quadratic mids)
      const p0 = ringPts[0];
      const p1 = ringPts[1];
      if (p0 && p1) {
        ctx.moveTo((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2);
      }
      for (let i = 1; i < ringPts.length - 1; i++) {
        const pi = ringPts[i];
        const pn = ringPts[i + 1];
        if (!pi || !pn) continue;
        const xc = (pi[0] + pn[0]) / 2;
        const yc = (pi[1] + pn[1]) / 2;
        ctx.quadraticCurveTo(pi[0], pi[1], xc, yc);
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

      // Waveform bars — only after full collapse AND earcon delay has passed
      if (playingProgress >= 0.99 && barsEnabledRef.current) {
        const bands = getPlaybackBandsRef.current?.() ?? [];
        const N = bands.length > 0 ? bands.length : 32;
        const totalW = r * 2 * 0.88;
        const slotW = totalW / N;
        const barW = Math.max(1, slotW * 0.55);
        const maxBarH = r * 0.82;
        const startX = px - totalW / 2;

        ctx.save();
        ctx.globalAlpha = 1.0;
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur = 4 + glow * 10;

        for (let i = 0; i < N; i++) {
          // Fallback synth when no real data: gentle noise-driven envelope
          const band =
            bands.length > 0
              ? (bands[i] ?? 0)
              : Math.max(
                  0,
                  0.15 +
                    0.5 *
                      Math.abs(
                        Math.sin(t * 3.2 + i * 0.55) *
                          Math.sin(t * 1.7 + i * 0.3),
                      ),
                );
          const bh = Math.max(2, band * maxBarH);
          const bx = startX + i * slotW + (slotW - barW) / 2;
          const radius = Math.min(barW / 2, bh / 2, 4);
          ctx.beginPath();
          ctx.roundRect(bx, py - bh, barW, bh * 2, radius);
          ctx.fill();
        }

        ctx.restore();
      }

      // Inner core dot for thinking
      if (cur === "thinking") {
        const pulseR = 3 + Math.sin(t * 3) * 1.2;
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.6;
        ctx.beginPath();
        ctx.arc(px, py, pulseR, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      // Pressed-state inner ring
      if (pressedRef.current && cur === "listening") {
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.18;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(px, py, r * 0.78, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size, color]);

  return (
    <div
      style={{
        width: size,
        height: size,
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        cursor: onPointerDown ? "pointer" : "default",
        userSelect: "none",
        WebkitUserSelect: "none",
        WebkitTouchCallout: "none",
        WebkitTapHighlightColor: "transparent",
        touchAction: "none",
      }}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <canvas
        ref={canvasRef}
        style={{ width: size, height: size, display: "block" }}
      />
    </div>
  );
}
