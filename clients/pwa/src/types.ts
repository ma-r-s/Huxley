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

// Marketplace v2 Phase A — DeviceSheet's Skills section. Shape mirrors
// the `skills_state` wire frame from docs/protocol.md.
//
// `name` is the entry-point key (`stocks`, `audiobooks`, ...); the
// `package` is the PyPI dist name (`huxley-skill-stocks`). `enabled`
// is whether the active persona's `skills:` block lists this skill.
// `current_config` is the per-skill block from persona.yaml — empty
// dict for skills that aren't enabled. `secret_keys_set` lists the
// keys present in `<persona>/data/secrets/<skill>/values.json` (no
// values, never on the wire); `secret_required_keys` is derived from
// `config_schema` properties whose `format` is `"secret"` — used to
// compute "missing required secret" UI affordances.
export interface SkillSummary {
  name: string;
  package: string | null;
  version: string | null;
  description: string | null;
  author: string | null;
  enabled: boolean;
  config_schema: JsonSchema | null;
  data_schema_version: number;
  current_config: Record<string, unknown>;
  secret_keys_set: string[];
  secret_required_keys: string[];
}

export interface SkillsState {
  persona: string | null;
  skills: SkillSummary[];
}

// Marketplace v2 Phase C — registry feed entry. Mirrors the schema
// at https://github.com/ma-r-s/huxley-registry/blob/main/schema.json
// (index.json shape) plus the runtime-decorated `installed: bool`
// field. We pass through every upstream field so the PWA can
// surface new ones without a wire-protocol bump (forward-compat).
export interface MarketplaceEntry {
  namespace?: string;
  name: string;
  display_name?: string;
  tagline?: string;
  version?: string;
  tier?: "first-party" | "community" | "experimental";
  categories?: string[];
  config_schema_present?: boolean;
  platforms?: string[];
  detail?: string;
  installed: boolean;
  // Forward-compat: unknown fields ride through.
  [key: string]: unknown;
}

export interface MarketplaceState {
  skills: MarketplaceEntry[];
  registry_version: string | null;
  generated_at: string | null;
  fetched_at_ms: number;
  stale: boolean;
  error: string | null;
}

// Marketplace v2 Phase D — install lifecycle frame.
// Server emits exactly two events per install: `started` (right after
// the regex/concurrency gates pass, before the subprocess runs) and
// `complete` (after `uv add` returns). On success, `restart_required`
// is true and the server immediately initiates the restart sequence —
// the WS will close, the PWA's existing reconnect logic kicks in.
export interface InstallEvent {
  kind: "started" | "complete";
  package: string;
  ok: boolean | null;
  error_code: string | null;
  error_message: string | null;
  restart_required: boolean;
}

// Local UI state for the install flow. Held in `useWs` and threaded
// through to the MarketplaceCard / detail view that initiated it.
export interface InstallUIState {
  package: string;
  status: "starting" | "running" | "success-restarting" | "error";
  error_code: string | null;
  error_message: string | null;
  started_at_ms: number;
}

// Minimal subset of JSON Schema 2020-12 the form renderer recognizes.
// Server validates that schemas conform; this is just enough to walk
// the tree and decide what input element to render. Anything we don't
// handle falls through to a debug-only "raw" view in dev builds.
export interface JsonSchema {
  type?: string | string[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  default?: unknown;
  description?: string;
  format?: string;
  minimum?: number;
  maximum?: number;
  // Two custom Huxley extensions — see docs/skill-marketplace.md
  // § Config schema convention.
  "x-huxley:help"?: string;
  // (Cannot use `format: "secret"` as a constraint on the type system
  // since `format` is open-ended in JSON Schema; we just match by string.)
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
