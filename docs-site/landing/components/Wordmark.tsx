"use client";
// Italic-serif "huxley" lockup with optional mono subline.

interface WordmarkProps {
  size?: number;
  color?: string;
  subtle?: string;
}

export function Wordmark({
  size = 28,
  color = "currentColor",
  subtle,
}: WordmarkProps) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
      <span
        style={{
          fontFamily: "var(--hux-serif)",
          fontStyle: "italic",
          fontWeight: 400,
          fontSize: size,
          letterSpacing: "-0.015em",
          lineHeight: 1,
          color,
          // The brand mark is always lowercase. Defend against parents
          // that apply text-transform (e.g. the footer's uppercase mono
          // styling) — without this, "huxley" became "HUXLEY" there.
          textTransform: "none",
        }}
      >
        huxley
      </span>
      {subtle && (
        <span
          style={{
            fontFamily: "var(--hux-mono)",
            fontSize: 10,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "currentColor",
            opacity: 0.45,
          }}
        >
          {subtle}
        </span>
      )}
    </div>
  );
}
