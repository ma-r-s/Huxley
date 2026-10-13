// Read by `cf deploy` (run from this directory). The ntfy topic is the
// Worker secret NTFY_TOPIC, never in this file: see scripts/pi/README.md.
export default {
  type: "worker",
  name: "huxley-walkie-deadman",
  entrypoint: "src/index.js",
  compatibilityDate: "2026-07-30",
  // Cron only: no public URL.
  workersDev: false,
  previewUrls: false,
  triggers: [{ type: "scheduled", schedule: "*/5 * * * *" }],
  // Traces stay off: they would record the push URL, which holds the topic.
  observability: { enabled: true, traces: { enabled: false } },
  env: {
    STATE: { type: "kv", id: "c022afaaf2d34fde88429e76257a3738" },
    // Declared so a redeploy keeps it (an undeclared secret is dropped).
    NTFY_TOPIC: { type: "secret" },
  },
};
