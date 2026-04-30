import { defineConfig, defineDocs } from "fumadocs-mdx/config";

// Single docs collection rooted at content/docs.
// Frontmatter (title, description, etc.) is parsed automatically.
export const docs = defineDocs({
  dir: "content/docs",
});

export default defineConfig();
