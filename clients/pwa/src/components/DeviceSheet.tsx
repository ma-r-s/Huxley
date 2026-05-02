import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Appearance, PersonaEntry, SkillsState } from "../types.js";
import {
  LANGUAGE_NAMES,
  type LanguageCode,
  type SUPPORTED_LANGUAGES,
} from "../i18n/index.js";

const S = {
  sheet: {
    position: "absolute" as const,
    inset: 0,
    zIndex: 30,
    background: "var(--hux-bg)",
    color: "var(--hux-fg)",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "20px 24px 12px",
    fontFamily: "var(--hux-sans)",
    fontSize: 14,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
    flexShrink: 0,
  },
  closeBtn: {
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg)",
    padding: "6px 12px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 12,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    cursor: "pointer",
  },
  body: {
    flex: 1,
    overflowY: "auto" as const,
    overscrollBehavior: "contain" as const,
    padding: "8px 24px 32px",
  },
  rowBtn: {
    width: "100%",
    textAlign: "left" as const,
    background: "transparent",
    border: "none",
    borderBottom: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg)",
    padding: "18px 0",
    fontFamily: "var(--hux-sans)",
    fontSize: 16,
    cursor: "pointer",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline" as const,
  },
};

// ── Section ──────────────────────────────────────────────────────────────
interface SectionProps {
  label: string;
  children: React.ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  summary?: string;
}

function Section({
  label,
  children,
  collapsible,
  defaultOpen = false,
  summary,
}: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  if (!collapsible) {
    return (
      <div style={{ marginBottom: 32 }}>
        <div
          style={{
            fontFamily: "var(--hux-sans)",
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            color: "var(--hux-fg-dim)",
            marginBottom: 14,
            paddingBottom: 8,
            borderBottom: "1px solid var(--hux-fg-line)",
          }}
        >
          {label}
        </div>
        {children}
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 32 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          padding: "0 0 8px",
          background: "transparent",
          border: "none",
          borderBottom: "1px solid var(--hux-fg-line)",
          color: "var(--hux-fg)",
          cursor: "pointer",
          textAlign: "left",
          marginBottom: open ? 16 : 0,
          transition: "margin-bottom 300ms ease",
        }}
        aria-expanded={open}
      >
        <span
          style={{
            fontFamily: "var(--hux-sans)",
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            color: "var(--hux-fg-dim)",
          }}
        >
          {label}
        </span>
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontFamily: "var(--hux-sans)",
            fontSize: 12,
            color: "var(--hux-fg-dim)",
          }}
        >
          {!open && summary && <span>{summary}</span>}
          <span
            style={{
              display: "inline-block",
              transform: open ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform 300ms cubic-bezier(.22,.9,.27,1)",
              fontSize: 14,
              color: "var(--hux-fg-dim)",
            }}
          >
            {"\u203a"}
          </span>
        </span>
      </button>
      <div
        style={{
          display: "grid",
          gridTemplateRows: open ? "1fr" : "0fr",
          transition: "grid-template-rows 400ms cubic-bezier(.22,.9,.27,1)",
        }}
      >
        <div style={{ minHeight: 0, overflow: "hidden" }}>
          <div
            style={{ padding: open ? "8px 6px 0" : "0 6px", margin: "0 -6px" }}
          >
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Appearance picker ─────────────────────────────────────────────────────
const ACCENTS = [
  { id: "coral", name: "Coral", l: 0.62, c: 0.19, h: 23 },
  { id: "amber", name: "Amber", l: 0.7, c: 0.14, h: 58 },
  { id: "clay", name: "Clay", l: 0.56, c: 0.1, h: 32 },
  { id: "rose", name: "Rose", l: 0.58, c: 0.16, h: 10 },
  { id: "plum", name: "Plum", l: 0.42, c: 0.12, h: 350 },
  { id: "moss", name: "Moss", l: 0.48, c: 0.08, h: 140 },
] as const;

const FONT_PAIRS = [
  { id: "instrument", name: "Instrument", hint: "Warm serif" },
  { id: "fraunces", name: "Fraunces", hint: "Editorial" },
  { id: "all-sans", name: "Sans", hint: "Clean & modern" },
  { id: "mono", name: "Mono", hint: "Terminal" },
] as const;

