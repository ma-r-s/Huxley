import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Analytics } from "@vercel/analytics/react";
import { SpeedInsights } from "@vercel/speed-insights/react";
import { Landing } from "./Landing.js";
import "./styles/index.css";
import "./i18n/index.js"; // initializes i18next; safe-effect import

// Vercel Analytics + Speed Insights are cookieless and only fire on Vercel
// deployments — locally and on other hosts they're no-ops, so no privacy
// banner needed. Speed Insights reports real-user Core Web Vitals (LCP,
// INP, CLS) instead of synthetic Lighthouse runs.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Landing />
    <Analytics />
    <SpeedInsights />
  </StrictMode>,
);
