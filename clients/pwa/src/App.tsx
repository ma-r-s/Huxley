import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useWs } from "./lib/useWs.js";
import { MicCapture } from "./lib/audio/capture.js";
import { AudioPlayback } from "./lib/audio/playback.js";
import { deriveOrbState } from "./lib/orbState.js";
import { Orb } from "./components/Orb.js";
import { TranscriptDrawer } from "./components/TranscriptDrawer.js";
import { SessionsSheet } from "./components/SessionsSheet.js";
import { SessionDetailSheet } from "./components/SessionDetailSheet.js";
import { SkillConfigSheet } from "./components/SkillConfigSheet.js";
import { SkillsSheet } from "./components/SkillsSheet.js";
import { DeviceSheet } from "./components/DeviceSheet.js";
import { LogsSheet } from "./components/LogsSheet.js";
import { ClientEventPanel } from "./components/ClientEventPanel.js";
import { TweaksPanel } from "./components/TweaksPanel.js";
import type { OrbState, Appearance, AppState } from "./types.js";
import type { Tweaks } from "./components/TweaksPanel.js";
import { DEFAULT_APPEARANCE } from "./types.js";
import {
  SUPPORTED_LANGUAGES,
  type LanguageCode,
  saveLanguage,
} from "./i18n/index.js";

// T1.13: the runtime pushes `available_personas` + `current_persona`
// in every hello payload, so the PWA discovers personas at runtime.
// `VITE_HUXLEY_PERSONAS` is gone; one process now hosts every persona
// the directory ./personas/ contains. The picker reads
// `ws.availablePersonas` + `ws.currentPersona`; switching personas is
// `ws.selectPersona(name)` which closes the WS and reopens with
// `?persona=<name>` against the same URL. See docs/protocol.md.

// ── Appearance persistence ────────────────────────────────────────────────
const APPEARANCE_KEY = "huxley-appearance";
function loadAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(APPEARANCE_KEY);
    if (raw) return { ...DEFAULT_APPEARANCE, ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { ...DEFAULT_APPEARANCE };
}
function saveAppearance(a: Appearance) {
  try {
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify(a));
  } catch {
    /* ignore */
  }
}

// ── Singleton audio instances ─────────────────────────────────────────────
const mic = new MicCapture();
const playback = new AudioPlayback();

