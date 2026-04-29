import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// ── Single source of truth for the canonical site URL ────────────────────
// Used by index.html (__SITE_URL__), sitemap.xml, robots.txt, and any SEO
// metadata that must be absolute (Open Graph, JSON-LD, hreflang). Static
// builds can't detect their own domain, so the URL has to be known at
// build time — but on Vercel that's automatic, no config required.
//
// Precedence:
//   1. VITE_SITE_URL — manual override (escape hatch for non-Vercel
//      deploys, custom canonical, or local production builds).
//   2. VERCEL_PROJECT_PRODUCTION_URL — auto-injected by Vercel during the
//      build with the project's production domain. Whatever you've aliased
//      in the dashboard gets used. Change domains? Just redeploy. This
//      env var is always set on Vercel — including for preview deploys,
//      where it correctly points at production so OG cards/canonical
//      links don't leak preview URLs into search indexes.
//   3. https://huxley.example.com — local-fallback so a misconfigured
//      deploy is obvious in search console rather than silently broken.
//
// Why we can't just do this at runtime: og:url, og:image, twitter:image,
// hreflang, and the JSON-LD url field are all scraped by bots that don't
// run JavaScript (Twitter, LinkedIn, Slack, Discord, Facebook in cold
// fetches). They have to be in the raw HTML — which means we have to
// know the domain at build time, not page load.
const SITE_URL = (
  process.env.VITE_SITE_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : null) ??
  "https://huxley.example.com"
).replace(/\/$/, "");

const SUPPORTED_LOCALES = ["en", "es", "fr"] as const;

function renderSitemap(): string {
  const today = new Date().toISOString().slice(0, 10);
  const alternates = ["x-default", ...SUPPORTED_LOCALES]
    .map(
      (lang) =>
        `    <xhtml:link rel="alternate" hreflang="${lang}" href="${SITE_URL}/" />`,
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<!--
  Single-page site: only one URL to declare. Multi-language is handled via
  runtime language detection on the same URL, so the alternates all point
  back to the canonical (mirrors the hreflang setup in index.html).
  Generated at build time by vite.config.ts from VITE_SITE_URL.
-->
<urlset
  xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
  xmlns:xhtml="http://www.w3.org/1999/xhtml"
>
  <url>
    <loc>${SITE_URL}/</loc>
    <lastmod>${today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
${alternates}
  </url>
</urlset>
`;
}

function renderRobots(): string {
  return `User-agent: *
Allow: /

Sitemap: ${SITE_URL}/sitemap.xml
`;
}

// Substitutes __SITE_URL__ in index.html and emits sitemap.xml + robots.txt
// derived from the same constant. One config knob feeds every absolute
// URL the build produces.
//
// Token is __SITE_URL__ rather than %SITE_URL% because Vite's HTML asset
// processor runs decodeURI() on every href/src/content attribute, and a
// stray "%SI" reads as a malformed percent-escape and crashes the build.
// The substitution also runs at order: "pre" so the token is gone before
// Vite's URL-rewriting pass sees the HTML.
function siteMetaPlugin(): Plugin {
  return {
    name: "huxley-site-meta",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => html.replaceAll("__SITE_URL__", SITE_URL),
    },
    configureServer(server) {
      server.middlewares.use("/sitemap.xml", (_req, res) => {
        res.setHeader("Content-Type", "application/xml");
        res.end(renderSitemap());
      });
      server.middlewares.use("/robots.txt", (_req, res) => {
        res.setHeader("Content-Type", "text/plain");
        res.end(renderRobots());
      });
    },
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "sitemap.xml",
        source: renderSitemap(),
      });
      this.emitFile({
        type: "asset",
        fileName: "robots.txt",
        source: renderRobots(),
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), siteMetaPlugin()],
  server: {
    host: true,
    port: 5175,
  },
});