const EXPR_STEPS = [
  { id: "subtle", name: "Subtle", value: 0.55 },
  { id: "natural", name: "Natural", value: 1.0 },
  { id: "expressive", name: "Expressive", value: 1.5 },
] as const;

const THEMES = [
  { id: "coral", name: "Light", desc: "Warm coral" },
  { id: "dark", name: "Dark", desc: "Evening" },
  { id: "auto", name: "Auto", desc: "Match system" },
] as const;

function AppearanceLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "var(--hux-sans)",
        fontSize: 10,
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        color: "var(--hux-fg-dim)",
        marginBottom: 12,
      }}
    >
      {children}
    </div>
  );
}

interface AppearancePickerProps {
  appearance: Appearance;
  onChange: (patch: Partial<Appearance>) => void;
}

function AppearancePicker({ appearance, onChange }: AppearancePickerProps) {
  const currentAccent =
    ACCENTS.find((a) => Math.abs(a.h - appearance.redHue) < 3) ?? ACCENTS[0];
  const currentExpr = EXPR_STEPS.reduce(
    (best, s) =>
      Math.abs(s.value - appearance.expressiveness) <
      Math.abs(best.value - appearance.expressiveness)
        ? s
        : best,
    EXPR_STEPS[1],
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Accent swatches */}
      <div>
        <AppearanceLabel>Accent</AppearanceLabel>
        <div
          style={{
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
            padding: "6px 6px 4px",
            margin: "0 -6px",
          }}
        >
          {ACCENTS.map((a) => {
            const active = a.id === currentAccent?.id;
            const swatch = `oklch(${a.l} ${a.c} ${a.h})`;
            return (
              <button
                key={a.id}
                onClick={() =>
                  onChange({
                    accent: a.id,
                    redHue: a.h,
                    redChroma: a.c,
                    redLight: a.l,
                  })
                }
                aria-label={a.name}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 6,
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  padding: 0,
                  fontFamily: "var(--hux-sans)",
                }}
              >
                <span
                  style={{
                    position: "relative",
                    width: 38,
                    height: 38,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {active && (
                    <span
                      style={{
                        position: "absolute",
                        inset: 0,
                        borderRadius: 999,
                        border: "1.5px solid var(--hux-fg)",
                        pointerEvents: "none",
                      }}
                    />
                  )}
                  <span
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: 999,
                      background: swatch,
                      boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${swatch} 50%, white)`,
                      transition: "box-shadow 200ms ease",
                    }}
                  />
                </span>
                <span
                  style={{
                    fontSize: 10,
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                    color: active ? "var(--hux-fg)" : "var(--hux-fg-dim)",
                    transition: "color 200ms ease",
                  }}
                >
                  {a.name}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Font pairing */}
      <div>
        <AppearanceLabel>Typeface</AppearanceLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
          {FONT_PAIRS.map((f) => {
            const active = f.id === appearance.fontPair;
            const serif =
              f.id === "fraunces"
                ? '"Fraunces", Georgia, serif'
                : f.id === "all-sans"
                  ? '"Inter Tight", system-ui, sans-serif'
                  : f.id === "mono"
                    ? '"JetBrains Mono", monospace'
                    : '"Instrument Serif", Georgia, serif';
            return (
              <button
                key={f.id}
                onClick={() => onChange({ fontPair: f.id })}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  padding: "12px 0",
                  background: "transparent",
                  border: "none",
                  borderBottom: "1px solid var(--hux-fg-line)",
                  color: "var(--hux-fg)",
                  cursor: "pointer",
                  textAlign: "left",
                  fontFamily: "var(--hux-sans)",
                }}
              >
                <span
                  style={{ display: "flex", alignItems: "baseline", gap: 10 }}
                >
                  <span
                    style={{
                      fontFamily: serif,
                      fontSize: 20,
                      fontStyle:
                        f.id === "instrument" || f.id === "fraunces"
                          ? "italic"
                          : "normal",
                    }}
                  >
                    Aa
                  </span>
                  <span style={{ fontSize: 15 }}>{f.name}</span>
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--hux-fg-dim)",
                      letterSpacing: "0.04em",
                    }}
                  >
                    {f.hint}
                  </span>
                </span>
                <span
                  style={{
                    width: 14,
                    height: 14,
                    borderRadius: 999,
                    border: "1px solid var(--hux-fg)",
                    background: active ? "var(--hux-fg)" : "transparent",
                    flexShrink: 0,
                  }}
                />
              </button>
            );
          })}
        </div>
      </div>

      {/* Orb personality */}
      <div>
        <AppearanceLabel>Orb personality</AppearanceLabel>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 6,
            padding: 4,
            background: "var(--hux-fg-faint)",
            borderRadius: 10,
          }}
        >
          {EXPR_STEPS.map((s) => {
            const active = s.id === currentExpr?.id;
            return (
              <button
                key={s.id}
                onClick={() => onChange({ expressiveness: s.value })}
                style={{
                  padding: "10px 4px",
                  borderRadius: 7,
                  border: "none",
                  background: active ? "var(--hux-fg)" : "transparent",
                  color: active ? "var(--hux-bg)" : "var(--hux-fg)",
                  fontFamily: "var(--hux-sans)",
                  fontSize: 13,
                  letterSpacing: "0.02em",
                  cursor: "pointer",
                  transition: "background 200ms ease, color 200ms ease",
                }}
              >
                {s.name}
              </button>
            );
          })}
        </div>
      </div>

      {/* Theme */}
      <div>
        <AppearanceLabel>Theme</AppearanceLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
          {THEMES.map((t) => {
            const active = t.id === appearance.theme;
            return (
              <button
                key={t.id}
                onClick={() => onChange({ theme: t.id as Appearance["theme"] })}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "14px 0",
                  background: "transparent",
                  border: "none",
                  borderBottom: "1px solid var(--hux-fg-line)",
                  color: "var(--hux-fg)",
                  cursor: "pointer",
                  textAlign: "left",
                  fontFamily: "var(--hux-sans)",
                }}
              >
                <span
                  style={{ display: "flex", flexDirection: "column", gap: 4 }}
                >
                  <span style={{ fontSize: 16 }}>{t.name}</span>
                  <span style={{ fontSize: 12, color: "var(--hux-fg-dim)" }}>
                    {t.desc}
                  </span>
                </span>
                <span
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: 999,
                    border: "1px solid var(--hux-fg)",
                    background: active ? "var(--hux-fg)" : "transparent",
                  }}
                />
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Persona picker ────────────────────────────────────────────────────────
// T1.13: persona shape comes from the server's `available_personas`.
// `name` is the canonical id (what `?persona=` selects), `display_name`
// is the human-readable label, `language` is the persona's default.
// The picker is disabled while a claim or stream is active because a
// swap closes the WS, and tearing down a live call / audiobook
// midstream is the user-hostile sort of surprise the picker shouldn't
// volunteer for. Symmetric to the server contract: client can't
// request, server doesn't act.
interface PersonaPickerProps {
  personas: PersonaEntry[];
  current: string;
  onPick: (name: string) => void;
  disabled?: boolean;
  disabledHint?: string;
}

function PersonaPicker({
  personas,
  current,
  onPick,
  disabled = false,
  disabledHint,
}: PersonaPickerProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 0,
        opacity: disabled ? 0.45 : 1,
        pointerEvents: disabled ? "none" : "auto",
      }}
      aria-disabled={disabled}
    >
      {disabled && disabledHint && (
        <div
          style={{
            fontSize: 12,
            color: "var(--hux-fg-dim)",
            fontFamily: "var(--hux-sans)",
            paddingBottom: 8,
          }}
        >
          {disabledHint}
        </div>
      )}
      {personas.map((p) => {
        const active = p.name === current;
        return (
          <button
            key={p.name}
            onClick={() => onPick(p.name)}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "14px 0",
              background: "transparent",
              border: "none",
              borderBottom: "1px solid var(--hux-fg-line)",
              color: "var(--hux-fg)",
              cursor: disabled ? "not-allowed" : "pointer",
              textAlign: "left",
              fontFamily: "var(--hux-sans)",
            }}
          >
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 16 }}>{p.display_name}</span>
              <span style={{ fontSize: 12, color: "var(--hux-fg-dim)" }}>
                {p.language}
              </span>
            </span>
            <span
              style={{
                width: 16,
                height: 16,
                borderRadius: 999,
                border: "1px solid var(--hux-fg)",
                background: active ? "var(--hux-fg)" : "transparent",
                transition: "background 0.2s ease",
              }}
            />
          </button>
        );
      })}
    </div>
  );
}

// ── Skills entry summary helper ─────────────────────────────────────────
//
// DeviceSheet now exposes Skills via a single "Manage skills →" row
// that opens the dedicated SkillsSheet (cards + tabs UX). The summary
// shown on that row is a quick "N of M enabled" so the user gets
// at-a-glance state without expanding anything.

function skillsRowSummary(
  state: SkillsState | null,
  t: (key: string, fallback: string) => string,
): string {
  if (state === null) return t("device.skills.loading", "Loading…");
  const total = state.skills.length;
  if (total === 0) return t("device.skills.none", "None installed");
  const enabled = state.skills.filter((s) => s.enabled).length;
  return t("device.skills.summary", "{{enabled}} of {{total}} enabled")
    .replace("{{enabled}}", String(enabled))
    .replace("{{total}}", String(total));
}

// ── DeviceSheet ───────────────────────────────────────────────────────────
export interface DeviceInfo {
  connected: boolean;
  url: string;
  persona: string;
  personas: PersonaEntry[];
}

interface DeviceSheetProps {
  onClose: () => void;
  device: DeviceInfo;
  onPersonaPick: (name: string) => void;
  // T1.13: when a claim or stream is active, switching personas
  // would terminate it abruptly (the WS closes during the swap).
  // Caller (App) sets this true while `activeClaimId !== null` or
  // `activeStream !== null`; the picker grays out with a hint.
  personaPickerDisabled?: boolean;
  language: LanguageCode;
  supportedLanguages: typeof SUPPORTED_LANGUAGES;
  onLanguagePick: (code: LanguageCode) => void;
  appearance: Appearance;
  onAppearance: (patch: Partial<Appearance>) => void;
  onReload: () => void;
  onRestart: () => void;
  onViewLogs: () => void;
  // Marketplace v2 Phase A — DeviceSheet shows a single "Manage skills →"
  // row whose summary is computed from `skillsState`. `null` until the
  // first `skills_state` reply arrives. `onRequestSkillsState` fires on
  // mount + on every reopen so a stale persona swap re-syncs.
  // `onOpenSkills` opens the dedicated SkillsSheet (cards + tabs).
  skillsState: SkillsState | null;
  onRequestSkillsState: () => void;
  onOpenSkills: () => void;
}

export function DeviceSheet({
  onClose,
  device,
  onPersonaPick,
  personaPickerDisabled = false,
  language,
  supportedLanguages,
  onLanguagePick,
  appearance,
  onAppearance,
  onReload,
  onRestart,
  onViewLogs,
  skillsState,
  onRequestSkillsState,
  onOpenSkills,
}: DeviceSheetProps) {
  const { t } = useTranslation();

  // Refresh on every open. The closure is stable (App.tsx wraps it
  // in useCallback) so this fires once per mount, not on every
  // render. Phase B will also re-fire on every successful write so
  // the panel reflects writes from another tab / external edits.
  useEffect(() => {
    onRequestSkillsState();
  }, [onRequestSkillsState]);

  const accentName =
    ACCENTS.find((a) => a.id === appearance.accent)?.name ?? "Custom";
  const themeName =
    appearance.theme === "auto"
      ? t("device.appearance.themeAuto")
      : appearance.theme === "dark"
        ? t("device.appearance.themeDark")
        : t("device.appearance.themeLight");
  const appearanceSummary = `${accentName} \u00b7 ${themeName}`;

  const deviceHost = device.url.replace(/^wss?:\/\//, "").replace(/:\d+$/, "");

  return (
    <div style={S.sheet} className="hux-sheet">
      <div style={S.header}>
        <span>{t("device.title")}</span>
        <button style={S.closeBtn} onClick={onClose}>
          {t("device.close")}
        </button>
      </div>
      <div style={S.body}>
        <h2
          style={{
            fontFamily: "var(--hux-serif)",
            fontWeight: 400,
            fontSize: "clamp(34px, 8vw, 56px)",
            lineHeight: 1.05,
            margin: "8px 0 24px",
            letterSpacing: "-0.01em",
          }}
        >
          {t("device.headline")}
        </h2>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontFamily: "var(--hux-sans)",
            fontSize: 13,
            color: "var(--hux-fg-dim)",
            marginBottom: 28,
            letterSpacing: "0.04em",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: device.connected ? "var(--hux-fg)" : "transparent",
              border: "1px solid var(--hux-fg)",
              boxShadow: device.connected ? "0 0 12px var(--hux-fg)" : "none",
            }}
          />
          {device.connected ? t("device.connected") : t("device.offline")}{" "}
          {"\u00b7"} {deviceHost}
        </div>

        <Section
          label={t("device.sections.appearance")}
          collapsible
          summary={appearanceSummary}
        >
          <AppearancePicker appearance={appearance} onChange={onAppearance} />
        </Section>

        <Section
          label={t("device.sections.language")}
          collapsible
          summary={LANGUAGE_NAMES[language]}
        >
          <LanguagePicker
            current={language}
            supported={supportedLanguages}
            onPick={onLanguagePick}
          />
        </Section>

        <Section label={t("device.sections.persona")}>
          <PersonaPicker
            personas={device.personas}
            current={device.persona}
            onPick={onPersonaPick}
            disabled={personaPickerDisabled}
            disabledHint={
              personaPickerDisabled
                ? t("device.persona.disabledHint")
                : undefined
            }
          />
        </Section>

        <Section label={t("device.sections.skills", "Skills")}>
          <button style={S.rowBtn} onClick={onOpenSkills}>
            <span>{t("device.skills.manage", "Manage skills")}</span>
            <span
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 12,
                fontSize: 13,
                color: "var(--hux-fg-dim)",
              }}
            >
              <span>{skillsRowSummary(skillsState, t)}</span>
              <span>{"›"}</span>
            </span>
          </button>
        </Section>

        <Section label={t("device.sections.maintenance")}>
          <button style={S.rowBtn} onClick={onReload}>
            <span>{t("device.maintenance.reloadSkills")}</span>
            <span style={{ fontSize: 13, color: "var(--hux-fg-dim)" }}>
              {"\u21bb"}
            </span>
          </button>
          <button style={S.rowBtn} onClick={onRestart}>
            <span>{t("device.maintenance.restartServer")}</span>
            <span style={{ fontSize: 13, color: "var(--hux-fg-dim)" }}>
              {"\u21bb"}
            </span>
          </button>
          <button style={S.rowBtn} onClick={onViewLogs}>
            <span>{t("device.maintenance.viewLogs")}</span>
            <span style={{ fontSize: 13, color: "var(--hux-fg-dim)" }}>
              {"\u2192"}
            </span>
          </button>
        </Section>
      </div>
    </div>
  );
}

// ── Language picker ───────────────────────────────────────────────────────

interface LanguagePickerProps {
  current: LanguageCode;
  supported: typeof SUPPORTED_LANGUAGES;
  onPick: (code: LanguageCode) => void;
}

function LanguagePicker({ current, supported, onPick }: LanguagePickerProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {supported.map((code) => {
        const active = code === current;
        return (
          <button
            key={code}
            onClick={() => onPick(code)}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "14px 0",
              background: "transparent",
              border: "none",
              borderBottom: "1px solid var(--hux-fg-line)",
              color: "var(--hux-fg)",
              cursor: "pointer",
              textAlign: "left",
              fontFamily: "var(--hux-sans)",
            }}
          >
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 16 }}>{LANGUAGE_NAMES[code]}</span>
              <span
                style={{
                  fontSize: 12,
                  color: "var(--hux-fg-dim)",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                }}
              >
                {code}
              </span>
            </span>
            <span
              style={{
                width: 16,
                height: 16,
                borderRadius: 999,
                border: "1px solid var(--hux-fg)",
                background: active ? "var(--hux-fg)" : "transparent",
                transition: "background 0.2s ease",
              }}
            />
          </button>
        );
      })}
    </div>
  );
}