export function App() {
  const ws = useWs();
  const { t, i18n } = useTranslation();

  // ── PTT state ────────────────────────────────────────────────────────────
  const [pttHeld, setPttHeld] = useState(false);
  const [pttPendingStart, setPttPendingStart] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const pttHeldRef = useRef(false);
  const pttPendingRef = useRef(false);

  // ── UI state ─────────────────────────────────────────────────────────────
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  type SheetKind =
    | "sessions"
    | "session-detail"
    | "device"
    | "logs"
    | "skills"
    | "skill-config"
    | null;
  const [activeSheet, setActiveSheet] = useState<SheetKind>(null);
  // Sheet-mount fade-up animation should only run when entering the
  // sheet stack from the home view. Transitions BETWEEN sheets
  // (Skills → SkillConfig, Sessions → SessionDetail, etc.) skip the
  // animation because the new sheet's `opacity: 0` start would let
  // the previous sheet briefly show through. The ref tracks the
  // previous render's `activeSheet`; if the previous was non-null
  // and the current is non-null, we're transitioning sheet → sheet,
  // so suppress the animation. The ref updates AFTER render commits,
  // so during the very render where `activeSheet` flips null →
  // something, the ref still reads `null` (= animate).
  const prevActiveSheetRef = useRef<SheetKind>(null);
  const sheetClass =
    prevActiveSheetRef.current !== null && activeSheet !== null
      ? "hux-sheet hux-sheet-no-anim"
      : "hux-sheet";
  useEffect(() => {
    prevActiveSheetRef.current = activeSheet;
  }, [activeSheet]);
  // Marketplace v2 Phase A — which skill the user just tapped in
  // DeviceSheet's Skills section. Null when no detail sheet is open.
  // Reset when DeviceSheet closes so a future open doesn't reuse a
  // stale selection.
  const [activeSkillName, setActiveSkillName] = useState<string | null>(null);

  // Marketplace v2 Phase D — pending install confirmation. When the
  // user taps a Marketplace card, this holds the registry entry the
  // confirmation modal renders. Cleared on confirm (install fires)
  // or cancel (modal dismissed).
  const [pendingInstall, setPendingInstall] = useState<
    import("./types.js").MarketplaceEntry | null
  >(null);

  // Phase D — when an install completes successfully + the post-restart
  // reconnect lands, fetch fresh marketplace_state so the card flips
  // to "Installed ✓", then clear the install state. The detection:
  // installState.status === "success-restarting" + ws.connected goes
  // false (server os.execv) → true (reconnect).
  useEffect(() => {
    if (!ws.installState) return;
    if (ws.installState.status === "success-restarting" && ws.connected) {
      // Reconnected after restart. Refresh the registry feed (cache
      // was wiped by the restart) so the card shows installed=true.
      ws.requestMarketplace();
      ws.requestSkillsState();
      // Brief delay so the user sees "Installed ✓" land in the panel
      // before we clear the modal — feels more confirmatory.
      const t = setTimeout(() => ws.clearInstallState(), 600);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [ws.installState, ws.connected, ws]);
  const [booted, setBooted] = useState(false);
  const [bootOrbState, setBootOrbState] = useState<OrbState>("wake");

  // ── Appearance ───────────────────────────────────────────────────────────
  const [appearance, setAppearance] = useState<Appearance>(loadAppearance);
  const patchAppearance = useCallback((patch: Partial<Appearance>) => {
    setAppearance((prev) => {
      const next = { ...prev, ...patch };
      saveAppearance(next);
      return next;
    });
  }, []);

  // ── Tweaks (dev panel) ────────────────────────────────────────────────────
  const [tweaksOpen, setTweaksOpen] = useState(
    () =>
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).has("tweaks"),
  );

  // ── Client-event panel (dev) — fire arbitrary `client_event` from the
  // PWA for testing skill subscriptions registered via
  // `ctx.subscribe_client_event`. Bound to Shift+E (locked Stage-4 DoD).
  const [eventPanelOpen, setEventPanelOpen] = useState(false);
  const [tweaks, setTweaks] = useState<Tweaks>({
    redHue: appearance.redHue,
    redChroma: appearance.redChroma,
    redLight: appearance.redLight,
    expressiveness: appearance.expressiveness,
    fontPair: appearance.fontPair,
    theme: appearance.theme,
    accent: appearance.accent,
    demoState: null,
    deviceFrame: "auto",
  });

  // ── System dark mode ─────────────────────────────────────────────────────
  const [systemDark, setSystemDark] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-color-scheme: dark)").matches,
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const h = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);

  // ── Persona state ────────────────────────────────────────────────────────
  // Source of truth lives on the server: `ws.availablePersonas` is
  // the picker's list, `ws.currentPersona` is the active persona's
  // name (the chip text). Both arrive in every `hello` payload so
  // they refresh on (re)connect; null until the first hello.
  const personas = ws.availablePersonas ?? [];
  // Defensive fallback only matters for the boot window before the
  // first hello arrives. Once connected, `ws.currentPersona` is the
  // canonical answer.
  const selectedPersonaId = ws.currentPersona ?? personas[0]?.name ?? "abuelos";
  // Header chip + status messages should show the human-readable
  // display_name ("Basic", "Abuelo"), NOT the canonical id ("basic",
  // "abuelos") which is the picker's wire identity. Resolve via the
  // available_personas list; fall back to the id capitalized if the
  // hello hasn't arrived yet (boot window).
  const selectedPersonaLabel =
    personas.find((p) => p.name === selectedPersonaId)?.display_name ??
    ws.currentPersona ??
    "huxley";

  // ── Language state ───────────────────────────────────────────────────────
  // i18n.language is initialized from localStorage / navigator on module
  // import (see `src/i18n/index.ts`). Keep a reactive mirror so the
  // DeviceSheet's picker re-renders when the user flips languages and
  // so the WebSocket `?lang=<code>` reflects the current selection on
  // reconnect. `handleLanguagePick` persists + swaps both sides (client
  // UI catalog and server persona resolution) in one user action.
  const [language, setLanguageState] = useState<LanguageCode>(
    () => (i18n.language as LanguageCode) ?? "es",
  );
  const handleLanguagePick = useCallback(
    (code: LanguageCode) => {
      if (code === language) return;
      setLanguageState(code);
      saveLanguage(code);
      void i18n.changeLanguage(code);
      ws.setLanguage(code);
    },
    [language, i18n, ws],
  );

  // ── Sessions (T1.12) ─────────────────────────────────────────────────────
  // SessionsSheet pulls `ws.sessionsList` (fetched on mount via
  // `ws.listSessions()`); SessionDetailSheet pulls `ws.sessionDetail`
  // for the row identified by `activeSessionId`. Server is the source
  // of truth — no local cache beyond what useWs holds.
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);

  // ── Boot wake animation ───────────────────────────────────────────────────
  useEffect(() => {
    setBootOrbState("wake");
    const t = setTimeout(() => {
      setBootOrbState("idle");
      setBooted(true);
    }, 2400);
    return () => clearTimeout(t);
  }, []);

  // ── Audio wiring ──────────────────────────────────────────────────────────
  useEffect(() => {
    ws.setOnAudio((data) => playback.play(data));
    ws.setOnAudioClear(() => playback.stop());
    ws.setOnThinkingTone(
      () => playback.playThinkingTone(),
      () => playback.stopThinkingTone(),
    );
    ws.setOnSetVolume((level) => playback.setVolume(level));
    ws.setOnStreamStarted((prerollMs) => {
      if (prerollTimerRef.current !== null)
        clearTimeout(prerollTimerRef.current);
      prerollDoneRef.current = false;
      if (prerollMs <= 0) {
        prerollDoneRef.current = true;
      } else {
        prerollTimerRef.current = setTimeout(() => {
          prerollDoneRef.current = true;
          prerollTimerRef.current = null;
        }, prerollMs);
      }
    });
    ws.setOnModelSpeakingFalse((done) => {
      playback.onceIdle(done);
    });
    mic.onFrame = (data) => ws.sendAudio(data);
    // Open the WS with the user's currently-selected language. Server
    // resolves the persona itself per the locked rule (env >
    // single-persona autodiscovery > alphabetic-first-with-loud-log).
    // Subsequent language flips go through `ws.setLanguage()`;
    // persona swaps go through `ws.selectPersona(name)`. Both close
    // the WS and reopen with the relevant query param.
    ws.connect(undefined, language);
    return () => {
      ws.disconnect();
      mic.destroy();
      playback.destroy();
      if (prerollTimerRef.current !== null)
        clearTimeout(prerollTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Pre-roll gate: bars hidden until the earcon/intro has finished ────────
  // prerollDoneRef flips true after a setTimeout(prerollMs) started synchronously
  // inside onmessage. prerollMs is computed from the earcon byte length by the
  // skill and sent in stream_started, so it's exact — no magic numbers.
  const prerollDoneRef = useRef(false);
  const prerollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Error tone on unexpected session drop ─────────────────────────────────
  const prevAppStateRef = useRef<AppState>("IDLE");
  useEffect(() => {
    const prev = prevAppStateRef.current;
    if (
      (prev === "CONNECTING" || prev === "CONVERSING") &&
      ws.appState === "IDLE"
    ) {
      playback.playErrorTone();
    }
    prevAppStateRef.current = ws.appState;
  }, [ws.appState]);

  // ── Skill continuous mode: open mic unconditionally ───────────────────────
  useEffect(() => {
    if (ws.inputMode === "skill_continuous") {
      void (async () => {
        try {
          await mic.init();
          await mic.resume();
          mic.active = true;
        } catch {
          setMicError(t("mic.cannotOpen"));
        }
      })();
    } else if (
      ws.inputMode === "assistant_ptt" &&
      !pttHeldRef.current &&
      !pttPendingRef.current
    ) {
      mic.active = false;
    }
  }, [ws.inputMode]);

  // ── Keyb shortcut: Ctrl+Shift+T toggles tweaks ────────────────────────────
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === "T") setTweaksOpen((v) => !v);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // ── Keyb shortcut: Shift+E toggles the client-event dev panel ────────────
  // Looser binding than Ctrl+Shift+T (no Ctrl) because the panel is
  // explicitly aimed at quick testing during development. The PTT
  // keyboard handler at the bottom of this file ignores keypresses
  // when a panel is open, so opening this panel doesn't accidentally
  // fire PTT.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      // Ignore if focus is in a typeable element — let the user type
      // capital E into existing inputs without launching the panel.
      const target = e.target as HTMLElement | null;
      const isInput =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (isInput) return;
      if (
        e.shiftKey &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        e.key === "E"
      ) {
        e.preventDefault();
        setEventPanelOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // ── PTT logic ─────────────────────────────────────────────────────────────
  const activatePtt = useCallback(() => {
    setPttPendingStart(false);
    pttPendingRef.current = false;
    mic.active = true;
    setPttHeld(true);
    pttHeldRef.current = true;
    playback.playTone();
    ws.pttStart();
  }, [ws]);

  // Activate PTT as soon as CONVERSING is reached (pending start)
  useEffect(() => {
    if (pttPendingRef.current && ws.appState === "CONVERSING") {
      activatePtt();
    }
  }, [ws.appState, activatePtt]);

  const pressPtt = useCallback(async () => {
    if (!ws.connected) return;
    if (pttHeldRef.current || pttPendingRef.current) return;
    setMicError(null);
    try {
      await mic.init();
      await mic.resume();
      await playback.resume();
    } catch {
      setMicError(t("mic.accessDenied"));
      return;
    }
    // T1.13: preload the persona-swap earcon now that the AudioContext
    // is unlocked — first user PTT is the earliest point we can fetch +
    // decodeAudioData. Idempotent + non-blocking; later swaps play
    // instantly. Failure is logged inside playback and ignored — earcon
    // is UX polish, not load-bearing.
    void playback.preloadPersonaSwap();
    playback.stop();
    switch (ws.appState) {
      case "CONVERSING":
        activatePtt();
        break;
      case "IDLE":
        setPttPendingStart(true);
        pttPendingRef.current = true;
        setPttHeld(true);
        pttHeldRef.current = true;
        ws.wakeWord();
        break;
      case "CONNECTING":
        setPttPendingStart(true);
        pttPendingRef.current = true;
        setPttHeld(true);
        pttHeldRef.current = true;
        break;
    }
  }, [ws, activatePtt]);

  const releasePtt = useCallback(() => {
    if (!pttHeldRef.current && !pttPendingRef.current) return;
    if (pttPendingRef.current && !mic.active) {
      setPttPendingStart(false);
      pttPendingRef.current = false;
      setPttHeld(false);
      pttHeldRef.current = false;
      return;
    }
    mic.active = false;
    setPttHeld(false);
    pttHeldRef.current = false;
    ws.pttStop();
  }, [ws]);

  // Pointer events (orb touch/click)
  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (activeSheet || tweaksOpen || eventPanelOpen) return;
      if (!booted) return;
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      void pressPtt();
    },
    [activeSheet, tweaksOpen, eventPanelOpen, booted, pressPtt],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      releasePtt();
    },
    [releasePtt],
  );

  // Window-level pointer release fallback. iOS Safari (especially in PWA
  // standalone) occasionally drops pointer capture mid-press — if that
  // happens, the orb's own onPointerUp won't fire when the user lifts their
  // finger. Listening on the window while PTT is engaged guarantees release.
  useEffect(() => {
    if (!pttHeld && !pttPendingStart) return;
    const release = () => releasePtt();
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
    };
  }, [pttHeld, pttPendingStart, releasePtt]);

  // Spacebar
  useEffect(() => {
    const isFormEl = (t: EventTarget | null) => {
      if (!(t instanceof HTMLElement)) return false;
      return (
        ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) ||
        t.isContentEditable
      );
    };
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || isFormEl(e.target)) return;
      if (activeSheet || tweaksOpen || eventPanelOpen) return;
      e.preventDefault();
      void pressPtt();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || isFormEl(e.target)) return;
      e.preventDefault();
      releasePtt();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [activeSheet, tweaksOpen, eventPanelOpen, pressPtt, releasePtt]);

  // ── Orb state derivation ──────────────────────────────────────────────────
  const liveOrbState: OrbState = booted
    ? deriveOrbState({
        connected: ws.connected,
        appState: ws.appState,
        inputMode: ws.inputMode,
        modelSpeaking: ws.modelSpeaking,
        activeStream: ws.activeStream !== null,
        thinkingActive: ws.thinkingActive,
        pttHeld,
        pttPendingStart,
      })
    : bootOrbState;

  // Tweaks demo-state override (dev panel only)
  const effectiveOrbState: OrbState = tweaks.demoState ?? liveOrbState;

  // ── CSS vars from appearance ───────────────────────────────────────────────
  const eff = { ...appearance, ...tweaks }; // tweaks take precedence for live editing
  const isDark = eff.theme === "dark" || (eff.theme === "auto" && systemDark);
  const bgCss = isDark
    ? "oklch(0.18 0.04 30)"
    : `oklch(${eff.redLight} ${eff.redChroma} ${eff.redHue})`;
  const fgCss = isDark ? "oklch(0.95 0.01 30)" : "oklch(0.985 0.005 50)";

  const fonts = (() => {
    switch (eff.fontPair) {
      case "fraunces":
        return {
          serif: '"Fraunces", Georgia, serif',
          sans: '"Inter Tight", system-ui, sans-serif',
        };
      case "all-sans":
        return {
          serif: '"Inter Tight", system-ui, sans-serif',
          sans: '"Inter Tight", system-ui, sans-serif',
        };
      case "mono":
        return {
          serif: '"JetBrains Mono", monospace',
          sans: '"JetBrains Mono", monospace',
        };
      default:
        return {
          serif: '"Instrument Serif", Georgia, serif',
          sans: '"Inter Tight", system-ui, sans-serif',
        };
    }
  })();

  const cssVars: Record<string, string> = {
    "--hux-bg": bgCss,
    "--hux-fg": fgCss,
    "--hux-fg-dim": "color-mix(in oklab, var(--hux-fg) 65%, transparent)",
    "--hux-fg-line": "color-mix(in oklab, var(--hux-fg) 22%, transparent)",
    "--hux-fg-faint": "color-mix(in oklab, var(--hux-fg) 10%, transparent)",
    "--hux-serif": fonts.serif,
    "--hux-sans": fonts.sans,
    "--hux-mono": '"JetBrains Mono", "SF Mono", monospace',
  };
  // Apply the theme vars to :root so html itself can paint the background —
  // `position: fixed` on .hux-root gets clipped out of the iOS PWA safe-area
  // strip, so painting from html guarantees the gradient reaches under the
  // Dynamic Island / notch.
  useEffect(() => {
    const el = document.documentElement;
    for (const [k, v] of Object.entries(cssVars)) el.style.setProperty(k, v);
  }, [cssVars]);

  // ── Clock ─────────────────────────────────────────────────────────────────
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);
  const timeStr = now.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });

  // ── Status label ─────────────────────────────────────────────────────────
  // "playing" shows the stream's label if the server provided one.
  // "live" (active claim — call) shows the claim's title localized via
  // `call.talkingWith` — e.g. "Hablando con Mario" / "Talking with Mario".
  const liveLabel =
    effectiveOrbState === "live" && ws.activeClaimTitle
      ? t("call.talkingWith", { who: ws.activeClaimTitle })
      : null;
  const statusLabel =
    liveLabel ??
    (effectiveOrbState === "playing" && ws.activeStream?.label
      ? ws.activeStream.label
      : t(`orbStatus.${effectiveOrbState}`));

  // ── Connection display ────────────────────────────────────────────────────
  const deviceHost = (() => {
    const raw =
      (ws.activeUrl ?? "")
        .replace(/^wss?:\/\//, "")
        .replace(/\/.*$/, "")
        .replace(/:\d+$/, "") || "localhost";
    // Show the first DNS label only; full URL is in the tooltip/device sheet.
    const first = raw.split(".")[0];
    return first || raw;
  })();
  const connLabel = ws.connected ? deviceHost : t("header.connRetrying");

  // ── Persona switch ────────────────────────────────────────────────────────
  // T1.13: tap the picker → play the swap earcon → close current WS →
  // reopen with the new `?persona=<name>` against the same URL. The
  // server's swap algorithm (build-new → start → atomic ref-swap →
  // background teardown of old) runs as part of the new connection's
  // handshake. The earcon bridges the brief silence; status text +
  // chip update on hello arrival confirm the swap landed.
  const handlePersonaPick = useCallback(
    (name: string) => {
      playback.playPersonaSwap();
      ws.selectPersona(name);
      setActiveSheet(null);
    },
    [ws],
  );

  // ── Orb scale when transcript is open ────────────────────────────────────
  const orbScale = transcriptOpen ? 0.62 : 1;

  return (
    <div className="hux-root">
      <div className="hux-stage">
        {/* Top chrome */}
        <header className="hux-topbar">
          <button
            className="hux-chip"
            onClick={() => setActiveSheet("sessions")}
          >
            <span className="hux-chip-dot" /> {t("header.sessions")}
          </button>
          <div className="hux-brand">
            <div className="hux-wordmark">huxley</div>
          </div>
          <button className="hux-chip" onClick={() => setActiveSheet("device")}>
            {selectedPersonaLabel}
            <span className="hux-chip-arrow">{"\u203a"}</span>
          </button>
        </header>
        <div className="hux-subbar" title={ws.activeUrl ?? undefined}>
          <span className={`hux-conn-dot ${ws.connected ? "on" : "off"}`} />
          <span className="hux-conn-host">{connLabel}</span>
          <span className="hux-conn-sep">{"\u00b7"}</span>
          <span className="hux-conn-time">{timeStr}</span>
        </div>

        {/* Orb hero */}
        <main className="hux-hero">
          <div
            className="hux-orb-wrap"
            style={{
              transform: `scale(${orbScale})`,
              transition: "transform 700ms cubic-bezier(.22,.9,.27,1)",
            }}
          >
            <Orb
              state={effectiveOrbState}
              size={320}
              color={fgCss}
              expressiveness={eff.expressiveness}
              pressed={pttHeld || effectiveOrbState === "listening"}
              onPointerDown={handlePointerDown}
              onPointerUp={handlePointerUp}
              getPlaybackBands={() => playback.getFrequencyData(32)}
              isPrerollDone={() => prerollDoneRef.current}
            />
          </div>

          <div className="hux-status" key={effectiveOrbState}>
            <div className="hux-status-line">{statusLabel}</div>
            {effectiveOrbState === "idle" && booted && (
              <div className="hux-hint">{t("orbHint")}</div>
            )}
            {micError && (
              <div
                className="hux-hint"
                style={{ color: "var(--hux-fg)", opacity: 0.9 }}
              >
                {micError}
              </div>
            )}
          </div>
        </main>

        {/* Transcript drawer */}
        <TranscriptDrawer
          messages={ws.transcript}
          partial={null}
          expanded={transcriptOpen}
          onToggle={() => setTranscriptOpen((v) => !v)}
        />

        {/* Sheets */}
        {activeSheet === "sessions" && (
          <SessionsSheet
            onClose={() => setActiveSheet(null)}
            sessions={ws.sessionsList}
            onPick={(id) => {
              setActiveSessionId(id);
              setActiveSheet("session-detail");
            }}
            onMount={ws.listSessions}
            sheetClassName={sheetClass}
          />
        )}
        {activeSheet === "session-detail" && activeSessionId !== null && (
          <SessionDetailSheet
            onClose={() => {
              setActiveSheet("sessions");
              setActiveSessionId(null);
            }}
            sessionId={activeSessionId}
            detail={ws.sessionDetail}
            onMount={() => ws.getSession(activeSessionId)}
            onDelete={() => {
              ws.deleteSession(activeSessionId);
              setActiveSheet("sessions");
              setActiveSessionId(null);
            }}
            sheetClassName={sheetClass}
          />
        )}
        {activeSheet === "device" && (
          <DeviceSheet
            onClose={() => setActiveSheet(null)}
            device={{
              connected: ws.connected,
              url: ws.activeUrl ?? "localhost:8765",
              persona: selectedPersonaId,
              personas,
            }}
            onPersonaPick={handlePersonaPick}
            personaPickerDisabled={
              ws.activeClaimId !== null || ws.activeStream !== null
            }
            language={language}
            supportedLanguages={SUPPORTED_LANGUAGES}
            onLanguagePick={handleLanguagePick}
            appearance={appearance}
            onAppearance={(patch) => {
              patchAppearance(patch as Partial<Appearance>);
              setTweaks((tw) => ({ ...tw, ...patch }));
            }}
            onReload={() => ws.sendClientEvent("ui.reload_skills")}
            onRestart={() => ws.restartServer()}
            onViewLogs={() => setActiveSheet("logs")}
            skillsState={ws.skillsState}
            onRequestSkillsState={ws.requestSkillsState}
            onOpenSkills={() => setActiveSheet("skills")}
            sheetClassName={sheetClass}
          />
        )}
        {activeSheet === "skills" && (
          <SkillsSheet
            skillsState={ws.skillsState}
            marketplaceState={ws.marketplaceState}
            onClose={() => setActiveSheet("device")}
            onPickSkill={(skill) => {
              setActiveSkillName(skill.name);
              setActiveSheet("skill-config");
            }}
            onPickMarketplaceSkill={(entry) => {
              // Phase D — install flow. Tapping a card opens a
              // confirmation modal (handled below); from there the
              // user can read the upstream README link, then click
              // Install. The install flow goes through `ws.installSkill`
              // which sends the WS frame and tracks state in
              // `ws.installState`.
              if (entry.installed) return; // already installed; nothing to do
              // Match the server's tightened regex exactly (Phase D
              // critic §8): no leading hyphen after the prefix, no
              // double-hyphens at the head. Out-of-sync regexes meant
              // the PWA would optimistically flash "Installing…" for
              // names the server immediately rejected.
              if (!/^huxley-skill-[a-z0-9][a-z0-9-]*$/.test(entry.name)) return;
              setPendingInstall(entry);
            }}
            onRequestSkillsState={ws.requestSkillsState}
            onRequestMarketplace={ws.requestMarketplace}
            sheetClassName={sheetClass}
          />
        )}
        {activeSheet === "skill-config" &&
          ws.skillsState &&
          activeSkillName !== null &&
          (() => {
            const skill = ws.skillsState.skills.find(
              (s) => s.name === activeSkillName,
            );
            if (!skill) {
              // Skill vanished mid-flight (e.g. uninstalled in Phase D).
              // Drop back to the SkillsSheet rather than render stale.
              setActiveSheet("skills");
              setActiveSkillName(null);
              return null;
            }
            return (
              <SkillConfigSheet
                skill={skill}
                onClose={() => {
                  setActiveSheet("skills");
                  setActiveSkillName(null);
                }}
                onSetEnabled={ws.setSkillEnabled}
                onSetConfig={ws.setSkillConfig}
                onSetSecret={ws.setSkillSecret}
                onDeleteSecret={ws.deleteSkillSecret}
                writesDisabled={
                  ws.activeClaimId !== null || ws.activeStream !== null
                }
                writesDisabledHint={t("skills.writesDisabledHint")}
                sheetClassName={sheetClass}
              />
            );
          })()}
        {activeSheet === "logs" && (
          <LogsSheet
            onClose={() => setActiveSheet(null)}
            statusLog={ws.statusLog}
            devEvents={ws.devEvents}
            onClear={ws.clearLog}
            sheetClassName={sheetClass}
          />
        )}
      </div>

      {/* Tweaks panel (dev) */}
      {tweaksOpen && (
        <TweaksPanel
          tweaks={tweaks}
          onChange={(patch) => setTweaks((t) => ({ ...t, ...patch }))}
          onClose={() => setTweaksOpen(false)}
        />
      )}

      {/* Client-event dev panel (Shift+E). Fires arbitrary
          client_event messages — useful for testing skill subscriptions
          registered via ctx.subscribe_client_event. Incoming server_event
          messages already surface in the existing dev-event log via
          useWs.ts's `server_event:<key>` push. */}
      {eventPanelOpen && (
        <ClientEventPanel
          onClose={() => setEventPanelOpen(false)}
          onSend={(event, data) => {
            ws.sendClientEvent(event, data);
            setEventPanelOpen(false);
          }}
        />
      )}

      {/* Marketplace v2 Phase D — install flow overlays. Sit on top of
          every sheet at zIndex 100 so they're never visually covered. */}
      {pendingInstall !== null && ws.installState === null && (
        <InstallConfirmModal
          entry={pendingInstall}
          onConfirm={() => {
            ws.installSkill(pendingInstall.name);
            setPendingInstall(null);
          }}
          onCancel={() => setPendingInstall(null)}
        />
      )}
      {ws.installState !== null && (
        <InstallProgressOverlay
          state={ws.installState}
          connected={ws.connected}
          onDismiss={ws.clearInstallState}
        />
      )}
    </div>
  );
}

