// Read-only per-skill config viewer (Marketplace v2 Phase A).
//
// Opens when the user taps a skill row in DeviceSheet's Skills section.
// Renders the skill's `config_schema` as a list of read-only fields
// with their current persona-yaml values. Secrets are surfaced as
// "Set ✓" / "Not set" — the value never leaves the server.
//
// Phase B will extend this with editable inputs + a "Save" footer.
// The visual structure here is the canvas Phase B drops inputs onto.

import { useTranslation } from "react-i18next";
import type { SkillSummary } from "../types.js";
import {
  type FieldDescriptor,
  formatValue,
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
    padding: "8px 32px 48px",
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
    marginBottom: 24,
    letterSpacing: "0.04em",
    display: "flex",
    gap: 10,
    alignItems: "center",
    flexWrap: "wrap" as const,
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
  field: {
    padding: "16px 0",
    borderBottom: "1px solid var(--hux-fg-line)",
    fontFamily: "var(--hux-sans)",
    fontSize: 15,
    lineHeight: 1.4,
    display: "flex",
    flexDirection: "column" as const,
    gap: 6,
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
  fieldValue: {
    color: "var(--hux-fg)",
    overflowWrap: "anywhere" as const,
    whiteSpace: "pre-wrap" as const,
    fontVariantNumeric: "tabular-nums" as const,
  },
  fieldEmpty: {
    color: "var(--hux-fg-dim)",
    fontStyle: "italic" as const,
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
};

interface Props {
  skill: SkillSummary;
  onClose: () => void;
  sheetClassName?: string;
}

export function SkillConfigSheet({
  skill,
  onClose,
  sheetClassName = "hux-sheet",
}: Props) {
  const { t } = useTranslation();
  const fields = topLevelFields(skill.config_schema);
  const secretsSet = new Set(skill.secret_keys_set);
  // The schema lists EVERY field. Secret-only fields appear in `fields`
  // already (their `kind === "secret"`). What remains for the "no schema"
  // empty state is a skill that simply doesn't declare config_schema.
  const hasAnything =
    fields.length > 0 ||
    skill.secret_required_keys.length > 0 ||
    skill.secret_keys_set.length > 0;

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
          <span style={skill.enabled ? S.badgeOn : S.badgeOff}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: skill.enabled
                  ? "var(--hux-fg)"
                  : "var(--hux-fg-line)",
              }}
            />
            {skill.enabled
              ? t("skills.enabled", "Enabled")
              : t("skills.disabled", "Disabled")}
          </span>
          {skill.package && (
            <span>
              {skill.package}
              {skill.version ? ` v${skill.version}` : ""}
            </span>
          )}
        </div>

        {!hasAnything && (
          <div style={S.emptyState}>
            {t("skills.noConfig", "This skill has no configurable settings.")}
          </div>
        )}

        {fields.map((field) => (
          <FieldRow
            key={field.name}
            field={field}
            value={skill.current_config[field.name]}
            secretSet={secretsSet.has(field.name)}
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
    </div>
  );
}

interface FieldRowProps {
  field: FieldDescriptor;
  value: unknown;
  secretSet: boolean;
}

function FieldRow({ field, value, secretSet }: FieldRowProps) {
  const { t } = useTranslation();
  const isSecret = field.kind === "secret";
  let display: React.ReactNode;
  if (isSecret) {
    display = (
      <span
        style={
          secretSet
            ? { color: "var(--hux-fg)", fontVariantNumeric: "tabular-nums" }
            : S.fieldEmpty
        }
      >
        {secretSet
          ? t("skills.secretSet", "Set ✓")
          : t("skills.secretNotSet", "Not set")}
      </span>
    );
  } else {
    const formatted = formatValue(field.kind, value);
    if (formatted !== null && formatted !== "") {
      display = <span style={S.fieldValue}>{formatted}</span>;
    } else if (field.schema.default !== undefined) {
      display = (
        <span style={S.fieldEmpty}>
          {t("skills.default", "Default")}:{" "}
          {formatValue(field.kind, field.schema.default) ?? "—"}
        </span>
      );
    } else {
      display = (
        <span style={S.fieldEmpty}>{t("skills.unset", "Not configured")}</span>
      );
    }
  }
  return (
    <div style={S.field}>
      <div style={S.fieldLabel}>
        <span>{prettyLabel(field.name)}</span>
        {field.required && <span style={S.required}>*</span>}
        {isSecret && (
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
        )}
      </div>
      {display}
      {field.help && <div style={S.help}>{field.help}</div>}
    </div>
  );
}
