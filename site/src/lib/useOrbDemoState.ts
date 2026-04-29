// Cycles the orb through its expressive states on a timer so the hero
// shows off the full repertoire instead of sitting in idle. Ported from
// the design prototype's `useOrbDemoState` (shared.jsx) — the timings
// here match what the original artboard used.

import { useEffect, useState } from "react";
import type { OrbState } from "../components/Orb.js";

interface Timings {
  idle: number;
  listening: number;
  thinking: number;
  speaking: number;
}

const DEFAULT: Timings = {
  idle: 2200,
  listening: 3000,
  thinking: 2000,
  speaking: 3400,
};

export function useOrbDemoState(overrides: Partial<Timings> = {}): OrbState {
  const [state, setState] = useState<OrbState>("idle");
  useEffect(() => {
    const t = { ...DEFAULT, ...overrides };
    const seq: OrbState[] = ["idle", "listening", "thinking", "speaking"];
    let cancelled = false;
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const step = () => {
      if (cancelled) return;
      const name = seq[i % seq.length]!;
      setState(name);
      timer = setTimeout(step, t[name]);
      i += 1;
    };
    step();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // overrides intentionally not in deps — timing changes after mount
    // would restart the cycle, which is jarring. Update the constant
    // instead if you want different defaults.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return state;
}
