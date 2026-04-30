"use client";
// Cycles the hero orb through every expressive state on a timer so the
// landing showcases its full repertoire — the four core voice states
// (idle / listening / thinking / speaking) plus the expressive ones
// (gaze / slosh / spiky / mitosis).

import { useEffect, useState } from "react";
import type { OrbState } from "../components/Orb";

// Per-state dwell times (ms). Mitosis runs one full internal split-talk-
// merge cycle (~10s) so the demo exits at the merged moment and the next
// state can take over without a visual jump.
const TIMINGS: Record<OrbState, number> = {
  idle: 2200,
  listening: 3000,
  thinking: 2200,
  speaking: 3400,
  gaze: 5000,
  slosh: 4200,
  spiky: 3600,
  mitosis: 10000,
};

const SEQ: readonly OrbState[] = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "gaze",
  "slosh",
  "spiky",
  "mitosis",
];

export function useOrbDemoState(): OrbState {
  const [state, setState] = useState<OrbState>("idle");
  useEffect(() => {
    let cancelled = false;
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const step = () => {
      if (cancelled) return;
      const name = SEQ[i % SEQ.length]!;
      setState(name);
      timer = setTimeout(step, TIMINGS[name]);
      i += 1;
    };
    step();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);
  return state;
}
