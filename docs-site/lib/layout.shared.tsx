import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

// Shared layout props used by both the home (root) layout and the docs
// layout. Centralizes nav config so the wordmark, links, and external
// references stay consistent across pages.
export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <span
          style={{
            fontFamily: "var(--font-instrument-serif), Georgia, serif",
            fontStyle: "italic",
            fontSize: 24,
            fontWeight: 400,
            letterSpacing: "-0.015em",
            textTransform: "none",
          }}
        >
          huxley
        </span>
      ),
      url: "/docs",
    },
    githubUrl: "https://github.com/ma-r-s/Huxley",
    links: [
      {
        text: "Home",
        url: "/",
        external: true,
      },
    ],
  };
}
