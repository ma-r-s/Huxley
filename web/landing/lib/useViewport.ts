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

function snapshot(): Viewport {
  const w = typeof window === "undefined" ? 1280 : window.innerWidth;
  return {
    width: w,
    isMobile: w < BP_MOBILE,
    isTablet: w >= BP_MOBILE && w < BP_TABLET,
    isDesktop: w >= BP_TABLET,
  };
}

export function useViewport(): Viewport {
  const [vp, setVp] = useState<Viewport>(() => snapshot());
  useEffect(() => {
    const onResize = () => setVp(snapshot());
    window.addEventListener("resize", onResize, { passive: true });
    window.addEventListener("orientationchange", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
    };
  }, []);
  return vp;
}
