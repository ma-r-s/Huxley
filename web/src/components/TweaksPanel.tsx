// Dev-only tweaks panel — exposed via ?tweaks=1 query param or Ctrl+Shift+T.
// Lets you override orb state, palette, fonts, and frame without touching code.

import type { OrbState } from "../types.js";

export interface Tweaks {
  redHue: number;
  redChroma: number;
  redLight: number;
  expressiveness: number;
  fontPair: string;
  demoState: OrbState | null; // null = live (not overridden)
  deviceFrame: "auto" | "mobile" | "desktop";
  theme: "coral" | "dark" | "auto";
  accent: string;
}

interface TweaksPanelProps {
  tweaks: Tweaks;
  onChange: (patch: Partial<Tweaks>) => void;
  onClose: () => void;
}

export function TweaksPanel({ tweaks, onChange, onClose }: TweaksPanelProps) {
  return (
    <aside className="hux-tweaks">
      <div className="hux-tweaks-head">
        <span>Tweaks</span>
        <button onClick={onClose} aria-label="Close">
          {"\u00d7"}
        </button>
      </div>
      <Field label={`Red hue \u00b7 ${tweaks.redHue.toFixed(0)}\u00b0`}>
        <input
          type="range"
          min="0"
          max="50"
          step="1"
          value={tweaks.redHue}
          onChange={(e) => onChange({ redHue: +e.target.value })}
        />
      </Field>
      <Field label={`Red chroma \u00b7 ${tweaks.redChroma.toFixed(2)}`}>
        <input
          type="range"
          min="0.05"
          max="0.22"
          step="0.01"
          value={tweaks.redChroma}
          onChange={(e) => onChange({ redChroma: +e.target.value })}
        />
      </Field>
      <Field label={`Red light \u00b7 ${tweaks.redLight.toFixed(2)}`}>
        <input
          type="range"
          min="0.40"
          max="0.78"
          step="0.01"
          value={tweaks.redLight}
          onChange={(e) => onChange({ redLight: +e.target.value })}
        />
      </Field>
      <Field
        label={`Orb expressiveness \u00b7 ${tweaks.expressiveness.toFixed(2)}\u00d7`}
      >
        <input
          type="range"
          min="0.3"
          max="1.8"
          step="0.05"
          value={tweaks.expressiveness}
          onChange={(e) => onChange({ expressiveness: +e.target.value })}
        />
      </Field>
      <Field label="Font pairing">
        <Pills
          value={tweaks.fontPair}
          onChange={(v) => onChange({ fontPair: v })}
          options={[
            ["instrument", "Instrument"],
            ["fraunces", "Fraunces"],
            ["all-sans", "All sans"],
            ["mono", "Mono"],
          ]}
        />
      </Field>
      <Field label="Demo state">
        <Pills
          value={tweaks.demoState ?? "live"}
          onChange={(v) =>
            onChange({ demoState: v === "live" ? null : (v as OrbState) })
          }
          options={[
            ["live", "Live"],
            ["idle", "Idle"],
            ["listening", "Listening"],
            ["thinking", "Thinking"],
            ["speaking", "Speaking"],
            ["playing", "Playing"],
            ["error", "Error"],
            ["paused", "Paused"],
            ["wake", "Wake"],
          ]}
        />
      </Field>
      <Field label="Frame">
        <Pills
          value={tweaks.deviceFrame}
          onChange={(v) =>
            onChange({ deviceFrame: v as Tweaks["deviceFrame"] })
          }
          options={[
            ["auto", "Auto"],
            ["mobile", "Mobile"],
            ["desktop", "Desktop"],
          ]}
        />
      </Field>
      <Field label="Theme">
        <Pills
          value={tweaks.theme}
          onChange={(v) => onChange({ theme: v as Tweaks["theme"] })}
          options={[
            ["coral", "Coral"],
            ["dark", "Dark"],
            ["auto", "Auto"],
          ]}
        />
      </Field>
    </aside>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="hux-tweak-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function Pills({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <div className="hux-pills">
      {options.map(([v, label]) => (
        <button
          key={v}
          className={`hux-pill${value === v ? " on" : ""}`}
          onClick={() => onChange(v)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
