// Orb state machine — single source of truth for visual state derivation.
// Priority-ordered: first matching condition wins.

import type { OrbState, AppState, InputMode } from "../types.js";

export function deriveOrbState(opts: {
  connected: boolean;
  appState: AppState;
  inputMode: InputMode;
  modelSpeaking: boolean;
  // true when the server has sent stream_started and not yet stream_ended;
  // drives the "playing" orb state + waveform visualizer
  activeStream: boolean;
  thinkingActive: boolean; // immediate post-ptt_stop, separate from 1500ms auditory timer
  pttHeld: boolean;
  pttPendingStart: boolean;
}): OrbState {
  if (!opts.connected) return "error";
  if (opts.inputMode === "skill_continuous") return "live";
  if (opts.activeStream) return "playing";
  if (opts.modelSpeaking) return "speaking";
  if (opts.thinkingActive) return "thinking";
  if (opts.pttHeld || opts.pttPendingStart) return "listening";
  return "idle";
}
