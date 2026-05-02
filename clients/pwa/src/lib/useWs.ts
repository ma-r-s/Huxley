// WebSocket hook — React port of the huxley wire protocol.
// Mirrors ws.svelte.ts logic; uses refs for callback-readable state to avoid
// stale closures, useState for render-triggering state.

import { useState, useRef, useCallback } from "react";
import type {
  AppState,
  InputMode,
  PersonaEntry,
  TranscriptEntry,
  StatusEntry,
  DevEvent,
  SessionMeta,
  SessionTurn,
  SkillSummary,
  SkillsState,
  MarketplaceEntry,
  MarketplaceState,
  InstallUIState,
} from "../types.js";
import { WsLog, type WsLogSnapshot } from "./wsLog.js";

const EXPECTED_PROTOCOL = 2;
// T1.21 — heartbeat ping interval. Every PING_INTERVAL_MS we send a
// `ping` and expect a `pong` back within PING_TIMEOUT_MS. Misses get
// recorded in the diag panel + a third miss force-closes the WS so
// the existing reconnect path heals it. 30s/5s SLA balances cost
// (one frame per direction every 30s) against detection latency
// (~95s worst case to detect a post-hello wedge that the T1.20
// watchdog can't catch because hello already arrived).
const PING_INTERVAL_MS = 30_000;
const PING_TIMEOUT_MS = 5_000;
const PING_MAX_MISSES = 3;
// T1.20 — how long after `onopen` we'll wait for the server's `hello`
// frame before declaring the socket half-dead and force-reconnecting.
// Server emits hello within milliseconds of accepting the WS upgrade,
// so 3s is a generous SLA — anything past this means the frame won't
// arrive (either Vite's proxy or the browser's deflate state is wedged
// after a StrictMode + eviction race).
const HELLO_WATCHDOG_MS = 3000;

// 1500 ms threshold before the auditory thinking-tone fires.
// Visual "thinking" state fires immediately on ptt_stop (see thinkingActive).
const SILENCE_TIMEOUT_MS = 1500;

let _id = 0;
const nextId = () => _id++;
const nowTs = () => new Date().toLocaleTimeString("en", { hour12: false });

// Same-origin WebSocket URL — uses wss when the page is HTTPS (Tailscale Serve,
// prod) and ws when on plain HTTP (localhost dev). Vite dev proxies /ws to the
// Python server; Tailscale Serve forwards /ws through Vite transparently.
export function defaultWsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8765/ws";
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

export interface ActiveStream {
  streamId: string;
  label: string | null;
  prerollMs: number;
}

type ServerMessage =
  | {
      type: "hello";
      protocol: number;
      // T1.13 additive fields. `current_persona` is the name of the
      // persona this connection is for (null only in the brief boot
      // window before the first persona loads — clients today won't
      // observe this). `available_personas` is the runtime-pushed
      // list the picker renders. Old clients ignore these keys, so
      // protocol stays at 2 — see docs/protocol.md.
      current_persona?: string | null;
      available_personas?: PersonaEntry[];
      // Marketplace v2 Phase D: a new tab connecting mid-install
      // sees the in-flight install via this field so its UI can
      // jump straight to the "Restarting" state instead of showing
      // "Installed ✓" prematurely. Null in steady state.
      install_in_progress?: {
        package: string;
        started_at_ms: number;
      } | null;
      // T1.16 — explicit session-readiness signal. `true` when the
      // server has a live `current_app` and no captured per-connection
      // error; `false` during lazy-boot failures, persona-load
      // failures, or recovering from a crashed swap. Old servers
      // (pre-T1.16) omit this; the PWA defaults `sessionReady = true`
      // so they stay usable. `session_error` is the human-readable
      // reason when not ready (e.g., "persona 'foo' failed to load").
      session_ready?: boolean;
      session_error?: string | null;
      // T1.21 — server-generated per-connection id, echoed back so
      // the PWA's diag panel can render it for cross-referencing
      // with server logs (which bind it via structlog contextvars).
      conn_id?: string;
    }
  | { type: "audio"; data: string }
  | { type: "audio_clear" }
  // T1.21 — heartbeat. Server replies pong with the original ts so
  // the client computes RTT.
  | { type: "pong"; ts: number; server_ts: number }
  // T1.21 — diagnostic state response. Server snapshots its
  // observability state (current conn, recent conns, app state,
  // runtime extras) on demand.
  | {
      type: "diag_state";
      now_ms: number;
      current: Record<string, unknown> | null;
      recent: Record<string, unknown>[];
      state: Record<string, unknown>;
      runtime: Record<string, unknown>;
    }
  | { type: "state"; value: AppState }
  | { type: "status"; message: string }
  | { type: "transcript"; role: "user" | "assistant"; text: string }
  | { type: "model_speaking"; value: boolean }
  | { type: "set_volume"; level: number }
  | {
      type: "input_mode";
      mode: InputMode;
      reason: string;
      claim_id: string | null;
    }
  | {
      type: "claim_started";
      claim_id: string;
      skill: string;
      // Human-readable label for the claim (e.g., contact name on a
      // call). Null when the skill didn't supply one; UI falls back
      // to a generic status string.
      title?: string | null;
    }
  | { type: "claim_ended"; claim_id: string; end_reason: string }
  | {
      type: "stream_started";
      stream_id: string;
      label: string | null;
      preroll_ms: number;
    }
  | { type: "stream_ended"; stream_id: string; end_reason: string }
  | { type: "dev_event"; kind: string; payload: Record<string, unknown> }
  | { type: "server_event"; event: string; data: Record<string, unknown> }
  | { type: "sessions_list"; sessions: SessionMeta[] }
  | { type: "session_detail"; id: number; turns: SessionTurn[] }
  | { type: "session_deleted"; id: number }
  | { type: "skills_state"; persona: string | null; skills: SkillSummary[] }
  | {
      type: "marketplace_state";
      skills: MarketplaceEntry[];
      registry_version: string | null;
      generated_at: string | null;
      fetched_at_ms: number;
      stale: boolean;
      error: string | null;
    }
  | {
      type: "install_event";
      kind: "started" | "complete";
      package: string;
      ok: boolean | null;
      error_code: string | null;
      error_message: string | null;
      restart_required: boolean;
    };

