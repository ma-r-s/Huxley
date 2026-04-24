import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useWs } from "./lib/useWs.js";
import { MicCapture } from "./lib/audio/capture.js";
import { AudioPlayback } from "./lib/audio/playback.js";
import { deriveOrbState } from "./lib/orbState.js";
import { Orb } from "./components/Orb.js";
import { TranscriptDrawer } from "./components/TranscriptDrawer.js";
import { SessionsSheet } from "./components/SessionsSheet.js";
import { DeviceSheet } from "./components/DeviceSheet.js";
import { TweaksPanel } from "./components/TweaksPanel.js";
import type { OrbState, Appearance, PersonaEntry, AppState } from "./types.js";
import type { Tweaks } from "./components/TweaksPanel.js";
import { DEFAULT_APPEARANCE } from "./types.js";
import {
  SUPPORTED_LANGUAGES,
  type LanguageCode,
  saveLanguage,
} from "./i18n/index.js";

// ── Persona list from env ─────────────────────────────────────────────────
function parsePersonas(): PersonaEntry[] {
  const raw =
    (import.meta.env["VITE_HUXLEY_PERSONAS"] as string | undefined) ?? "";
  const fallback: PersonaEntry[] = [
    {
      id: "abuelos",
      name: "abuelos",
      url: `ws://${typeof window === "undefined" ? "localhost" : window.location.hostname}:8765`,
    },
  ];
  if (!raw.trim()) return fallback;
  const entries = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((pair) => {
      const idx = pair.indexOf(":");
      if (idx === -1) return null;
      const name = pair.slice(0, idx).trim();
      const url = pair.slice(idx + 1).trim();
      return name && url ? { id: name, name, url } : null;
    })
    .filter((e): e is PersonaEntry => e !== null);
  return entries.length > 0 ? entries : fallback;
}

const PERSONAS = parsePersonas();

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
  const [activeSheet, setActiveSheet] = useState<"sessions" | "device" | null>(
    null,
  );
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
  const [selectedPersonaId, setSelectedPersonaId] = useState(
    PERSONAS[0]?.id ?? "abuelos",
  );
  const currentPersona =
    PERSONAS.find((p) => p.id === selectedPersonaId) ?? PERSONAS[0];

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

  // ── Sessions (in-memory only for v1) ─────────────────────────────────────
  // Sample conversations — static demo data. Strings come from the i18n
  // catalog so they flip with the UI language; the `when` field is a
  // composed template (today/yesterday + a fixed time) rather than a
  // real Date to keep the mock deterministic.
  const sessions = [
    {
      id: "s1",
      preview: t("sessions.sample.1"),
      when: t("sessions.when.todayAt", { time: "3:42 PM" }),
      duration: "14m",
      turns: 23,
    },
    {
      id: "s2",
      preview: t("sessions.sample.2"),
      when: t("sessions.when.todayAt", { time: "1:08 PM" }),
      duration: "2m",
      turns: 4,
    },
    {
      id: "s3",
      preview: t("sessions.sample.3"),
      when: t("sessions.when.yesterday"),
      duration: "6m",
      turns: 11,
    },
  ];

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
    // Connect to first persona with the currently-selected language so
    // the very first session opens in the right translation. Subsequent
    // language flips go through `ws.setLanguage()` which drops the
    // socket and reconnects with `?lang=<code>`.
    ws.connect(currentPersona?.url, language);
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
      if (activeSheet || tweaksOpen) return;
      if (!booted) return;
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      void pressPtt();
    },
    [activeSheet, tweaksOpen, booted, pressPtt],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      releasePtt();
    },
    [releasePtt],
  );

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
      if (activeSheet || tweaksOpen) return;
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
  }, [activeSheet, tweaksOpen, pressPtt, releasePtt]);

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

  const cssVars: React.CSSProperties & Record<string, string> = {
    "--hux-bg": bgCss,
    "--hux-fg": fgCss,
    "--hux-fg-dim": "color-mix(in oklab, var(--hux-fg) 65%, transparent)",
    "--hux-fg-line": "color-mix(in oklab, var(--hux-fg) 22%, transparent)",
    "--hux-fg-faint": "color-mix(in oklab, var(--hux-fg) 10%, transparent)",
    "--hux-serif": fonts.serif,
    "--hux-sans": fonts.sans,
    "--hux-mono": '"JetBrains Mono", "SF Mono", monospace',
  };

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
  const deviceHost =
    (ws.activeUrl ?? "").replace(/^wss?:\/\//, "").replace(/:\d+$/, "") ||
    "localhost";
  const connLabel = ws.connected ? deviceHost : t("header.connRetrying");

  // ── Persona switch ────────────────────────────────────────────────────────
  const handlePersonaPick = useCallback(
    (id: string) => {
      const p = PERSONAS.find((x) => x.id === id);
      if (!p) return;
      setSelectedPersonaId(id);
      ws.switchPersona(p.url);
      setActiveSheet(null);
    },
    [ws],
  );

  // ── Orb scale when transcript is open ────────────────────────────────────
  const orbScale = transcriptOpen ? 0.62 : 1;

  return (
    <div className="hux-root" style={cssVars as React.CSSProperties}>
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
            <div className="hux-connchip" title={ws.activeUrl ?? undefined}>
              <span className={`hux-conn-dot ${ws.connected ? "on" : "off"}`} />
              {connLabel}
              <span className="hux-conn-sep">\u00b7</span>
              <span className="hux-conn-time">{timeStr}</span>
            </div>
          </div>
          <button className="hux-chip" onClick={() => setActiveSheet("device")}>
            {currentPersona?.name ?? "huxley"}
            <span className="hux-chip-arrow">{"\u203a"}</span>
          </button>
        </header>

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
            sessions={sessions}
            onPick={() => setActiveSheet(null)}
          />
        )}
        {activeSheet === "device" && (
          <DeviceSheet
            onClose={() => setActiveSheet(null)}
            device={{
              connected: ws.connected,
              url: ws.activeUrl ?? "localhost:8765",
              persona: selectedPersonaId,
              personas: PERSONAS,
              spend: 0,
              storage: "\u2014",
              lastSession: "\u2014",
              skillsCount: 0,
            }}
            onPersonaPick={handlePersonaPick}
            language={language}
            supportedLanguages={SUPPORTED_LANGUAGES}
            onLanguagePick={handleLanguagePick}
            appearance={appearance}
            onAppearance={(patch) => {
              patchAppearance(patch as Partial<Appearance>);
              setTweaks((tw) => ({ ...tw, ...patch }));
            }}
            onReload={() => ws.sendClientEvent("ui.reload_skills")}
            onRestart={() => ws.sendClientEvent("ui.restart_server")}
            onViewLogs={() => setActiveSheet(null)}
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
    </div>
  );
}
