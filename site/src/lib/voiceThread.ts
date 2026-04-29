// Module-level pub/sub store + IntersectionObserver hooks that drive the
// page-wide voice-thread interactivity. Each section registers itself with a
// semantic state (idle | listening | thinking | speaking | interrupt). The
// most-visible section's state becomes the active state, and the VoiceThread
// canvas + hero orb both subscribe to it.
//
// Ported verbatim from the prototype's voice-thread.jsx, narrowed to TS.

import { useEffect, useRef, useState } from "react";

export type VoiceState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupt";

interface SectionRecord {
  state: VoiceState;
  el: HTMLElement;
  ratio: number;
  order: number;
}

interface ActiveSnapshot {
  id: string;
  state: VoiceState;
  scrollProgress: number;
}

const VoiceStore = (() => {
  const sections = new Map<string, SectionRecord>();
  const subs = new Set<() => void>();
  let active: { id: string; state: VoiceState } = { id: "hero", state: "idle" };
  let scrollProgress = 0;
  let orderSeq = 0;

  const notify = () => {
    for (const fn of subs) fn();
  };

  const recompute = () => {
    let bestId: string | null = null;
    let bestRatio = -1;
    for (const [id, rec] of sections) {
      const r = rec.ratio ?? 0;
      if (r > bestRatio) {
        bestRatio = r;
        bestId = id;
      }
    }
    if (bestId && bestId !== active.id) {
      active = { id: bestId, state: sections.get(bestId)!.state };
      notify();
    }
  };

  return {
    add(id: string, state: VoiceState, el: HTMLElement) {
      sections.set(id, { state, el, ratio: 0, order: orderSeq++ });
    },
    remove(id: string) {
      sections.delete(id);
    },
    setRatio(id: string, ratio: number) {
      const rec = sections.get(id);
      if (!rec) return;
      rec.ratio = ratio;
      recompute();
    },
    // Update a section's voice state in place. Used by the hero orb cycler
    // so the sticky waveform bar reflects the current orb state instead of
    // staying frozen on the section's static "idle" registration.
    setSectionState(id: string, state: VoiceState) {
      const rec = sections.get(id);
      if (!rec) return;
      rec.state = state;
      if (active.id === id && active.state !== state) {
        active = { id, state };
        notify();
      }
    },
    setScrollProgress(p: number) {
      scrollProgress = p;
      notify();
    },
    get(): ActiveSnapshot {
      return { ...active, scrollProgress };
    },
    subscribe(fn: () => void) {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
})();

export function useVoiceState(): ActiveSnapshot {
  const [snap, setSnap] = useState(() => VoiceStore.get());
  useEffect(() => VoiceStore.subscribe(() => setSnap(VoiceStore.get())), []);
  return snap;
}

// Imperative setter — call from a section that wants to override its
// registered state at runtime (e.g. the hero cycling its orb through
// every state on a timer).
export function setSectionVoiceState(id: string, state: VoiceState): void {
  VoiceStore.setSectionState(id, state);
}

// Find the nearest scrollable ancestor — used as the IntersectionObserver
// root so registration works even if the landing is mounted inside another
// scroller (design canvas, future portal mount, etc.).
function findScrollAncestor(el: HTMLElement): HTMLElement | null {
  let scroller: HTMLElement | null = el.parentElement;
  while (scroller && scroller !== document.body) {
    const s = getComputedStyle(scroller);
    if (
      /(auto|scroll)/.test(s.overflowY) &&
      scroller.scrollHeight > scroller.clientHeight + 1
    ) {
      return scroller;
    }
    scroller = scroller.parentElement;
  }
  return null;
}

// Each section uses this to register its element + voice state.
export function useRegisterSection<T extends HTMLElement = HTMLElement>(
  id: string,
  voiceState: VoiceState,
) {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    VoiceStore.add(id, voiceState, el);

    const root = findScrollAncestor(el);

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) VoiceStore.setRatio(id, e.intersectionRatio);
      },
      { root, threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] },
    );
    io.observe(el);

    const target: HTMLElement | Window = root ?? window;
    const onScroll = () => {
      const isWin = !root;
      const sTop = isWin ? window.scrollY || 0 : root!.scrollTop;
      const vh = isWin ? window.innerHeight : root!.clientHeight;
      const sh = isWin
        ? document.documentElement.scrollHeight
        : root!.scrollHeight;
      const max = Math.max(1, sh - vh);
      VoiceStore.setScrollProgress(Math.max(0, Math.min(1, sTop / max)));
    };
    target.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    return () => {
      io.disconnect();
      target.removeEventListener("scroll", onScroll);
      VoiceStore.remove(id);
    };
  }, [id, voiceState]);
  return ref;
}

// In-view hook used by reveal primitives below + by sections that want to
// trigger an animation once they enter view.
export function useInView<T extends HTMLElement = HTMLElement>(
  threshold = 0.15,
): [React.MutableRefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const root = findScrollAncestor(el);
    const io = new IntersectionObserver(
      ([e]) => {
        if (e && e.intersectionRatio >= threshold) {
          setSeen(true);
          io.disconnect();
        }
      },
      { root, threshold: [threshold] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold, seen]);
  return [ref, seen];
}
