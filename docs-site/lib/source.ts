import type React from "react";
import { docs } from "@/.source/server";
import { loader } from "fumadocs-core/source";
import { createElement } from "react";
import * as LucideIcons from "lucide-react";

// Single source of truth for the docs tree — sidebar, search index, page
// resolution, and OG card generation all consume this. The .source/server
// module is generated at build time by `fumadocs-mdx` from the
// content/docs/**/*.mdx files; rerun via `bunx fumadocs-mdx` after content
// changes (also runs as a postinstall hook).
export const source = loader({
  baseUrl: "/docs",
  source: docs.toFumadocsSource(),
  icon(name) {
    if (name && name in LucideIcons) {
      return createElement(
        LucideIcons[name as keyof typeof LucideIcons] as React.ComponentType<{
          className?: string;
        }>,
        { className: "size-4" },
      );
    }
  },
});
