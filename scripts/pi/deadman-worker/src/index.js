// Outside check for Huxley's production server (T1.43).
//
// The Pi pushes its own alerts through ntfy, but it cannot report its own
// death. This Worker asks the public hostname from outside every 5 minutes
// and pushes to the same ntfy topic when it stops answering.
//
// Healthy means GET https://walkie.ma-r-s.com/ answers 401 (the server
// wants a device token). 530 is the Pi or the tunnel connector down, 502 the
// connector up but the server down; any other status, a timeout or a fetch
// error is a failed check too.
//
// State is one JSON value in KV, written once per check (288 writes a day;
// the free plan allows 1000).

export const CHECK_URL = "https://walkie.ma-r-s.com/";
export const CHECK_TIMEOUT_MS = 10_000;
export const PUSH_TIMEOUT_MS = 10_000;
export const NTFY_BASE = "https://ntfy.sh/";
export const CLICK_URL = "http://100.75.152.70:8780/";
export const STATE_KEY = "state";
export const FAILS_TO_ALERT = 2;

const BOGOTA_OFFSET_MS = -5 * 3600 * 1000; // America/Bogota is UTC-5, no DST
const QUIET_FROM_H = 22; // quiet 22:00 to 07:00 Bogota time
const QUIET_TO_H = 7;

export const INITIAL = Object.freeze({
  down_since: null, // ISO time of the first failed check of this outage
  fails: 0, // consecutive failed checks
  notified_down: false, // a down push went out for this outage
  up_since: null, // set only while a fixed push still has to be delivered
  last_check: null, // ISO time of the last check
  last_result: null, // "401", "530", "error: TimeoutError", ...
});

function bogota(t) {
  return new Date(new Date(t).getTime() + BOGOTA_OFFSET_MS);
}

export function bogotaHHMM(t) {
  const d = bogota(t);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

export function isQuiet(t) {
  const h = bogota(t).getUTCHours();
  return h >= QUIET_FROM_H || h < QUIET_TO_H;
}

// Pure transition for one check. `next` assumes the push it asks for gets
// delivered; `onFail` is the state to keep when delivery fails, so the
// next check tries again.
export function step(prev, ok, now) {
  const s = { ...INITIAL, ...prev };
  const nowIso = new Date(now).toISOString();
  if (ok) {
    if (!s.notified_down) {
      // Healthy, or recovered before any push went out: say nothing.
      return { next: { ...INITIAL }, push: null };
    }
    const until = s.up_since ?? nowIso;
    return {
      next: { ...INITIAL },
      push: { kind: "fixed", since: s.down_since, until },
      onFail: { ...s, fails: 0, up_since: until },
    };
  }
  const down = {
    ...s,
    fails: s.fails + 1,
    down_since: s.down_since ?? nowIso,
    up_since: null,
  };
  if (!down.notified_down && down.fails >= FAILS_TO_ALERT && !isQuiet(now)) {
    return {
      next: { ...down, notified_down: true },
      push: { kind: "down", since: down.down_since },
      onFail: down,
    };
  }
  return { next: down, push: null };
}

export function message(push) {
  if (push.kind === "down") {
    return {
      title: "Walkie: needs you",
      priority: "high",
      tags: "warning",
      body:
        `Huxley's server can't be reached from the internet since ${bogotaHHMM(push.since)}: ` +
        "the Pi, its power, the home internet or the tunnel is down.",
    };
  }
  return {
    title: "Walkie: fixed",
    priority: "default",
    tags: "white_check_mark",
    body:
      `Huxley's server is reachable again ` +
      `(down from ${bogotaHHMM(push.since)} to ${bogotaHHMM(push.until)}).`,
  };
}

export async function check(url, fetchImpl = fetch) {
  try {
    const res = await fetchImpl(url, {
      method: "GET",
      redirect: "manual",
      headers: { "user-agent": "huxley-walkie-deadman" },
      signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
    });
    const status = res.status;
    res.body?.cancel().catch(() => {});
    return { ok: status === 401, result: String(status) };
  } catch (err) {
    const why = `${err?.name ?? "Error"}: ${err?.message ?? err}`;
    return { ok: false, result: `error: ${why}`.slice(0, 200) };
  }
}

// ntfy explains a refusal in a JSON body such as {"code":42901,"error":
// "limit reached: too many requests"}; keep its code and words, nothing
// else from the body.
async function ntfyReason(res) {
  try {
    const j = JSON.parse((await res.text()).slice(0, 1000));
    return ` ${j.code ?? ""} ${String(j.error ?? "").slice(0, 120)}`.trimEnd();
  } catch {
    return "";
  }
}

// Errors carry the error's name, the HTTP status and ntfy's reason only:
// never the URL, which holds the secret topic.
export async function push(topic, msg, fetchImpl = fetch) {
  const name = (topic ?? "").trim();
  if (!name) return { sent: false, error: "no NTFY_TOPIC secret" };
  try {
    const res = await fetchImpl(NTFY_BASE + encodeURIComponent(name), {
      method: "POST",
      body: msg.body,
      headers: {
        Title: msg.title,
        Priority: msg.priority,
        Tags: msg.tags,
        Click: CLICK_URL,
      },
      signal: AbortSignal.timeout(PUSH_TIMEOUT_MS),
    });
    if (res.ok) {
      res.body?.cancel().catch(() => {});
      return { sent: true };
    }
    return { sent: false, error: `ntfy ${res.status}${await ntfyReason(res)}` };
  } catch (err) {
    return { sent: false, error: err?.name ?? "Error" };
  }
}

// One check: read state, probe, write state, maybe push, log one line.
// The state is saved before the push: if KV fails, the push is skipped
// rather than repeated every 5 minutes. A push that fails costs a second
// write (the state that retries it).
// env.CHECK_URL is unset in production; it exists to rehearse the alert
// path against a URL that fails.
export async function run(env, now, fetchImpl = fetch) {
  const url = env.CHECK_URL || CHECK_URL;
  const [stored, probe] = await Promise.all([
    env.STATE.get(STATE_KEY, { type: "json" }),
    check(url, fetchImpl),
  ]);
  const prev = { ...INITIAL, ...stored };
  const t = step(prev, probe.ok, now);
  const stamp = {
    last_check: new Date(now).toISOString(),
    last_result: probe.result,
  };
  let state = { ...t.next, ...stamp };
  await env.STATE.put(STATE_KEY, JSON.stringify(state));
  let delivery = null;
  if (t.push) {
    delivery = await push(env.NTFY_TOPIC, message(t.push), fetchImpl);
    if (!delivery.sent) {
      state = { ...t.onFail, ...stamp };
      await env.STATE.put(STATE_KEY, JSON.stringify(state));
    }
  }
  // An outage that ended before any push leaves no trace in the state.
  const ended = prev.down_since && !state.down_since ? prev : null;
  console.log(
    JSON.stringify({
      event: "deadman.check",
      ok: probe.ok,
      result: probe.result,
      url_override: url !== CHECK_URL,
      fails: state.fails,
      down_since: state.down_since,
      notified_down: state.notified_down,
      push: t.push?.kind ?? null,
      sent: delivery?.sent ?? null,
      push_error: delivery?.error ?? null,
      ended_outage: ended && { down_since: ended.down_since, fails: ended.fails },
    }),
  );
  return { state, push: t.push, delivery };
}

export default {
  async scheduled(controller, env) {
    await run(env, Date.now());
  },
};
