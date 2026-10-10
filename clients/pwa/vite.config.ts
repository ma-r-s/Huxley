import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: Number(process.env.HUXLEY_PWA_PORT ?? 5174),
    allowedHosts: [".ts.net"],
    proxy: {
      "/ws": {
        // Override to point a second PWA instance at a second server
        // (e.g. a simulator test rig that must not evict a real phone).
        target: process.env.HUXLEY_WS_TARGET ?? "ws://localhost:8765",
        ws: true,
      },
    },
  },
});
