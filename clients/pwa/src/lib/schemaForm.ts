// JSON Schema 2020-12 walker for the Skills config form.
//
// Phase A is read-only: this module exposes helpers that let the
// renderer decide what kind of field to draw, and how to format the
// current value for display. Phase B layers editable inputs on top
// using the same field-type taxonomy.
//
// We support a deliberate subset:
//   - string / number / integer / boolean / enum / array of strings
//   - top-level object (the schema's root)
//   - format: "secret" — a string field that's stored in ctx.secrets,
//     never echoed back from the server (the wire only carries
//     `secret_keys_set: string[]` indicating presence).
//   - x-huxley:help — markdown help text rendered below the field.
// Anything outside this subset falls through to a "raw" debug view.

import type { JsonSchema } from "../types.js";

export type FieldKind =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "enum"
  | "array_of_strings"
  | "secret"
  | "object"
  | "unknown";

export interface FieldDescriptor {
  name: string;
  kind: FieldKind;
  required: boolean;
  schema: JsonSchema;
  help: string | null;
}

// Top-level walk: iterate `properties` in declaration order.
// Phase A only renders top-level fields; nested objects fall through
// to a "raw" debug view so the user sees something rather than
// nothing. Phase B may recurse into nested objects when a real
// skill schema demands it.
export function topLevelFields(schema: JsonSchema | null): FieldDescriptor[] {
  if (!schema || schema.type !== "object" || !schema.properties) return [];
  const required = new Set(schema.required ?? []);
  const out: FieldDescriptor[] = [];
  for (const [name, sub] of Object.entries(schema.properties)) {
    out.push({
      name,
      kind: classify(sub),
      required: required.has(name),
      schema: sub,
      help:
        typeof sub["x-huxley:help"] === "string" ? sub["x-huxley:help"] : null,
    });
  }
  return out;
}

function classify(schema: JsonSchema): FieldKind {
  // `format: "secret"` overrides type — it's always rendered as a
  // masked / "Set ✓" affordance, regardless of declared type.
  if (schema.format === "secret") return "secret";
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return "enum";
  const t = Array.isArray(schema.type) ? schema.type[0] : schema.type;
  switch (t) {
    case "string":
      return "string";
    case "number":
      return "number";
    case "integer":
      return "integer";
    case "boolean":
      return "boolean";
    case "array":
      // We support the simple case: arrays of strings. Anything else
      // (arrays of objects, nested arrays) falls through.
      if (
        schema.items &&
        !Array.isArray(schema.type) &&
        schema.items.type === "string"
      ) {
        return "array_of_strings";
      }
      return "unknown";
    case "object":
      return "object";
    default:
      return "unknown";
  }
}

// Format a current value for read-only display. Returns null when
// the value is absent (the renderer shows the schema's `default` or
// "—" instead). NEVER receives secret values — the server strips them.
export function formatValue(kind: FieldKind, value: unknown): string | null {
  if (value === undefined || value === null) return null;
  switch (kind) {
    case "string":
      return typeof value === "string" ? value : String(value);
    case "number":
    case "integer":
      return typeof value === "number" ? String(value) : null;
    case "boolean":
      return value === true ? "On" : value === false ? "Off" : null;
    case "enum":
      return typeof value === "string"
        ? value
        : typeof value === "number"
          ? String(value)
          : null;
    case "array_of_strings":
      return Array.isArray(value)
        ? value.filter((v): v is string => typeof v === "string").join(", ")
        : null;
    case "object":
    case "secret":
    case "unknown":
      // Secret VALUES never reach this code path (server strips).
      // Object & unknown render as raw JSON for debug.
      try {
        return JSON.stringify(value);
      } catch {
        return null;
      }
  }
}

// Pretty label: `safe_search` → `Safe Search`. Falls back to the raw
// name for anything that doesn't snake_case cleanly.
export function prettyLabel(name: string): string {
  return name
    .split(/[_\-]+/)
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}

// Parse the user-edited form value back into the JSON shape the
// server expects (Phase B). Inverse of `formatValue`. The renderer
// holds inputs as strings (text, comma-joined arrays) or booleans
// (toggles) and converts on Save. Returns `undefined` when the
// input can't be coerced — the caller surfaces a per-field error.
//
// Secret values are not handled here — secrets ride a different
// frame (`set_skill_secret`) and the renderer treats them as
// strings end-to-end.
export function parseValue(kind: FieldKind, raw: unknown): unknown | undefined {
  switch (kind) {
    case "string":
    case "secret":
      return typeof raw === "string" ? raw : undefined;
    case "number": {
      if (typeof raw === "number") return raw;
      if (typeof raw === "string") {
        const trimmed = raw.trim();
        if (trimmed === "") return undefined;
        const n = Number(trimmed);
        return Number.isFinite(n) ? n : undefined;
      }
      return undefined;
    }
    case "integer": {
      if (typeof raw === "number" && Number.isInteger(raw)) return raw;
      if (typeof raw === "string") {
        const trimmed = raw.trim();
        if (trimmed === "") return undefined;
        const n = Number(trimmed);
        return Number.isInteger(n) ? n : undefined;
      }
      return undefined;
    }
    case "boolean":
      return typeof raw === "boolean" ? raw : undefined;
    case "enum":
      // Enums round-trip as strings (or numbers for numeric enums);
      // any non-empty value is acceptable here, the schema's `enum`
      // list is enforced by the form rendering (we only show the
      // valid options).
      return typeof raw === "string" || typeof raw === "number"
        ? raw
        : undefined;
    case "array_of_strings": {
      // The renderer holds the array as a single comma-separated
      // string; split + trim + drop empties on Save. An empty input
      // becomes `[]` (not undefined) so the user can clear an array.
      if (Array.isArray(raw)) {
        return raw.filter((v): v is string => typeof v === "string");
      }
      if (typeof raw === "string") {
        return raw
          .split(",")
          .map((s) => s.trim())
          .filter((s) => s.length > 0);
      }
      return undefined;
    }
    case "object":
    case "unknown":
      // Objects + unknowns aren't editable in Phase B's form. The
      // renderer pins them as read-only "raw JSON"; on Save the
      // existing value is preserved unchanged.
      return raw;
  }
}