export function useWs() {
  // ── Render state ────────────────────────────────────────────────────────
  const [connected, setConnected] = useState(false);
  const [appState, setAppState] = useState<AppState>("IDLE");
  const [modelSpeaking, setModelSpeaking] = useState(false);
  const [inputMode, setInputMode] = useState<InputMode>("assistant_ptt");
  const [activeClaimId, setActiveClaimId] = useState<string | null>(null);
  // Human-readable label for the active claim (e.g., contact name on a
  // call). Null when no claim is active or the skill didn't supply a
  // title. Drives the "live" orb status label.
  const [activeClaimTitle, setActiveClaimTitle] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [statusLog, setStatusLog] = useState<StatusEntry[]>([]);
  const [devEvents, setDevEvents] = useState<DevEvent[]>([]);
  // Visual thinking state: fires immediately on ptt_stop; cleared by audio /
  // model_speaking:true / ptt_start / socket close. Decoupled from the 1500 ms
  // auditory thinking-tone timer — the visual should be instant.
  const [thinkingActive, setThinkingActive] = useState(false);
  // Active long-form audio stream (audiobook, radio). Set by stream_started,
  // cleared by stream_ended or socket close. Drives the "playing" orb state
  // and waveform visualizer. Null = no stream in progress.
  const [activeStream, setActiveStream] = useState<ActiveStream | null>(null);
  // Session history (T1.12). `sessionsList` is null until the first
  // `list_sessions` reply arrives — distinguishes "loading" from
  // "loaded and empty." `sessionDetail` holds the most recently
  // fetched single-session transcript.
  const [sessionsList, setSessionsList] = useState<SessionMeta[] | null>(null);
  const [sessionDetail, setSessionDetail] = useState<{
    id: number;
    turns: SessionTurn[];
  } | null>(null);
  // Persona discovery (T1.13). The server pushes both fields on every
  // `hello`. `availablePersonas` drives the picker; `currentPersona`
  // drives the chip text. `null` until the first hello arrives.
  const [availablePersonas, setAvailablePersonas] = useState<
    PersonaEntry[] | null
  >(null);
  const [currentPersona, setCurrentPersona] = useState<string | null>(null);

  // Marketplace v2 Phase A — DeviceSheet's Skills section requests
  // this on open via `requestSkillsState()`. Null until the first
  // reply arrives so the UI can distinguish "loading" from "loaded
  // and empty." Refreshed on every `skills_state` frame.
  const [skillsState, setSkillsState] = useState<SkillsState | null>(null);

  // Marketplace v2 Phase C — registry feed. Null until the first
  // `marketplace_state` reply arrives so the UI can distinguish
  // "loading" from "loaded and empty / errored." Refreshed on
  // every `marketplace_state` frame.
  const [marketplaceState, setMarketplaceState] =
    useState<MarketplaceState | null>(null);

  // Marketplace v2 Phase D — local install lifecycle state. Set when
  // the user clicks Install in MarketplaceDetailSheet; cleared when
  // the post-restart reconnect lands. Survives WS drops because the
  // PWA component owns it; useEffect can clear it once the new
  // marketplace_state arrives showing the package as installed.
  const [installState, setInstallState] = useState<InstallUIState | null>(null);

  // T1.16 — explicit session-readiness signal pushed by the server in
  // every hello. `true` once the server has a live `current_app` and
  // no captured persona-select error; `false` while lazy-booting,
  // failing to load a persona, or recovering from a crashed swap. The
  // PWA gates PTT + the orb hint on this so the user is never told
  // "ready" when the server actually couldn't bring up an Application.
  // `sessionError` carries the human-readable reason when not ready.
  // Default `true` so old servers (pre-T1.16) that don't emit the
  // field stay usable — the new field is strictly additive.
  const [sessionReady, setSessionReady] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);

  // ── Refs (callback-readable without stale closures) ─────────────────────
  const socketRef = useRef<WebSocket | null>(null);
  const activeUrlRef = useRef<string | null>(null);
  const switchingRef = useRef(false);
  // Set to true on intentional disconnect (unmount / persona switch) so the
  // onclose handler doesn't schedule a reconnect.
  const noReconnectRef = useRef(false);
  // T1.20 — hello watchdog. Server emits `hello` within milliseconds of
  // accepting the WS upgrade. If we don't see it within `_HELLO_WATCHDOG_MS`
  // of `onopen`, the socket is half-dead (TCP up both ways, frames not
  // flowing through Vite's proxy / browser's deflate state — observed
  // under StrictMode double-mount + the server's `client_evicted` flow).
  // The watchdog force-closes the socket so the normal reconnect path
  // schedules a fresh connection. Pre-T1.20 the user had to manually
  // reload past the wedge.
  const helloWatchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Mirrors of state vars that callbacks need to read synchronously
  const inputModeRef = useRef<InputMode>("assistant_ptt");
  const thinkingActiveRef = useRef(false);

  // Persona the client requested via the most recent `?persona=` —
  // tracked so the hello handler can detect when the server returned a
  // different `current_persona` (load failed, fell back to previous)
  // and surface a status to the user. Cleared once the next hello
  // arrives, regardless of match.
  const requestedPersonaRef = useRef<string | null>(null);
  // Mirror of `currentPersona` for callbacks (the hello handler runs
  // before React re-renders; this lets `selectPersona` short-circuit
  // a no-op call without waiting for state to flush).
  const currentPersonaRef = useRef<string | null>(null);

  // Silence timer (auditory thinking tone — fires after SILENCE_TIMEOUT_MS)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const thinkingToneActiveRef = useRef(false);

  // Captured at pttStart — tells pttStop whether to skip the silence timer.
  // See ws.svelte.ts for the two-timing-scenario analysis.
  const pttWasClaimHangupRef = useRef(false);

  // ── External audio callbacks ─────────────────────────────────────────────
  const onAudioRef = useRef<((data: string) => void) | null>(null);
  const onAudioClearRef = useRef<(() => void) | null>(null);
  const onThinkingToneStartRef = useRef<(() => void) | null>(null);
  const onThinkingToneStopRef = useRef<(() => void) | null>(null);
  const onSetVolumeRef = useRef<((level: number) => void) | null>(null);
  // Fired synchronously inside onmessage when stream_started arrives, before
  // React re-render. Receives preroll_ms so the caller can start an exact timer.
  const onStreamStartedRef = useRef<((prerollMs: number) => void) | null>(null);
  // Called when model_speaking:false arrives. Receives a `done` callback that
  // the caller must invoke to actually flip modelSpeaking to false. Allows the
  // caller to delay the transition until the audio buffer has drained.
  const onModelSpeakingFalseRef = useRef<((done: () => void) => void) | null>(
    null,
  );

  // ── Helpers ──────────────────────────────────────────────────────────────
  const pushStatus = useCallback((text: string) => {
    setStatusLog((prev) =>
      [{ id: nextId(), text, ts: nowTs() }, ...prev].slice(0, 30),
    );
  }, []);

  const pushDevEvent = useCallback(
    (kind: string, payload: Record<string, unknown>) => {
      setDevEvents((prev) =>
        [{ id: nextId(), kind, payload, ts: nowTs() }, ...prev].slice(0, 50),
      );
    },
    [],
  );

  const sendRaw = useCallback((msg: object) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      const encoded = JSON.stringify(msg);
      socketRef.current.send(encoded);
      // T1.21 — log every outbound frame. The log is a ref so this
      // doesn't trigger a re-render; the diag panel polls when open.
      wsLogRef.current?.recordTx(encoded, msg);
    }
  }, []);

  const sendClientEvent = useCallback(
    (event: string, data: Record<string, unknown> = {}) => {
      sendRaw({ type: "client_event", event, data });
    },
    [sendRaw],
  );

  const setThinkingActiveSync = useCallback((v: boolean) => {
    thinkingActiveRef.current = v;
    setThinkingActive(v);
  }, []);

  const cancelSilenceTimer = useCallback(
    (reason = "unknown") => {
      const hadTimer = silenceTimerRef.current !== null;
      if (silenceTimerRef.current !== null) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
      const hadTone = thinkingToneActiveRef.current;
      if (thinkingToneActiveRef.current) {
        thinkingToneActiveRef.current = false;
        onThinkingToneStopRef.current?.();
      }
      if (hadTimer || hadTone) {
        sendClientEvent("silence_timer_cancelled", {
          reason,
          had_timer: hadTimer,
          had_tone: hadTone,
        });
      }
    },
    [sendClientEvent],
  );

  const startSilenceTimer = useCallback(
    (trigger: string) => {
      if (silenceTimerRef.current !== null)
        clearTimeout(silenceTimerRef.current);
      sendClientEvent("silence_timer_started", { trigger });
      silenceTimerRef.current = setTimeout(() => {
        silenceTimerRef.current = null;
        thinkingToneActiveRef.current = true;
        sendClientEvent("thinking_tone_on", { reason: "silence_timeout" });
        onThinkingToneStartRef.current?.();
      }, SILENCE_TIMEOUT_MS);
    },
    [sendClientEvent],
  );

  // ── Connection ───────────────────────────────────────────────────────────
  // `language` and `persona` are appended to the base URL as
  // `?lang=<code>` and `?persona=<name>` so the server can resolve the
  // active session BEFORE the WebSocket handshake completes. The base
  // URL stashed in `activeUrlRef` is WITHOUT any query string; the
  // params are tracked separately so a swap-or-relang flow can rebuild
  // the full URL without parsing the previous one. T1.13: persona is
  // chosen at handshake time, not in-band, so the picker UX is a
  // close-and-reopen with a different `?persona=`.
  const activeLanguageRef = useRef<string | null>(null);
  const activePersonaRef = useRef<string | null>(null);

  // T1.21 — bidirectional frame log + lifecycle log. Lives in a ref
  // because new entries shouldn't trigger a re-render (frames are
  // hot-path). The diagnostic panel polls `wsLogRef.current.snapshot()`
  // when open. Created once per useWs instance via `useRef`'s lazy
  // initializer pattern.
  const wsLogRef = useRef<WsLog | null>(null);
  if (wsLogRef.current === null) wsLogRef.current = new WsLog();
  // T1.21 — heartbeat machinery. Each `ping` we send gets a unique ts;
  // when the matching `pong` lands we compute RTT and reset the miss
  // counter. After PING_MAX_MISSES consecutive timeouts we force-close
  // the WS (same path as the hello watchdog).
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pingPendingRef = useRef<{
    ts: number;
    timer: ReturnType<typeof setTimeout>;
  } | null>(null);
  const pingMissesRef = useRef(0);
  const lastPingRttMsRef = useRef<number | null>(null);
  // T1.21 — most-recent `diag_state` payload from the server. Stored
  // in a ref (not state) because only the diag panel reads it; we
  // don't want a re-render on every diag refresh. Panel pulls via
  // `getDiagnostic()` which composes this with the local frame log.
  const lastServerDiagRef = useRef<{
    receivedAtMs: number;
    payload: Record<string, unknown>;
  } | null>(null);

  const connect = useCallback(
    (url?: string, language?: string | null, persona?: string | null) => {
      // An explicit connect() call (initial mount, persona switch, or StrictMode
      // remount) always re-enables auto-reconnect.
      noReconnectRef.current = false;
      activeUrlRef.current = url ?? activeUrlRef.current ?? defaultWsUrl();
      if (language !== undefined) activeLanguageRef.current = language;
      if (persona !== undefined) activePersonaRef.current = persona;
      // Don't open a second socket if one is already live or connecting.
      // This prevents StrictMode's second mount from racing the first.
      const rs = socketRef.current?.readyState;
      if (rs === WebSocket.CONNECTING || rs === WebSocket.OPEN) return;
      const params = new URLSearchParams();
      if (activeLanguageRef.current) {
        params.set("lang", activeLanguageRef.current);
      }
      if (activePersonaRef.current) {
        params.set("persona", activePersonaRef.current);
      }
      const qs = params.toString();
      const full = qs
        ? `${activeUrlRef.current}${activeUrlRef.current.includes("?") ? "&" : "?"}${qs}`
        : activeUrlRef.current;
      const ws = new WebSocket(full);

      ws.onopen = () => {
        if (socketRef.current !== ws) return; // stale socket
        setConnected(true);
        pushStatus(`Connected to ${activeUrlRef.current}`);
        // T1.21 — lifecycle log entry so the diag panel timeline
        // shows the open event alongside the frames that follow.
        wsLogRef.current?.recordLifecycle("open", {
          url: activeUrlRef.current,
        });
        // T1.20 — arm the hello watchdog. Cleared on hello arrival
        // (case "hello" below) and on close. If it fires, the socket
        // is half-dead and we force-close to trigger reconnect.
        if (helloWatchdogRef.current !== null) {
          clearTimeout(helloWatchdogRef.current);
        }
        helloWatchdogRef.current = setTimeout(() => {
          helloWatchdogRef.current = null;
          if (socketRef.current !== ws) return; // already replaced
          if (ws.readyState !== WebSocket.OPEN) return; // already closed
          pushStatus("Connection unhealthy — reconnecting…");
          wsLogRef.current?.recordLifecycle("watchdog-fired", {
            reason: "no-hello-after-3s",
          });
          ws.close(4002, "no-hello-watchdog");
        }, HELLO_WATCHDOG_MS);
        // T1.16 — refetch skills_state on every open, not just on
        // sheet mount. The previous fire-on-mount-only path lost the
        // response when StrictMode's double-mount eviction landed
        // the reply on a socket that had already been closed —
        // leaving the panel "Loading…" forever.
        try {
          const reqEncoded = JSON.stringify({ type: "get_skills_state" });
          ws.send(reqEncoded);
          wsLogRef.current?.recordTx(reqEncoded, { type: "get_skills_state" });
        } catch {
          // Socket closed between OPEN check and send; the next
          // reconnect's onopen will retry.
        }
        // T1.21 — start the heartbeat loop. Every PING_INTERVAL_MS
        // we send a `ping` and arm a per-ping timeout. On miss,
        // increment misses + record; after PING_MAX_MISSES, force-close.
        if (pingTimerRef.current !== null) clearInterval(pingTimerRef.current);
        pingMissesRef.current = 0;
        lastPingRttMsRef.current = null;
        pingTimerRef.current = setInterval(() => {
          if (socketRef.current !== ws || ws.readyState !== WebSocket.OPEN)
            return;
          const ts = Date.now();
          // Cancel any in-flight ping waiter (a fresh ping replaces it
          // — the previous one already counted as a miss when it was
          // pending past the last interval).
          if (pingPendingRef.current !== null) {
            clearTimeout(pingPendingRef.current.timer);
          }
          const timer = setTimeout(() => {
            // Pong didn't arrive within PING_TIMEOUT_MS.
            pingMissesRef.current += 1;
            wsLogRef.current?.recordLifecycle("ping-miss", {
              ts,
              consecutive: pingMissesRef.current,
            });
            pingPendingRef.current = null;
            if (
              pingMissesRef.current >= PING_MAX_MISSES &&
              socketRef.current === ws &&
              ws.readyState === WebSocket.OPEN
            ) {
              pushStatus("Connection unhealthy — reconnecting…");
              wsLogRef.current?.recordLifecycle("watchdog-fired", {
                reason: `ping-miss-${pingMissesRef.current}`,
              });
              ws.close(4003, "ping-miss-watchdog");
            }
          }, PING_TIMEOUT_MS);
          pingPendingRef.current = { ts, timer };
          try {
            const encoded = JSON.stringify({ type: "ping", ts });
            ws.send(encoded);
            wsLogRef.current?.recordTx(encoded, { type: "ping", ts });
          } catch {
            // Send failed; the interval will try again next tick. The
            // ping-pending timeout will fire and count as a miss.
          }
        }, PING_INTERVAL_MS);
      };

      ws.onclose = (ev) => {
        if (socketRef.current !== ws) return; // stale socket — don't clobber live connection
        // T1.21 — capture the close cause in the lifecycle log so the
        // diag panel timeline shows WHY a connection ended (1006
        // network drop vs 1001 server eviction vs 4002 hello watchdog
        // vs 4003 ping watchdog). Code 1000 is normal.
        wsLogRef.current?.recordLifecycle(`close-${ev.code}`, {
          code: ev.code,
          reason: ev.reason || null,
          wasClean: ev.wasClean,
        });
        setConnected(false);
        socketRef.current = null;
        cancelSilenceTimer("socket_close");
        // T1.20 — clear the hello watchdog if a close pre-empted it
        // (normal close, server-side eviction, or our own watchdog
        // fire). Prevents a stale timer from firing against a socket
        // that's already gone.
        if (helloWatchdogRef.current !== null) {
          clearTimeout(helloWatchdogRef.current);
          helloWatchdogRef.current = null;
        }
        // T1.21 — clear the heartbeat machinery on every close so a
        // stale interval can't keep firing against a dead socket.
        if (pingTimerRef.current !== null) {
          clearInterval(pingTimerRef.current);
          pingTimerRef.current = null;
        }
        if (pingPendingRef.current !== null) {
          clearTimeout(pingPendingRef.current.timer);
          pingPendingRef.current = null;
        }
        pingMissesRef.current = 0;
        lastPingRttMsRef.current = null;
        setThinkingActiveSync(false);
        setActiveStream(null);
        setActiveClaimTitle(null);
        // T1.16 — drop session-readiness on disconnect so the orb
        // hint shows "Reconnecting…" rather than stale "ready" while
        // the auto-reconnect loop is in flight.
        setSessionReady(false);
        setSessionError(null);
        if (switchingRef.current || noReconnectRef.current) return;
        pushStatus("Disconnected — retrying in 2s\u2026");
        setTimeout(() => connect(), 2000);
      };

      ws.onmessage = (ev) => {
        if (socketRef.current !== ws) return; // stale socket
        // T1.21 — log every inbound frame BEFORE dispatch so even a
        // dispatcher that drops on parse failure still leaves a trace
        // in the timeline. The log records its own parse + falls
        // back to a [malformed] entry if JSON.parse fails.
        if (typeof ev.data === "string") {
          wsLogRef.current?.recordRx(ev.data);
        }
        try {
          const msg = JSON.parse(ev.data as string) as ServerMessage;
          switch (msg.type) {
            case "hello":
              // T1.20 — disarm the hello watchdog. Frames are
              // flowing, the socket is healthy.
              if (helloWatchdogRef.current !== null) {
                clearTimeout(helloWatchdogRef.current);
                helloWatchdogRef.current = null;
              }
              if (msg.protocol !== EXPECTED_PROTOCOL) {
                pushStatus(
                  `Protocol mismatch: server=${msg.protocol} client=${EXPECTED_PROTOCOL}`,
                );
                ws.close(1002, "Protocol version mismatch");
                break;
              }
              // T1.13: server pushes `current_persona` + `available_personas`
              // on every hello. Capture both into render state so the picker
              // and chip refresh on each (re)connect.
              if (msg.available_personas !== undefined) {
                setAvailablePersonas(msg.available_personas);
              }
              if (msg.current_persona !== undefined) {
                const actual = msg.current_persona ?? null;
                currentPersonaRef.current = actual;
                setCurrentPersona(actual);
                // If the user requested a specific persona via
                // `selectPersona()`, the server may have rejected it
                // (load failed, persona not found) and fallen back to
                // whatever was previously active. Surface that to the
                // user instead of letting the picker silently snap
                // back to the wrong choice. Cleared regardless of
                // outcome so a subsequent same-persona reconnect (no
                // mismatch by definition) doesn't fire false positives.
                const requested = requestedPersonaRef.current;
                requestedPersonaRef.current = null;
                if (requested !== null && requested !== actual) {
                  pushStatus(
                    `Could not switch to ${requested} — staying on ${actual ?? "(no persona)"}`,
                  );
                }
              }
              // Phase D: server-side install in flight. Surface to
              // the install UI state so a fresh tab joining mid-
              // install sees "Restarting…" instead of "Idle".
              if (msg.install_in_progress) {
                setInstallState((prev) =>
                  prev !== null &&
                  prev.package === msg.install_in_progress!.package
                    ? prev // tab that started the install already has its state
                    : {
                        package: msg.install_in_progress!.package,
                        status: "running",
                        error_code: null,
                        error_message: null,
                        started_at_ms: msg.install_in_progress!.started_at_ms,
                      },
                );
              } else {
                // Phase D post-impl critic §6 — self-healing fallback.
                // If the install's `complete` WS frame got dropped on
                // the floor (race against `os.execv` flushing the
                // kernel buffer), the post-restart hello arriving
                // with `install_in_progress: null` is the signal that
                // the install is OVER. Transition any stuck running/
                // starting state to success-restarting so the
                // post-reconnect useEffect's auto-dismiss + refetch
                // path activates instead of hanging forever.
                setInstallState((prev) => {
                  if (prev === null) return prev;
                  if (prev.status === "running" || prev.status === "starting") {
                    return { ...prev, status: "success-restarting" };
                  }
                  return prev;
                });
              }
              // T1.16 — explicit session-readiness fields. Default to
              // ready when the field is missing (old servers) so we
              // don't break compatibility. When false, stash the
              // server-supplied error message so the orb hint can
              // show it.
              if (msg.session_ready === false) {
                setSessionReady(false);
                setSessionError(msg.session_error ?? "Server not ready");
              } else {
                setSessionReady(true);
                setSessionError(null);
              }
              break;
            case "audio":
              cancelSilenceTimer("audio_arrived");
              setThinkingActiveSync(false);
              onAudioRef.current?.(msg.data);
              break;
            case "audio_clear":
              cancelSilenceTimer("audio_clear");
              setThinkingActiveSync(false);
              onAudioClearRef.current?.();
              break;
            case "state":
              setAppState(msg.value);
              break;
            case "status":
              pushStatus(msg.message);
              break;
            case "transcript":
              setTranscript((prev) => [
                ...prev,
                { id: nextId(), role: msg.role, text: msg.text },
              ]);
              break;
            case "model_speaking":
              if (msg.value) {
                setModelSpeaking(true);
                cancelSilenceTimer("model_speaking_true");
                setThinkingActiveSync(false);
              } else if (onModelSpeakingFalseRef.current) {
                onModelSpeakingFalseRef.current(() => setModelSpeaking(false));
              } else {
                setModelSpeaking(false);
              }
              break;
            case "set_volume":
              onSetVolumeRef.current?.(msg.level);
              break;
            case "input_mode": {
              inputModeRef.current = msg.mode;
              setInputMode(msg.mode);
              setActiveClaimId(msg.claim_id);
              pushStatus(
                `Mic mode \u2192 ${msg.mode}${msg.reason ? ` (${msg.reason})` : ""}`,
              );
              if (msg.mode === "assistant_ptt") {
                cancelSilenceTimer("input_mode_assistant_ptt");
              }
              break;
            }
            case "claim_started":
              setActiveClaimTitle(msg.title ?? null);
              pushDevEvent("claim_started", {
                claim_id: msg.claim_id,
                skill: msg.skill,
                title: msg.title ?? null,
              });
              break;
            case "claim_ended":
              setActiveClaimTitle(null);
              pushDevEvent("claim_ended", {
                claim_id: msg.claim_id,
                end_reason: msg.end_reason,
              });
              break;
            case "stream_started":
              onStreamStartedRef.current?.(msg.preroll_ms);
              setActiveStream({
                streamId: msg.stream_id,
                label: msg.label,
                prerollMs: msg.preroll_ms,
              });
              break;
            case "stream_ended":
              setActiveStream(null);
              setActiveClaimTitle(null);
              break;
            case "dev_event":
              pushDevEvent(msg.kind, msg.payload);
              break;
            case "server_event":
              // Surface generic skill→client events in the same dev-event
              // log so they're visible in the existing dev surface
              // alongside `dev_event` and `claim_started`/`claim_ended`.
              // The kind is prefixed `server_event:<key>` so log readers
              // can grep them apart from internal dev events.
              pushDevEvent(`server_event:${msg.event}`, msg.data);
              break;
            case "sessions_list":
              // T1.12 — reply to listSessions(). Whole array replaced
              // each time; the server is the source of truth.
              setSessionsList(msg.sessions);
              break;
            case "session_detail":
              setSessionDetail({ id: msg.id, turns: msg.turns });
              break;
            case "session_deleted":
              // Drop the row from the cached list; clear the active
              // detail if it was for the deleted session so the
              // SessionDetailSheet can react via its prop.
              setSessionsList((prev) =>
                prev ? prev.filter((s) => s.id !== msg.id) : prev,
              );
              setSessionDetail((prev) => (prev?.id === msg.id ? null : prev));
              break;
            case "skills_state":
              // Marketplace v2 Phase A — DeviceSheet's Skills section.
              // Whole array replaced each time; the server is the
              // source of truth.
              setSkillsState({
                persona: msg.persona,
                skills: msg.skills,
              });
              break;
            case "marketplace_state":
              // Marketplace v2 Phase C — registry feed. Whole payload
              // replaced; server caches at 1h TTL so subsequent reads
              // return the same response cheaply.
              setMarketplaceState({
                skills: msg.skills,
                registry_version: msg.registry_version,
                generated_at: msg.generated_at,
                fetched_at_ms: msg.fetched_at_ms,
                stale: msg.stale,
                error: msg.error,
              });
              break;
            case "install_event":
              // Marketplace v2 Phase D — install lifecycle.
              // `started` flips the local state to "running"; `complete`
              // either flips to "success-restarting" (server is about
              // to os.execv; PWA's reconnect kicks in shortly) or
              // "error" (with error_code + message for the UI).
              setInstallState((prev) => {
                if (msg.kind === "started") {
                  return {
                    package: msg.package,
                    status: "running",
                    error_code: null,
                    error_message: null,
                    started_at_ms: Date.now(),
                  };
                }
                // kind === "complete"
                if (msg.ok) {
                  return {
                    package: msg.package,
                    status: "success-restarting",
                    error_code: null,
                    error_message: null,
                    started_at_ms: prev?.started_at_ms ?? Date.now(),
                  };
                }
                return {
                  package: msg.package,
                  status: "error",
                  error_code: msg.error_code,
                  error_message: msg.error_message,
                  started_at_ms: prev?.started_at_ms ?? Date.now(),
                };
              });
              break;
            case "pong": {
              // T1.21 — heartbeat reply. Compute RTT, clear the
              // pending-ping timeout, reset the miss counter. If
              // the timer was already cleared (e.g. socket racing
              // close), this still no-ops safely.
              if (
                pingPendingRef.current !== null &&
                pingPendingRef.current.ts === msg.ts
              ) {
                clearTimeout(pingPendingRef.current.timer);
                pingPendingRef.current = null;
              }
              const rtt = Date.now() - msg.ts;
              lastPingRttMsRef.current = rtt;
              pingMissesRef.current = 0;
              wsLogRef.current?.recordLifecycle("pong", {
                rtt_ms: rtt,
                server_ts: msg.server_ts,
              });
              break;
            }
            case "diag_state":
              // T1.21 — server-side diag snapshot. Stash in a ref;
              // the panel reads it via `getDiagnostic()`. Never
              // re-renders the rest of the app — diag is a passive
              // observer.
              lastServerDiagRef.current = {
                receivedAtMs: Date.now(),
                payload: msg as unknown as Record<string, unknown>,
              };
              break;
          }
        } catch {
          // ignore malformed messages
        }
      };

      socketRef.current = ws;
    },
    [pushStatus, pushDevEvent, cancelSilenceTimer, setThinkingActiveSync],
  );

  const disconnect = useCallback(() => {
    noReconnectRef.current = true;
    cancelSilenceTimer("disconnect");
    setThinkingActiveSync(false);
    setActiveStream(null);
    setActiveClaimTitle(null);
    socketRef.current?.close();
    socketRef.current = null;
    setConnected(false);
  }, [cancelSilenceTimer, setThinkingActiveSync]);

  // T1.13: persona switch is a WebSocket reconnect with `?persona=<name>`.
  // The server runs its swap algorithm on connect \u2014 close-and-reopen
  // mirrors the existing `setLanguage` flow. No-op if the requested
  // name already matches `currentPersona`. Sets `requestedPersonaRef`
  // so the next hello can detect a server-side fallback and surface
  // it as a "couldn't switch" status.
  const selectPersona = useCallback(
    (name: string) => {
      if (currentPersonaRef.current === name && socketRef.current !== null) {
        return;
      }
      requestedPersonaRef.current = name;
      activePersonaRef.current = name;
      switchingRef.current = true;
      pushStatus(`Switching to ${name}\u2026`);
      // Set CONNECTING (not IDLE) during the swap window. CONVERSING\u2192IDLE
      // triggers the unexpected-session-drop error tone (App.tsx) \u2014 we
      // don't want that on an INTENTIONAL swap. CONNECTING also makes
      // the PWA's PTT handler treat presses as "wait for state to
      // settle" instead of firing `wake_word` against the new server's
      // already-CONVERSING session (which the server would reject).
      setAppState("CONNECTING");
      setTranscript([]);
      setDevEvents([]);
      setStatusLog((prev) => prev.slice(0, 5));
      setActiveStream(null);
      setActiveClaimTitle(null);
      cancelSilenceTimer("persona_switch");
      setThinkingActiveSync(false);
      if (socketRef.current !== null) socketRef.current.close();
      setTimeout(() => {
        switchingRef.current = false;
        connect(undefined, undefined, name);
      }, 50);
    },
    [pushStatus, cancelSilenceTimer, setThinkingActiveSync, connect],
  );

  // Language switch — reconnect with the new `?lang=` so the server
  // drops the current OpenAI session and brings one up in the new
  // language. Cheaper than an in-session flip because the whole
  // session.update (tools, instructions, transcription_language) has
  // to change together. Idempotent: calling with the current language
  // just re-opens an already-healthy socket.
  const setLanguage = useCallback(
    (language: string) => {
      if (activeLanguageRef.current === language) return;
      activeLanguageRef.current = language;
      switchingRef.current = true;
      pushStatus(`Language \u2192 ${language}\u2026`);
      if (socketRef.current !== null) socketRef.current.close();
      setTimeout(() => {
        switchingRef.current = false;
        connect(undefined, language);
      }, 50);
    },
    [pushStatus, connect],
  );

  // ── PTT ─────────────────────────────────────────────────────────────────
  const pttStart = useCallback(() => {
    pttWasClaimHangupRef.current = inputModeRef.current === "skill_continuous";
    cancelSilenceTimer("ptt_start");
    setThinkingActiveSync(false);
    sendRaw({ type: "ptt_start" });
  }, [cancelSilenceTimer, setThinkingActiveSync, sendRaw]);

  const pttStop = useCallback(() => {
    sendRaw({ type: "ptt_stop" });
    if (pttWasClaimHangupRef.current) {
      pttWasClaimHangupRef.current = false;
      sendClientEvent("ptt_hangup_no_silence_timer");
      // Claim hangup — no listening turn, no audio coming; skip both timers.
    } else {
      // Normal PTT — model will respond; start visual thinking immediately,
      // and start the 1500 ms auditory tone timer separately.
      setThinkingActiveSync(true);
      startSilenceTimer("ptt_stop");
    }
  }, [sendRaw, sendClientEvent, setThinkingActiveSync, startSilenceTimer]);

  const wakeWord = useCallback(() => {
    cancelSilenceTimer("wake_word");
    sendRaw({ type: "wake_word" });
  }, [cancelSilenceTimer, sendRaw]);

  const reset = useCallback(() => {
    cancelSilenceTimer("reset");
    setThinkingActiveSync(false);
    setActiveStream(null);
    setActiveClaimTitle(null);
    setTranscript([]);
    setDevEvents([]);
    setStatusLog([]);
    sendRaw({ type: "reset" });
  }, [cancelSilenceTimer, setThinkingActiveSync, sendRaw]);

  // Clears the in-memory log buffers (status + dev events). Local-only —
  // does not touch the server session. Used by LogsSheet's "Clear" button.
  const clearLog = useCallback(() => {
    setStatusLog([]);
    setDevEvents([]);
  }, []);

  // ── Sessions (T1.12) ────────────────────────────────────────────────────
  const listSessions = useCallback(() => {
    sendRaw({ type: "list_sessions" });
  }, [sendRaw]);

  const getSession = useCallback(
    (id: number) => {
      sendRaw({ type: "get_session", id });
    },
    [sendRaw],
  );

  const deleteSession = useCallback(
    (id: number) => {
      sendRaw({ type: "delete_session", id });
    },
    [sendRaw],
  );

  // ── Skills (Marketplace v2 Phase A + B) ────────────────────────────────
  const requestSkillsState = useCallback(() => {
    sendRaw({ type: "get_skills_state" });
  }, [sendRaw]);

  // ── Marketplace (Marketplace v2 Phase C) ───────────────────────────────
  const requestMarketplace = useCallback(() => {
    sendRaw({ type: "get_marketplace" });
  }, [sendRaw]);

  // ── Install + restart (Marketplace v2 Phase D) ─────────────────────────
  const installSkill = useCallback(
    (pkg: string) => {
      // Optimistic local state — flips the UI to "starting" before
      // the server's `started` event arrives. The server's event
      // overwrites this within ~50ms unless validation fails (in
      // which case the `complete` event with ok=false replaces it).
      setInstallState({
        package: pkg,
        status: "starting",
        error_code: null,
        error_message: null,
        started_at_ms: Date.now(),
      });
      sendRaw({ type: "install_skill", package: pkg });
    },
    [sendRaw],
  );
  const restartServer = useCallback(() => {
    sendRaw({ type: "restart_server" });
  }, [sendRaw]);
  const clearInstallState = useCallback(() => {
    setInstallState(null);
  }, []);

  // Phase B writes. Each fires a fire-and-forget WS frame; the server
  // persists to disk + triggers a hot reload of the active persona,
  // which pushes a fresh skills_state frame. The PWA's reducer-style
  // dispatch catches that and re-renders the panel — no manual refresh
  // needed, no ack frame, no separate "saved" event.
  const setSkillEnabled = useCallback(
    (skill: string, enabled: boolean) => {
      sendRaw({ type: "set_skill_enabled", skill, enabled });
    },
    [sendRaw],
  );
  const setSkillConfig = useCallback(
    (skill: string, config: Record<string, unknown>) => {
      sendRaw({ type: "set_skill_config", skill, config });
    },
    [sendRaw],
  );
  const setSkillSecret = useCallback(
    (skill: string, key: string, value: string) => {
      sendRaw({ type: "set_skill_secret", skill, key, value });
    },
    [sendRaw],
  );
  const deleteSkillSecret = useCallback(
    (skill: string, key: string) => {
      sendRaw({ type: "delete_skill_secret", skill, key });
    },
    [sendRaw],
  );

  // ── Public API ───────────────────────────────────────────────────────────
  return {
    // State
    connected,
    appState,
    modelSpeaking,
    activeStream,
    inputMode,
    activeClaimId,
    activeClaimTitle,
    transcript,
    statusLog,
    devEvents,
    thinkingActive,
    sessionsList,
    sessionDetail,
    availablePersonas,
    currentPersona,
    skillsState,
    marketplaceState,
    installState,
    sessionReady,
    sessionError,
    get activeUrl() {
      return activeUrlRef.current;
    },

    // Connection
    connect,
    disconnect,
    selectPersona,
    setLanguage,
    pushStatus,

    // Protocol actions
    pttStart,
    pttStop,
    wakeWord,
    reset,
    clearLog,
    listSessions,
    getSession,
    deleteSession,
    requestSkillsState,
    requestMarketplace,
    installSkill,
    restartServer,
    clearInstallState,
    setSkillEnabled,
    setSkillConfig,
    setSkillSecret,
    deleteSkillSecret,
    sendAudio: (data: string) => sendRaw({ type: "audio", data }),
    sendClientEvent,

    // T1.21 — diagnostic surface. `requestDiag` triggers the server
    // to emit a `diag_state` reply (stashed in `lastServerDiagRef`).
    // `getDiagnostic()` composes everything we know — server state,
    // local frame log, ping RTT — into a single snapshot the panel
    // renders. Both are no-arg, side-effect-light: requestDiag fires
    // an outbound frame (logged in the WsLog), getDiagnostic just
    // reads refs.
    requestDiag: () => sendRaw({ type: "get_diag" }),
    getDiagnostic: (): {
      log: WsLogSnapshot;
      lastServerDiag: {
        receivedAtMs: number;
        payload: Record<string, unknown>;
      } | null;
      lastPingRttMs: number | null;
      pingMisses: number;
      pingPending: boolean;
      socketReadyState: number | null;
      activeUrl: string | null;
    } => ({
      log: wsLogRef.current?.snapshot() ?? {
        entries: [],
        connId: null,
        totals: {
          rx: 0,
          tx: 0,
          rxBytes: 0,
          txBytes: 0,
          rxByType: {},
          txByType: {},
        },
        lastRxAt: null,
        lastTxAt: null,
        lastOpenAt: null,
        lastCloseAt: null,
      },
      lastServerDiag: lastServerDiagRef.current,
      lastPingRttMs: lastPingRttMsRef.current,
      pingMisses: pingMissesRef.current,
      pingPending: pingPendingRef.current !== null,
      socketReadyState: socketRef.current?.readyState ?? null,
      activeUrl: activeUrlRef.current,
    }),
    clearDiagLog: () => wsLogRef.current?.clear(),

    // Audio callback wiring (call once in useEffect/onMount)
    setOnAudio: (fn: (data: string) => void) => {
      onAudioRef.current = fn;
    },
    setOnAudioClear: (fn: () => void) => {
      onAudioClearRef.current = fn;
    },
    setOnThinkingTone: (start: () => void, stop: () => void) => {
      onThinkingToneStartRef.current = start;
      onThinkingToneStopRef.current = stop;
    },
    setOnSetVolume: (fn: (level: number) => void) => {
      onSetVolumeRef.current = fn;
    },
    setOnStreamStarted: (fn: (prerollMs: number) => void) => {
      onStreamStartedRef.current = fn;
    },
    setOnModelSpeakingFalse: (fn: (done: () => void) => void) => {
      onModelSpeakingFalseRef.current = fn;
    },
  };
}

export type WsHandle = ReturnType<typeof useWs>;
