"use client";
// useViewport — single source of truth for breakpoints across the landing.
// Sections branch on `isMobile` / `isTablet` to swap inline layout styles.
// We use JS-side breakpoints (vs CSS media queries) because the landing leans
// heavily on inline `style={{ ... }}` props, and inline styles win over any
// CSS @media rule. Branching in JS avoids a confusing dual-source-of-truth.

import { useEffect, useState } from "react";

export const BP_MOBILE = 640; // px — phone portrait
export const BP_TABLET = 960; // px — tablet / small laptop

export interface Viewport {
  width: number;
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
}

// SSR default matches the server render (desktop) so hydration is stable.
// The useEffect immediately fires on mount with the real window size.
const SSR_DEFAULT: Viewport = {
  width: 1280,
  isMobile: false,
  isTablet: false,
  isDesktop: true,
};

// Breakpoints by media query, the width CSS uses. Not innerWidth: on a
// phone that is the zoomed-out visual width, which grows to fit anything
// that overflows. The server renders the desktop layout, which overflows a
// phone, so innerWidth read 701 at 390 px and the landing stayed in its
// desktop layout (2026-10-12).
const MQ_MOBILE = `(max-width: ${BP_MOBILE - 0.02}px)`;
const MQ_TABLET = `(max-width: ${BP_TABLET - 0.02}px)`;

function snapshot(): Viewport {
  const mobile = window.matchMedia(MQ_MOBILE).matches;
  const tablet = !mobile && window.matchMedia(MQ_TABLET).matches;
  return {
    width: document.documentElement.clientWidth,
    isMobile: mobile,
    isTablet: tablet,
    isDesktop: !mobile && !tablet,
  };
}

export function useViewport(): Viewport {
  const [vp, setVp] = useState<Viewport | null>(null);
  useEffect(() => {
    setVp(snapshot());
    const onChange = () => setVp(snapshot());
    const queries = [MQ_MOBILE, MQ_TABLET].map((q) => window.matchMedia(q));
    for (const q of queries) q.addEventListener("change", onChange);
    window.addEventListener("resize", onChange, { passive: true });
    return () => {
      for (const q of queries) q.removeEventListener("change", onChange);
      window.removeEventListener("resize", onChange);
    };
  }, []);
  return vp ?? SSR_DEFAULT;
}
