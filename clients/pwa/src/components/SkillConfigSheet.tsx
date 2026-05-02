// Per-skill config + secrets editor (Marketplace v2 Phase A read +
// Phase B writes).
//
// Opens when the user taps a skill card in SkillsSheet. Renders the
// skill's `config_schema` as editable inputs whose state is held
// locally; the user hits "Save" to persist a config diff (or save a
// secret separately). The header carries the enable/disable toggle.
//
// Two write channels:
//
//  - Plain config + enable/disable: mutates persona.yaml (server-side
//    ruamel round-trip) → reload. PWA receives a fresh skills_state
//    push and re-renders.
//
//  - Secrets (fields with `format: "secret"`): write to
//    `<persona>/data/secrets/<skill>/values.json` via a separate
//    frame; the value never sits in persona.yaml. The form holds
//    secret state alongside plain state but Saves them via different
//    WS frames.

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SkillSummary } from "../types.js";
import {
  type FieldDescriptor,
  formatValue,
  parseValue,
  prettyLabel,
  topLevelFields,
} from "../lib/schemaForm.js";

// Position fixed (not absolute) so the sheet escapes the
// `.hux-stage`'s 720px max-width column on desktop. Stays narrower
// than SkillsSheet (form-shaped, not card-grid-shaped).
const S = {
  sheet: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 31,
    background: "var(--hux-bg)",
    color: "var(--hux-fg)",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
  },
  // Outer/inner split so the chrome aligns with the body's centered
  // column on ultra-wide displays — see SkillsSheet for the rationale.
  headerOuter: {
    flexShrink: 0,
    width: "100%",
  },
  headerInner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "20px 32px 12px",
    width: "100%",
    maxWidth: 720,
    margin: "0 auto",
    boxSizing: "border-box" as const,
    fontFamily: "var(--hux-sans)",
    fontSize: 14,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
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
    padding: "8px 32px 24px",
    width: "100%",
    maxWidth: 720,
    margin: "0 auto",
    boxSizing: "border-box" as const,
  },
  title: {
    fontFamily: "var(--hux-serif)",
    fontWeight: 400,
    fontSize: "clamp(28px, 6vw, 44px)",
    lineHeight: 1.05,
    margin: "8px 0 8px",
    letterSpacing: "-0.01em",
  },
  subtitle: {
    fontFamily: "var(--hux-sans)",
    fontSize: 13,
    color: "var(--hux-fg-dim)",
    marginBottom: 16,
    letterSpacing: "0.04em",
    display: "flex",
    gap: 10,
    alignItems: "center",
    flexWrap: "wrap" as const,
  },
  enableRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 16px",
    border: "1px solid var(--hux-fg-line)",
    borderRadius: 12,
    marginBottom: 24,
    fontFamily: "var(--hux-sans)",
    fontSize: 14,
  },
  enableLabel: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  },
  enableLabelTitle: {
    fontSize: 14,
    color: "var(--hux-fg)",
  },
  enableLabelHint: {
    fontSize: 12,
    color: "var(--hux-fg-dim)",
  },
  toggle: (on: boolean): React.CSSProperties => ({
    position: "relative",
    width: 44,
    height: 24,
    borderRadius: 999,
    border: "1px solid var(--hux-fg-line)",
    background: on ? "var(--hux-fg)" : "transparent",
    cursor: "pointer",
    flexShrink: 0,
    transition: "background 200ms ease",
    padding: 0,
  }),
  toggleKnob: (on: boolean): React.CSSProperties => ({
    position: "absolute",
    top: 2,
    left: on ? 22 : 2,
    width: 18,
    height: 18,
    borderRadius: 999,
    background: on ? "var(--hux-bg)" : "var(--hux-fg)",
    transition: "left 200ms ease, background 200ms ease",
  }),
  field: {
    padding: "16px 0",
    borderBottom: "1px solid var(--hux-fg-line)",
    fontFamily: "var(--hux-sans)",
    fontSize: 15,
    lineHeight: 1.4,
    display: "flex",
    flexDirection: "column" as const,
    gap: 8,
  },
  fieldLabel: {
    fontSize: 12,
    letterSpacing: "0.10em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
    display: "flex",
    gap: 6,
    alignItems: "center",
  },
  required: {
    color: "var(--hux-accent, #c44)",
    fontSize: 13,
    lineHeight: 1,
  },
  input: {
    width: "100%",
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    borderRadius: 8,
    padding: "10px 12px",
    color: "var(--hux-fg)",
    fontFamily: "var(--hux-sans)",
    fontSize: 15,
    lineHeight: 1.3,
    boxSizing: "border-box" as const,
    outline: "none",
  },
  inputDisabled: {
    color: "var(--hux-fg-dim)",
    cursor: "not-allowed",
  },
  help: {
    fontSize: 13,
    color: "var(--hux-fg-dim)",
    lineHeight: 1.4,
  },
  emptyState: {
    fontFamily: "var(--hux-sans)",
    color: "var(--hux-fg-dim)",
    fontSize: 15,
    padding: "24px 0",
    lineHeight: 1.45,
  },
  metaRow: {
    fontFamily: "var(--hux-sans)",
    fontSize: 12,
    color: "var(--hux-fg-dim)",
    letterSpacing: "0.05em",
    paddingTop: 28,
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
  },
  footer: {
    flexShrink: 0,
    padding: "12px 32px",
    borderTop: "1px solid var(--hux-fg-line)",
    background: "var(--hux-bg)",
    display: "flex",
    justifyContent: "center",
  },
  footerInner: {
    display: "flex",
    width: "100%",
    maxWidth: 720,
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  saveBtn: (enabled: boolean): React.CSSProperties => ({
    background: enabled ? "var(--hux-fg)" : "transparent",
    color: enabled ? "var(--hux-bg)" : "var(--hux-fg-dim)",
    border: "1px solid var(--hux-fg)",
    padding: "8px 18px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 13,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    cursor: enabled ? "pointer" : "default",
    opacity: enabled ? 1 : 0.5,
  }),
  secretActions: {
    display: "flex",
    gap: 8,
  },
  secretBtn: {
    background: "transparent",
    border: "1px solid var(--hux-fg-line)",
    color: "var(--hux-fg)",
    padding: "6px 12px",
    borderRadius: 999,
    fontFamily: "var(--hux-sans)",
    fontSize: 12,
    cursor: "pointer",
  },
  badgeOn: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontSize: 11,
    letterSpacing: "0.10em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg)",
    border: "1px solid var(--hux-fg)",
    borderRadius: 999,
    padding: "2px 10px",
  },
  badgeOff: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontSize: 11,
    letterSpacing: "0.10em",
    textTransform: "uppercase" as const,
    color: "var(--hux-fg-dim)",
    border: "1px solid var(--hux-fg-line)",
    borderRadius: 999,
    padding: "2px 10px",
  },
};

