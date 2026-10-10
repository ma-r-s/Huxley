import type { CapacitorConfig } from "@capacitor/cli";

// Native iOS shell around the PWA (Capacitor). Why native: a home-screen web
// app asks for the microphone on every launch; a native app asks once.
// Capacitor's WKWebView also auto-grants the page's mic request and lets
// audio start without a tap.
//
// Dev mode: point the app at the live Vite dev server and every web change
// shows up on the phone with no reinstall (only native changes need one):
//   CAP_SERVER_URL=https://<mac>.<tailnet>.ts.net:8443 bunx cap sync ios
// Without CAP_SERVER_URL the app bundles `dist/` (run `bun run build` first).
const devServerUrl = process.env.CAP_SERVER_URL;

const config: CapacitorConfig = {
  appId: "com.marioruiz.huxley",
  appName: "Huxley",
  webDir: "dist",
  ...(devServerUrl ? { server: { url: devServerUrl } } : {}),
  ios: {
    // Full-bleed like the standalone PWA; the CSS handles safe areas.
    contentInset: "never",
  },
};

export default config;
