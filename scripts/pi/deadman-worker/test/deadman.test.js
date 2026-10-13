// Unit tests for the outside check: bun test (from this directory).
import { describe, expect, spyOn, test } from "bun:test";
import {
  CHECK_URL,
  CLICK_URL,
  INITIAL,
  STATE_KEY,
  bogotaHHMM,
  isQuiet,
  message,
  run,
  step,
} from "../src/index.js";

// Bogota is UTC-5: 10:00 Bogota is 15:00Z.
const at = (iso) => Date.parse(iso);
const MIN = 60_000;
const DAY_10_00 = at("2026-10-13T15:00:00Z");

class MemKV {
  constructor() {
    this.map = new Map();
    this.puts = 0;
  }
  async get(key, opts) {
    const v = this.map.get(key);
    if (v === undefined) return null;
    return opts?.type === "json" ? JSON.parse(v) : v;
  }
  async put(key, value) {
    this.puts += 1;
    this.map.set(key, value);
  }
}

// A fetch that answers the check with the next scripted status (or throws
// for "throw") and records every ntfy POST.
function fakeFetch(statuses, { ntfyStatus = 200, ntfyBody = "{}" } = {}) {
  const pushes = [];
  const queue = [...statuses];
  const impl = async (url, init) => {
    if (String(url).startsWith("https://ntfy.sh/")) {
      pushes.push({ url: String(url), headers: init.headers, body: init.body });
      return new Response(ntfyBody, { status: ntfyStatus });
    }
    const s = queue.shift();
    if (s === "throw") throw new TypeError("fetch failed");
    return new Response("x", { status: s });
  };
  return { impl, pushes };
}

function env(extra = {}) {
  return { STATE: new MemKV(), NTFY_TOPIC: "test-topic", ...extra };
}

async function runAll(e, statuses, times, opts) {
  const f = fakeFetch(statuses, opts);
  const results = [];
  for (const t of times) results.push(await run(e, t, f.impl));
  return { results, pushes: f.pushes };
}

describe("Bogota time", () => {
  test("HH:MM is UTC-5", () => {
    expect(bogotaHHMM(at("2026-10-13T15:07:00Z"))).toBe("10:07");
    expect(bogotaHHMM(at("2026-10-14T03:05:00Z"))).toBe("22:05");
    expect(bogotaHHMM("2026-10-14T04:59:00.000Z")).toBe("23:59");
  });

  test("quiet from 22:00 to 07:00", () => {
    expect(isQuiet(at("2026-10-14T02:59:00Z"))).toBe(false); // 21:59
    expect(isQuiet(at("2026-10-14T03:00:00Z"))).toBe(true); // 22:00
    expect(isQuiet(at("2026-10-14T09:00:00Z"))).toBe(true); // 04:00
    expect(isQuiet(at("2026-10-14T11:59:00Z"))).toBe(true); // 06:59
    expect(isQuiet(at("2026-10-14T12:00:00Z"))).toBe(false); // 07:00
  });
});

