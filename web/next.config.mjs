import { createMDX } from "fumadocs-mdx/next";

const withMDX = createMDX();

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-DNS-Prefetch-Control", value: "on" },
];

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  async redirects() {
    return [
      {
        source: "/docs",
        destination: "/docs/welcome",
        permanent: true,
      },
    ];
  },
  // The HX-1 build guide is a static page that the enclosure's site.py
  // writes (site.py --web): served as is, under the HX-1 page.
  async rewrites() {
    return [{ source: "/hx-1/build", destination: "/hx1/build.html" }];
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default withMDX(config);
