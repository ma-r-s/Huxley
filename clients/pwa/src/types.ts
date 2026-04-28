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

export interface PersonaEntry {
  id: string;
  name: string;
  url: string;
  lang?: string;
  desc?: string;
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