describe("state machine", () => {
  test("healthy stays healthy without a push", () => {
    const t = step(INITIAL, true, DAY_10_00);
    expect(t.push).toBeNull();
    expect(t.next.fails).toBe(0);
  });

  test("one failure: no push yet", () => {
    const t = step(INITIAL, false, DAY_10_00);
    expect(t.push).toBeNull();
    expect(t.next.fails).toBe(1);
    expect(t.next.down_since).toBe("2026-10-13T15:00:00.000Z");
  });

  test("second failure sends one down push from the first failure's time", () => {
    const a = step(INITIAL, false, DAY_10_00).next;
    const b = step(a, false, DAY_10_00 + 5 * MIN);
    expect(b.push).toEqual({ kind: "down", since: "2026-10-13T15:00:00.000Z" });
    expect(b.next.notified_down).toBe(true);
    const c = step(b.next, false, DAY_10_00 + 10 * MIN);
    expect(c.push).toBeNull();
    expect(c.next.fails).toBe(3);
  });

  test("recovery before any push: nothing", () => {
    const a = step(INITIAL, false, DAY_10_00).next;
    const b = step(a, true, DAY_10_00 + 5 * MIN);
    expect(b.push).toBeNull();
    expect(b.next).toEqual({ ...INITIAL });
  });

  test("recovery after a push: one fixed push, then quiet", () => {
    let s = step(INITIAL, false, DAY_10_00).next;
    s = step(s, false, DAY_10_00 + 5 * MIN).next;
    const r = step(s, true, DAY_10_00 + 20 * MIN);
    expect(r.push).toEqual({
      kind: "fixed",
      since: "2026-10-13T15:00:00.000Z",
      until: "2026-10-13T15:20:00.000Z",
    });
    expect(step(r.next, true, DAY_10_00 + 25 * MIN).push).toBeNull();
  });

  test("quiet hours hold the down push until the first check after 07:00", () => {
    const night = at("2026-10-14T04:00:00Z"); // 23:00 Bogota
    let s = INITIAL;
    for (let i = 0; i < 12; i++) {
      const t = step(s, false, night + i * 5 * MIN);
      expect(t.push).toBeNull();
      s = t.next;
    }
    expect(step(s, false, at("2026-10-14T11:55:00Z")).push).toBeNull(); // 06:55
    const morning = step(s, false, at("2026-10-14T12:00:00Z")); // 07:00
    expect(morning.push).toEqual({
      kind: "down",
      since: "2026-10-14T04:00:00.000Z",
    });
  });

  test("recovered during quiet hours before any push: nothing at all", () => {
    const night = at("2026-10-14T04:00:00Z");
    let s = step(INITIAL, false, night).next;
    s = step(s, false, night + 5 * MIN).next;
    const r = step(s, true, night + 10 * MIN);
    expect(r.push).toBeNull();
    expect(step(r.next, true, at("2026-10-14T12:00:00Z")).push).toBeNull();
  });

  test("the fixed push is not held by quiet hours", () => {
    let s = step(INITIAL, false, at("2026-10-14T02:50:00Z")).next; // 21:50
    s = step(s, false, at("2026-10-14T02:55:00Z")).next; // 21:55: down push
    expect(s.notified_down).toBe(true);
    const r = step(s, true, at("2026-10-14T04:00:00Z")); // 23:00
    expect(r.push?.kind).toBe("fixed");
  });
});

describe("push texts", () => {
  test("down", () => {
    const m = message({ kind: "down", since: "2026-10-13T15:05:00.000Z" });
    expect(m).toEqual({
      title: "Walkie: needs you",
      priority: "high",
      tags: "warning",
      body:
        "Huxley's server can't be reached from the internet since 10:05: " +
        "the Pi, its power, the home internet or the tunnel is down.",
    });
  });

  test("fixed", () => {
    const m = message({
      kind: "fixed",
      since: "2026-10-13T15:05:00.000Z",
      until: "2026-10-13T16:40:00.000Z",
    });
    expect(m.title).toBe("Walkie: fixed");
    expect(m.priority).toBe("default");
    expect(m.tags).toBe("white_check_mark");
    expect(m.body).toBe(
      "Huxley's server is reachable again (down from 10:05 to 11:40).",
    );
  });
});

