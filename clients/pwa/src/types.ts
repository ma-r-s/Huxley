// Core protocol types — must stay in sync with huxley's docs/protocol.md

export type AppState = "IDLE" | "CONNECTING" | "CONVERSING";
export type InputMode = "assistant_ptt" | "skill_continuous";

// Visual orb states derived from ws signals + local PTT state.
// 'wake'    — boot animation only, not a protocol signal
// 'paused'  — future (no server signal yet), kept for tweaks panel
// 'live'    — skill_continuous mode (active call)
// 'playing' — long-form audio stream (audiobook / radio); waveform visualizer
export type OrbState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "live"
  | "playing"
  | "error"
  | "wake"
  | "paused";

export interface TranscriptEntry {
  id: number;
  role: "user" | "assistant";
  text: string;
}

export interface StatusEntry {
  id: number;
  text: string;
  ts: string;
}

export interface DevEvent {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  ts: string;
}

// Persona summary as the server pushes it in the `hello` payload's
// `available_personas` array (T1.13). Field shape mirrors the wire
// shape exactly — `name` is the canonical id (the persona directory's
// basename, what `?persona=<name>` selects), `display_name` is the
// human-readable label (today same as `name` since `PersonaSpec`
// doesn't yet carry a separate display field), `language` is the
// persona's default language code.
//
// There is no `url` field anymore: post-T1.13 the runtime hosts
// every persona in one process, so the picker just selects by name
// and the WS reconnects with `?persona=<name>` against the same URL.
export interface PersonaEntry {
  name: string;
  display_name: string;
  language: string;
}

// Session history (T1.12). Field shape mirrors the wire shape from
// docs/protocol.md (snake_case) so we don't have to maintain a
// camelCase mapping in the parser. `started_at` etc. are raw ISO
// strings — components format relative time client-side.
export interface SessionMeta {
  id: number;
  started_at: string;
  ended_at: string | null;
  last_turn_at: string | null;
  turn_count: number;
  preview: string | null;
  summary: string | null;
}

export interface SessionTurn {
  idx: number;
  role: "user" | "assistant";
  text: string;
}

// Appearance preferences — persisted in localStorage.
export interface Appearance {
  accent: string;
  redHue: number;
  redChroma: number;
  redLight: number;
  expressiveness: number;
  fontPair: string;
  theme: "coral" | "dark" | "auto";
}

export const DEFAULT_APPEARANCE: Appearance = {
  accent: "coral",
  redHue: 23,
  redChroma: 0.19,
  redLight: 0.62,
  expressiveness: 1.0,
  fontPair: "instrument",
  theme: "coral",
};
