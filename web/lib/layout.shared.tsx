import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

export const baseOptions: BaseLayoutProps = {
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
    url: "/",
  },
  links: [],
};