describe("run", () => {
  test("only 401 is healthy: 530, 502, 200 and errors fail", async () => {
    for (const s of [530, 502, 200, 404, "throw"]) {
      const e = env();
      const { results } = await runAll(e, [s], [DAY_10_00]);
      expect(results[0].state.fails).toBe(1);
    }
    const e = env();
    const { results } = await runAll(e, [401], [DAY_10_00]);
    expect(results[0].state.fails).toBe(0);
    expect(results[0].state.last_result).toBe("401");
  });

  test("down then fixed: two pushes with the ntfy headers, one KV write per check", async () => {
    const e = env();
    const times = [0, 5, 10, 15, 20, 25].map((m) => DAY_10_00 + m * MIN);
    const { pushes } = await runAll(
      e,
      [530, 502, "throw", 401, 401, 401],
      times,
    );
    expect(pushes.length).toBe(2);
    expect(pushes[0].url).toBe("https://ntfy.sh/test-topic");
    expect(pushes[0].headers).toEqual({
      Title: "Walkie: needs you",
      Priority: "high",
      Tags: "warning",
      Click: CLICK_URL,
    });
    expect(pushes[0].body).toContain("since 10:00:");
    expect(pushes[1].headers.Title).toBe("Walkie: fixed");
    expect(pushes[1].headers.Tags).toBe("white_check_mark");
    expect(pushes[1].body).toContain("down from 10:00 to 10:15");
    expect(e.STATE.puts).toBe(times.length);
    const saved = JSON.parse(e.STATE.map.get(STATE_KEY));
    expect(saved.notified_down).toBe(false);
    expect(saved.last_check).toBe("2026-10-13T15:25:00.000Z");
  });

  test("a failed down push is retried at the next check", async () => {
    const e = env();
    const times = [0, 5].map((m) => DAY_10_00 + m * MIN);
    const first = await runAll(e, [530, 530], times, { ntfyStatus: 500 });
    expect(first.pushes.length).toBe(1);
    expect(first.results[1].state.notified_down).toBe(false);
    expect(e.STATE.puts).toBe(3); // the failed push wrote its retry state too
    const again = await runAll(e, [530], [DAY_10_00 + 10 * MIN]);
    expect(again.pushes.length).toBe(1);
    expect(again.pushes[0].body).toContain("since 10:00:");
    expect(again.results[0].state.notified_down).toBe(true);
  });

  test("a failed fixed push is retried with the first recovery time", async () => {
    const e = env();
    await runAll(
      e,
      [530, 530],
      [0, 5].map((m) => DAY_10_00 + m * MIN),
    );
    const bad = await runAll(e, [401], [DAY_10_00 + 10 * MIN], {
      ntfyStatus: 503,
    });
    expect(bad.results[0].state.notified_down).toBe(true);
    const good = await runAll(e, [401], [DAY_10_00 + 15 * MIN]);
    expect(good.pushes.length).toBe(1);
    expect(good.pushes[0].body).toContain("down from 10:00 to 10:10");
    expect(good.results[0].state.notified_down).toBe(false);
  });

  test("no topic: nothing sent, retried later", async () => {
    const e = env({ NTFY_TOPIC: undefined });
    const { results, pushes } = await runAll(
      e,
      [530, 530],
      [0, 5].map((m) => DAY_10_00 + m * MIN),
    );
    expect(pushes.length).toBe(0);
    expect(results[1].delivery).toEqual({
      sent: false,
      error: "no NTFY_TOPIC secret",
    });
    expect(results[1].state.notified_down).toBe(false);
  });

  test("state is saved before the push: a KV failure skips the push, never repeats it", async () => {
    const e = env();
    await runAll(e, [530], [DAY_10_00]);
    e.STATE.put = async () => {
      throw new Error("KV write limit");
    };
    const f = fakeFetch([530]);
    await expect(run(e, DAY_10_00 + 5 * MIN, f.impl)).rejects.toThrow("KV write limit");
    expect(f.pushes.length).toBe(0);
  });

  test("a refused push logs ntfy's code and reason", async () => {
    const e = env();
    const ntfyBody = JSON.stringify({
      code: 42901,
      http: 429,
      error: "limit reached: too many requests",
      link: "https://ntfy.sh/docs/publish/#limitations",
    });
    const { results } = await runAll(e, [530, 530], [0, 5].map((m) => DAY_10_00 + m * MIN), {
      ntfyStatus: 429,
      ntfyBody,
    });
    expect(results[1].delivery).toEqual({
      sent: false,
      error: "ntfy 429 42901 limit reached: too many requests",
    });
  });

  test("the topic is trimmed (a piped secret may end in a newline)", async () => {
    const e = env({ NTFY_TOPIC: "test-topic\n" });
    const { pushes } = await runAll(e, [530, 530], [0, 5].map((m) => DAY_10_00 + m * MIN));
    expect(pushes[0].url).toBe("https://ntfy.sh/test-topic");
  });

  test("an outage that ended before any push is still in the log line", async () => {
    const e = env();
    await runAll(e, [530], [DAY_10_00]);
    const log = spyOn(console, "log");
    try {
      await runAll(e, [401], [DAY_10_00 + 5 * MIN]);
      const line = JSON.parse(log.mock.calls.at(-1)[0]);
      expect(line.push).toBeNull();
      expect(line.ended_outage).toEqual({ down_since: "2026-10-13T15:00:00.000Z", fails: 1 });
    } finally {
      log.mockRestore();
    }
  });

  test("checks the production URL unless CHECK_URL overrides it", async () => {
    const seen = [];
    const spy = async (url) => {
      seen.push(String(url));
      return new Response("", { status: 401 });
    };
    await run(env(), DAY_10_00, spy);
    await run(env({ CHECK_URL: "https://example.invalid/" }), DAY_10_00, spy);
    expect(seen).toEqual([CHECK_URL, "https://example.invalid/"]);
  });
});