interface Props {
  skill: SkillSummary;
  onClose: () => void;
  onSetEnabled: (skill: string, enabled: boolean) => void;
  onSetConfig: (skill: string, config: Record<string, unknown>) => void;
  onSetSecret: (skill: string, key: string, value: string) => void;
  onDeleteSecret: (skill: string, key: string) => void;
  sheetClassName?: string;
}

// Convert a config value to its in-form draft representation.
// Most kinds round-trip directly; arrays-of-strings get joined into
// a comma-separated string for an `<input>`-friendly editing
// experience (Phase B); booleans stay booleans for the toggle
// renderer.
function toDraft(field: FieldDescriptor, value: unknown): unknown {
  if (field.kind === "array_of_strings" && Array.isArray(value)) {
    return value.filter((v) => typeof v === "string").join(", ");
  }
  if (
    (field.kind === "number" || field.kind === "integer") &&
    typeof value === "number"
  ) {
    return String(value);
  }
  if (value === undefined && field.schema.default !== undefined) {
    // Pre-fill the input with the schema's default so the user sees
    // what's effective and can edit from there.
    return toDraft(field, field.schema.default);
  }
  return value;
}

export function SkillConfigSheet({
  skill,
  onClose,
  onSetEnabled,
  onSetConfig,
  onSetSecret,
  onDeleteSecret,
  sheetClassName = "hux-sheet",
}: Props) {
  const { t } = useTranslation();
  const fields = useMemo(
    () => topLevelFields(skill.config_schema),
    [skill.config_schema],
  );
  const secretsSet = useMemo(
    () => new Set(skill.secret_keys_set),
    [skill.secret_keys_set],
  );
  const plainFields = useMemo(
    () => fields.filter((f) => f.kind !== "secret"),
    [fields],
  );
  const secretFields = useMemo(
    () => fields.filter((f) => f.kind === "secret"),
    [fields],
  );

  // Local draft state for plain config inputs. Keyed by field name.
  // Initialized from the current_config + schema defaults; reset when
  // the skill prop changes (the user closes + reopens, or a fresh
  // skills_state push arrives with new server-side state).
  const [drafts, setDrafts] = useState<Record<string, unknown>>(() => {
    const out: Record<string, unknown> = {};
    for (const f of fields) {
      if (f.kind === "secret") continue;
      out[f.name] = toDraft(f, skill.current_config[f.name]);
    }
    return out;
  });

  // Reset drafts when the underlying skill changes (server pushes
  // a fresh skills_state after a write — we want the form to mirror
  // disk truth, not retain a stale draft from before the save).
  useEffect(() => {
    const out: Record<string, unknown> = {};
    for (const f of fields) {
      if (f.kind === "secret") continue;
      out[f.name] = toDraft(f, skill.current_config[f.name]);
    }
    setDrafts(out);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skill.name, skill.current_config, fields]);

  // The plain-config Save button only enables when the form is
  // dirty (some draft differs from the on-disk value). Comparing as
  // JSON-strings lets us treat nested objects + arrays uniformly
  // without a deep-equals helper.
  const isDirty = useMemo(() => {
    for (const f of plainFields) {
      const current = skill.current_config[f.name];
      const parsed = parseValue(f.kind, drafts[f.name]);
      if (JSON.stringify(parsed) !== JSON.stringify(current)) return true;
    }
    return false;
  }, [drafts, plainFields, skill.current_config]);

  const handleSaveConfig = () => {
    if (!isDirty) return;
    const out: Record<string, unknown> = {};
    for (const f of plainFields) {
      const parsed = parseValue(f.kind, drafts[f.name]);
      // Skip undefined to avoid writing literal `undefined` (which
      // becomes `null` in JSON) — leaves the field unset on disk.
      if (parsed === undefined) continue;
      out[f.name] = parsed;
    }
    onSetConfig(skill.name, out);
  };

  return (
    <div style={S.sheet} className={sheetClassName}>
      <div style={S.headerOuter}>
        <div style={S.headerInner}>
          <span>{t("skills.detailEyebrow", "Skill")}</span>
          <button style={S.closeBtn} onClick={onClose}>
            {t("skills.back", "Back")}
          </button>
        </div>
      </div>
      <div style={S.body}>
        <h2 style={S.title}>{prettyLabel(skill.name)}</h2>
        <div style={S.subtitle}>
          {skill.package && (
            <span>
              {skill.package}
              {skill.version ? ` v${skill.version}` : ""}
            </span>
          )}
        </div>

        <div style={S.enableRow}>
          <div style={S.enableLabel}>
            <span style={S.enableLabelTitle}>
              {skill.enabled
                ? t("skills.enabled", "Enabled")
                : t("skills.disabled", "Disabled")}
            </span>
            <span style={S.enableLabelHint}>
              {t(
                "skills.toggleHint",
                "Toggle to add or remove from the active persona.",
              )}
            </span>
          </div>
          <button
            style={S.toggle(skill.enabled)}
            aria-label={t("skills.toggleAria", "Toggle skill")}
            onClick={() => onSetEnabled(skill.name, !skill.enabled)}
          >
            <span style={S.toggleKnob(skill.enabled)} />
          </button>
        </div>

        {fields.length === 0 && (
          <div style={S.emptyState}>
            {t("skills.noConfig", "This skill has no configurable settings.")}
          </div>
        )}

        {plainFields.map((field) => (
          <PlainFieldRow
            key={field.name}
            field={field}
            value={drafts[field.name]}
            disabled={!skill.enabled}
            onChange={(v) =>
              setDrafts((prev) => ({ ...prev, [field.name]: v }))
            }
          />
        ))}

        {secretFields.map((field) => (
          <SecretFieldRow
            key={field.name}
            field={field}
            isSet={secretsSet.has(field.name)}
            disabled={!skill.enabled}
            onSet={(value) => onSetSecret(skill.name, field.name, value)}
            onDelete={() => onDeleteSecret(skill.name, field.name)}
          />
        ))}

        <div style={S.metaRow}>
          <span>
            {t("skills.dataSchemaVersion", "Data schema version")}:{" "}
            {skill.data_schema_version}
          </span>
          <span>
            {t("skills.entryPointName", "Entry-point name")}: {skill.name}
          </span>
        </div>
      </div>
      {plainFields.length > 0 && (
        <div style={S.footer}>
          <div style={S.footerInner}>
            <span
              style={{
                fontSize: 12,
                color: "var(--hux-fg-dim)",
              }}
            >
              {isDirty
                ? t("skills.unsavedHint", "Unsaved changes.")
                : t("skills.savedHint", "All changes saved.")}
            </span>
            <button
              style={S.saveBtn(isDirty && skill.enabled)}
              onClick={handleSaveConfig}
              disabled={!isDirty || !skill.enabled}
            >
              {t("skills.save", "Save")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Plain field row (string / number / boolean / enum / array) ───────────

interface PlainFieldRowProps {
  field: FieldDescriptor;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
}

function PlainFieldRow({
  field,
  value,
  disabled,
  onChange,
}: PlainFieldRowProps) {
  const inputStyle = {
    ...S.input,
    ...(disabled ? S.inputDisabled : {}),
  };

  let input: React.ReactNode;
  switch (field.kind) {
    case "boolean":
      input = (
        <button
          style={S.toggle(value === true)}
          aria-label={field.name}
          disabled={disabled}
          onClick={() => onChange(value !== true)}
        >
          <span style={S.toggleKnob(value === true)} />
        </button>
      );
      break;
    case "enum": {
      const options = Array.isArray(field.schema.enum) ? field.schema.enum : [];
      input = (
        <select
          style={inputStyle}
          value={
            typeof value === "string" || typeof value === "number"
              ? String(value)
              : ""
          }
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
      );
      break;
    }
    case "number":
    case "integer":
      input = (
        <input
          type="number"
          style={inputStyle}
          value={
            typeof value === "string" || typeof value === "number"
              ? String(value)
              : ""
          }
          step={field.kind === "integer" ? 1 : "any"}
          min={field.schema.minimum}
          max={field.schema.maximum}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      );
      break;
    case "array_of_strings":
      input = (
        <input
          type="text"
          style={inputStyle}
          value={typeof value === "string" ? value : ""}
          placeholder="e.g. AAPL, MSFT, GOOG"
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      );
      break;
    case "object":
    case "unknown":
      // Phase B doesn't editor these — show the raw JSON so the user
      // sees the value and the field doesn't disappear. They can
      // still hand-edit persona.yaml for the rare nested-object case.
      input = (
        <span
          style={{
            fontFamily: "var(--hux-mono, monospace)",
            fontSize: 13,
            color: "var(--hux-fg-dim)",
          }}
        >
          {formatValue(field.kind, value) ?? "—"}
        </span>
      );
      break;
    default:
      input = (
        <input
          type="text"
          style={inputStyle}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }

  return (
    <div style={S.field}>
      <div style={S.fieldLabel}>
        <span>{prettyLabel(field.name)}</span>
        {field.required && <span style={S.required}>*</span>}
      </div>
      {input}
      {field.help && <div style={S.help}>{field.help}</div>}
    </div>
  );
}

// ── Secret field row (Set ✓ / Set / Update / Clear) ─────────────────────

interface SecretFieldRowProps {
  field: FieldDescriptor;
  isSet: boolean;
  disabled: boolean;
  onSet: (value: string) => void;
  onDelete: () => void;
}

function SecretFieldRow({
  field,
  isSet,
  disabled,
  onSet,
  onDelete,
}: SecretFieldRowProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(!isSet);
  const inputStyle = {
    ...S.input,
    ...(disabled ? S.inputDisabled : {}),
  };

  const handleSave = () => {
    if (!draft) return;
    onSet(draft);
    setDraft("");
    setEditing(false);
  };

  return (
    <div style={S.field}>
      <div style={S.fieldLabel}>
        <span>{prettyLabel(field.name)}</span>
        {field.required && <span style={S.required}>*</span>}
        <span
          style={{
            fontSize: 10,
            color: "var(--hux-fg-dim)",
            border: "1px solid var(--hux-fg-line)",
            borderRadius: 999,
            padding: "1px 6px",
            letterSpacing: "0.06em",
          }}
        >
          {t("skills.secretBadge", "Secret")}
        </span>
        <span style={isSet ? S.badgeOn : S.badgeOff}>
          {isSet
            ? t("skills.secretSet", "Set ✓")
            : t("skills.secretNotSet", "Not set")}
        </span>
      </div>
      {editing ? (
        <input
          type="password"
          style={inputStyle}
          value={draft}
          autoComplete="new-password"
          placeholder={t("skills.secretPlaceholder", "Paste the secret value")}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
        />
      ) : null}
      <div style={S.secretActions}>
        {editing && (
          <>
            <button
              style={S.secretBtn}
              onClick={handleSave}
              disabled={disabled || !draft}
            >
              {t("skills.secretSave", "Save secret")}
            </button>
            {isSet && (
              <button
                style={S.secretBtn}
                onClick={() => {
                  setEditing(false);
                  setDraft("");
                }}
                disabled={disabled}
              >
                {t("skills.cancel", "Cancel")}
              </button>
            )}
          </>
        )}
        {!editing && (
          <>
            <button
              style={S.secretBtn}
              onClick={() => setEditing(true)}
              disabled={disabled}
            >
              {isSet
                ? t("skills.secretUpdate", "Update")
                : t("skills.secretSet", "Set ✓")}
            </button>
            {isSet && (
              <button
                style={S.secretBtn}
                onClick={onDelete}
                disabled={disabled}
              >
                {t("skills.secretClear", "Clear")}
              </button>
            )}
          </>
        )}
      </div>
      {field.help && <div style={S.help}>{field.help}</div>}
    </div>
  );
}
