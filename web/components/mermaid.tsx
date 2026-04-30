"use client";

import { useEffect, useId, useRef, useState } from "react";

// Mermaid renderer with brand-matched theme variables. Used inline in
// MDX for turn flow, focus channel, and audio path diagrams. The dynamic
// import keeps mermaid out of the initial bundle (it's huge).
export function Mermaid({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>("");
  const rawId = useId();
  const id = `mermaid-${rawId.replace(/:/g, "")}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mermaid = (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "base",
        themeVariables: {
          primaryColor: "oklch(0.62 0.19 23)",
          primaryTextColor: "oklch(0.96 0.015 60)",
          primaryBorderColor: "oklch(0.42 0.14 22)",
          lineColor: "oklch(0.42 0.14 22)",
          fontFamily: "var(--font-inter-tight), system-ui, sans-serif",
          fontSize: "14px",
        },
      });
      const { svg: rendered } = await mermaid.render(id, chart);
      if (!cancelled) setSvg(rendered);
    })();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  return (
    <div
      ref={ref}
      className="my-6 flex justify-center"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
