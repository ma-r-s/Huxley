// WebSocket hook — React port of the huxley wire protocol.
// Mirrors ws.svelte.ts logic; uses refs for callback-readable state to avoid
// stale closures, useState for render-triggering state.

import { useState, useRef, useCallback } from "react";
import type {
  AppState,
  InputMode,
  TranscriptEntry,
  StatusEntry,
  DevEvent,
  SessionMeta,
  SessionTurn,
} from "../types.js";

const EXPECTED_PROTOCOL = 2;

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
  | { type: "hello"; protocol: number }
  | { type: "audio"; data: string }
  | { type: "audio_clear" }
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
  | { type: "session_deleted"; id: number };

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

  // ── Refs (callback-readable without stale closures) ─────────────────────
  const socketRef = useRef<WebSocket | null>(null);
  const activeUrlRef = useRef<string | null>(null);
  const switchingRef = useRef(false);
  // Set to true on intentional disconnect (unmount / persona switch) so the
  // onclose handler doesn't schedule a reconnect.
  const noReconnectRef = useRef(false);

  // Mirrors of state vars that callbacks need to read synchronously
  const inputModeRef = useRef<InputMode>("assistant_ptt");
  const thinkingActiveRef = useRef(false);

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
      socketRef.current.send(JSON.stringify(msg));
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
  // `language` is appended to the base URL as `?lang=<code>` so the
  // server can resolve the persona for that language BEFORE the session
  // is established. The base URL stashed in `activeUrlRef` is WITHOUT
  // the query string (so a persona switch that keeps the same language
  // compares cleanly), and the lang is tracked separately so
  // `setLanguage` can rebuild the full URL without parsing.
  const activeLanguageRef = useRef<string | null>(null);
  const connect = useCallback(
    (url?: string, language?: string | null) => {
      // An explicit connect() call (initial mount, persona switch, or StrictMode
      // remount) always re-enables auto-reconnect.
      noReconnectRef.current = false;
      activeUrlRef.current = url ?? activeUrlRef.current ?? defaultWsUrl();
      if (language !== undefined) activeLanguageRef.current = language;
      // Don't open a second socket if one is already live or connecting.
      // This prevents StrictMode's second mount from racing the first.
      const rs = socketRef.current?.readyState;
      if (rs === WebSocket.CONNECTING || rs === WebSocket.OPEN) return;
      const full = activeLanguageRef.current
        ? `${activeUrlRef.current}${activeUrlRef.current.includes("?") ? "&" : "?"}lang=${encodeURIComponent(activeLanguageRef.current)}`
        : activeUrlRef.current;
      const ws = new WebSocket(full);

      ws.onopen = () => {
        if (socketRef.current !== ws) return; // stale socket
        setConnected(true);
        pushStatus(`Connected to ${activeUrlRef.current}`);
      };

      ws.onclose = () => {
        if (socketRef.current !== ws) return; // stale socket — don't clobber live connection
        setConnected(false);
        socketRef.current = null;
        cancelSilenceTimer("socket_close");
        setThinkingActiveSync(false);
        setActiveStream(null);
        setActiveClaimTitle(null);
        if (switchingRef.current || noReconnectRef.current) return;
        pushStatus("Disconnected — retrying in 2s\u2026");
        setTimeout(() => connect(), 2000);
      };

      ws.onmessage = (ev) => {
        if (socketRef.current !== ws) return; // stale socket
        try {
          const msg = JSON.parse(ev.data as string) as ServerMessage;
          switch (msg.type) {
            case "hello":
              if (msg.protocol !== EXPECTED_PROTOCOL) {
                pushStatus(
                  `Protocol mismatch: server=${msg.protocol} client=${EXPECTED_PROTOCOL}`,
                );
                ws.close(1002, "Protocol version mismatch");
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

  const switchPersona = useCallback(
    (url: string) => {
      if (url === activeUrlRef.current && socketRef.current !== null) return;
      pushStatus(`Switching to ${url}\u2026`);
      switchingRef.current = true;
      setAppState("IDLE");
      setTranscript([]);
      setDevEvents([]);
      setStatusLog((prev) => prev.slice(0, 5));
      setActiveStream(null);
      setActiveClaimTitle(null);
      cancelSilenceTimer("persona_switch");
      setThinkingActiveSync(false);
      if (socketRef.current !== null) socketRef.current.close();
      activeUrlRef.current = url;
      setTimeout(() => {
        switchingRef.current = false;
        connect(url);
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
    get activeUrl() {
      return activeUrlRef.current;
    },

    // Connection
    connect,
    disconnect,
    switchPersona,
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
    sendAudio: (data: string) => sendRaw({ type: "audio", data }),
    sendClientEvent,

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
