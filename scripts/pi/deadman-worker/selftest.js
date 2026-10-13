// Live self-test on the Mac: real fetches and real pushes, but to a fresh
// throwaway ntfy topic and an in-memory KV. Never touches Mario's topic.
//   bun selftest.js        (from this directory)
// Two failed checks (an unresolvable host, then a URL that answers 404)
// must send one down push; a check of the real hostname (401) must then
// send one fixed push. The pushes are read back from ntfy and compared.
import { CHECK_URL, CLICK_URL, run } from "./src/index.js";

const topic = `huxley-selftest-${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
const kv = new Map();
const STATE = {
  get: async (k, o) =>
    kv.has(k) ? (o?.type === "json" ? JSON.parse(kv.get(k)) : kv.get(k)) : null,
  put: async (k, v) => void kv.set(k, v),
};
// Daytime in Bogota (10:00, 10:05, 10:10) so quiet hours stay out of it.
const t0 = Date.parse("2026-10-13T15:00:00Z");
const steps = [
  ["https://deadman-selftest.invalid/", t0],
  ["https://walkie.ma-r-s.com/health", t0 + 5 * 60_000],
  [CHECK_URL, t0 + 10 * 60_000],
];
console.log(`throwaway topic: ${topic}`);
for (const [url, now] of steps) {
  await run({ STATE, NTFY_TOPIC: topic, CHECK_URL: url }, now);
}

await new Promise((r) => setTimeout(r, 2000));
const res = await fetch(`https://ntfy.sh/${topic}/json?poll=1&since=all`);
const got = (await res.text())
  .split("\n")
  .filter(Boolean)
  .map((l) => JSON.parse(l))
  .filter((m) => m.event === "message");
for (const m of got) {
  console.log(
    JSON.stringify({
      title: m.title,
      priority: m.priority,
      tags: m.tags,
      click: m.click,
      message: m.message,
    }),
  );
}

const want = [
  {
    title: "Walkie: needs you",
    priority: 4,
    tags: ["warning"],
    message:
      "Huxley's server can't be reached from the internet since 10:00: " +
      "the Pi, its power, the home internet or the tunnel is down.",
  },
  {
    title: "Walkie: fixed",
    priority: 3,
    tags: ["white_check_mark"],
    message: "Huxley's server is reachable again (down from 10:00 to 10:10).",
  },
];
const problems = [];
if (got.length !== want.length)
  problems.push(`expected ${want.length} pushes, got ${got.length}`);
want.forEach((w, i) => {
  const m = got[i];
  if (!m) return;
  for (const k of ["title", "message"])
    if (m[k] !== w[k]) problems.push(`push ${i} ${k}: ${m[k]}`);
  if ((m.priority ?? 3) !== w.priority)
    problems.push(`push ${i} priority: ${m.priority}`);
  if (JSON.stringify(m.tags) !== JSON.stringify(w.tags))
    problems.push(`push ${i} tags: ${m.tags}`);
  if (m.click !== CLICK_URL) problems.push(`push ${i} click: ${m.click}`);
});
if (problems.length) {
  console.error(`FAIL\n${problems.join("\n")}`);
  process.exit(1);
}
console.log("PASS: one down push, one fixed push, as specified");
