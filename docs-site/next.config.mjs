import { createMDX } from "fumadocs-mdx/next";

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  // basePath lets the docs app live at huxley.dev/docs while running
  // standalone at localhost:5176 during development. Vercel rewrite in
  // site/vercel.json proxies /docs/* here in production.
  basePath: "/docs",
  // Asset prefix matches basePath so static assets resolve correctly
  // when served behind the rewrite.
  assetPrefix: "/docs",
};

export default withMDX(config);