// ── Marketplace v2 Phase D — install confirmation modal ──────────────────

interface InstallConfirmModalProps {
  entry: import("./types.js").MarketplaceEntry;
  onConfirm: () => void;
  onCancel: () => void;
}

function InstallConfirmModal({
  entry,
  onConfirm,
  onCancel,
}: InstallConfirmModalProps) {
  const { t } = useTranslation();
  // Esc to dismiss — Phase D post-impl critic §12. Skipped on the
  // progress overlay (intentional; install in flight isn't cancelable).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="install-confirm-title"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--hux-bg)",
          color: "var(--hux-fg)",
          border: "1px solid var(--hux-fg-line)",
          borderRadius: 14,
          padding: "28px 28px 20px",
          maxWidth: 480,
          width: "100%",
          fontFamily: "var(--hux-sans)",
          fontSize: 15,
          lineHeight: 1.4,
          boxShadow: "0 12px 48px rgba(0,0,0,0.4)",
        }}
      >
        <h3
          id="install-confirm-title"
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: 26,
            lineHeight: 1.1,
            margin: "0 0 8px",
          }}
        >
          {t("install.confirmTitle", "Install {{name}}?").replace(
            "{{name}}",
            entry.display_name ?? entry.name,
          )}
        </h3>
        <p style={{ margin: "0 0 16px", color: "var(--hux-fg-dim)" }}>
          {entry.tagline ?? t("install.noTagline", "No description provided.")}
        </p>
        <p
          style={{
            margin: "0 0 16px",
            fontSize: 13,
            color: "var(--hux-fg-dim)",
            lineHeight: 1.5,
          }}
        >
          {t(
            "install.warning",
            "This will run `uv add {{pkg}}` and restart the server. Any active call or stream is preserved (mid-call installs are blocked).",
          ).replace("{{pkg}}", entry.name)}
        </p>
        <div
          style={{
            display: "flex",
            gap: 10,
            justifyContent: "flex-end",
            marginTop: 24,
          }}
        >
          <button
            style={{
              background: "transparent",
              border: "1px solid var(--hux-fg-line)",
              color: "var(--hux-fg)",
              padding: "8px 16px",
              borderRadius: 999,
              fontFamily: "var(--hux-sans)",
              fontSize: 13,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              cursor: "pointer",
            }}
            onClick={onCancel}
          >
            {t("install.cancel", "Cancel")}
          </button>
          <button
            style={{
              background: "var(--hux-fg)",
              border: "1px solid var(--hux-fg)",
              color: "var(--hux-bg)",
              padding: "8px 18px",
              borderRadius: 999,
              fontFamily: "var(--hux-sans)",
              fontSize: 13,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              cursor: "pointer",
              fontWeight: 600,
            }}
            onClick={onConfirm}
          >
            {t("install.install", "Install")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Marketplace v2 Phase D — install progress overlay ───────────────────

interface InstallProgressOverlayProps {
  state: import("./types.js").InstallUIState;
  connected: boolean;
  onDismiss: () => void;
}

function InstallProgressOverlay({
  state,
  connected,
  onDismiss,
}: InstallProgressOverlayProps) {
  const { t } = useTranslation();
  // Force re-render every second so the elapsed counter ticks.
  // Phase D post-impl critic §7. Only when `running` — other states
  // are terminal-ish and don't need the tick.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (state.status !== "running") return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [state.status]);

  // Watchdog for the post-execv reconnect window. If the server
  // fails to come back up (broken venv, launchd respawn loop), the
  // PWA would otherwise sit on "Restarting…" forever. After 30s we
  // surface a recoverable error state. Phase D post-impl critic §4.
  const [restartTimedOut, setRestartTimedOut] = useState(false);
  useEffect(() => {
    if (state.status !== "success-restarting") {
      setRestartTimedOut(false);
      return undefined;
    }
    if (connected) return undefined; // reconnected; no watchdog needed
    const id = setTimeout(() => setRestartTimedOut(true), 30_000);
    return () => clearTimeout(id);
  }, [state.status, connected]);

  // Status copy varies by phase. The "success-restarting + !connected"
  // state is the post-execv reconnect window.
  let title: string;
  let body: string;
  let canDismiss = false;
  if (restartTimedOut) {
    title = t("install.restartTimedOutTitle", "Server didn't come back");
    body = t(
      "install.restartTimedOutBody",
      "The install completed but the server hasn't responded in 30 seconds. Check the server log (~/Library/Logs/Huxley/huxley.log) — the new skill may have a broken setup.",
    );
    canDismiss = true;
  } else if (state.status === "starting" || state.status === "running") {
    title = t("install.installing", "Installing {{pkg}}…").replace(
      "{{pkg}}",
      state.package,
    );
    body = t(
      "install.installingBody",
      "Running `uv add`. This may take a minute on first install (C-extension wheels build from source on slower machines).",
    );
  } else if (state.status === "success-restarting") {
    title = connected
      ? t("install.installedTitle", "Installed ✓")
      : t("install.restartingTitle", "Restarting server…");
    body = connected
      ? t(
          "install.installedBody",
          "{{pkg}} is now available. Open the Skills tab to enable it on this persona.",
        ).replace("{{pkg}}", state.package)
      : t(
          "install.restartingBody",
          "The server is replacing itself with a fresh interpreter so the new skill's entry point is visible. ~5 seconds.",
        );
    canDismiss = connected;
  } else {
    // error
    title = t("install.errorTitle", "Install failed");
    body = state.error_message
      ? state.error_message
      : t(
          "install.errorGeneric",
          "uv add returned an error. Check the server log.",
        );
    canDismiss = true;
  }
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-live="polite"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        style={{
          background: "var(--hux-bg)",
          color: "var(--hux-fg)",
          border: "1px solid var(--hux-fg-line)",
          borderRadius: 14,
          padding: "28px 28px 20px",
          maxWidth: 480,
          width: "100%",
          fontFamily: "var(--hux-sans)",
          fontSize: 15,
          lineHeight: 1.4,
          boxShadow: "0 12px 48px rgba(0,0,0,0.4)",
        }}
      >
        <h3
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: 26,
            lineHeight: 1.1,
            margin: "0 0 12px",
          }}
        >
          {title}
        </h3>
        <p style={{ margin: "0 0 8px", color: "var(--hux-fg-dim)" }}>{body}</p>
        {state.status === "running" && (
          <div
            style={{
              fontSize: 12,
              color: "var(--hux-fg-dim)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {Math.floor((Date.now() - state.started_at_ms) / 1000)}s
          </div>
        )}
        {canDismiss && (
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              marginTop: 24,
            }}
          >
            <button
              style={{
                background: "transparent",
                border: "1px solid var(--hux-fg-line)",
                color: "var(--hux-fg)",
                padding: "8px 18px",
                borderRadius: 999,
                fontFamily: "var(--hux-sans)",
                fontSize: 13,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                cursor: "pointer",
              }}
              onClick={onDismiss}
            >
              {t("install.dismiss", "Done")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
