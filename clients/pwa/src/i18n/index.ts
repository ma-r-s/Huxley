import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.js";
import es from "./locales/es.js";
import fr from "./locales/fr.js";

// ── Supported languages ──────────────────────────────────────────────────
// The persona side of the stack lives in `personas/*/persona.yaml` and
// supports whatever codes the persona declares. The web shell ships with
// these three today; adding a fourth means: one file under `locales/`,
// one entry here, one entry in `LANGUAGE_NAMES`. No other wiring.
export const SUPPORTED_LANGUAGES = ["es", "en", "fr"] as const;
export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number];

// Human-readable labels shown in the language picker. Each label is in
// the target language (French readers see "Français", not "French").
export const LANGUAGE_NAMES: Record<LanguageCode, string> = {
  es: "Español",
  en: "English",
  fr: "Français",
};

export const DEFAULT_LANGUAGE: LanguageCode = "es";

// ── Persistence ──────────────────────────────────────────────────────────
// Language survives reloads via localStorage; falls back to the browser's
// navigator.language (first matching supported code) and finally to the
// framework default. Kept intentionally dumb — no server round-trip, no
// cookies; the app can reconnect to the Huxley server at any time with
// whatever the user last picked.
const STORAGE_KEY = "huxley-language";

function detectLanguage(): LanguageCode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && (SUPPORTED_LANGUAGES as readonly string[]).includes(stored)) {
      return stored as LanguageCode;
    }
  } catch {
    /* ignore — treat as first visit */
  }
  if (typeof navigator !== "undefined" && navigator.language) {
    const short = navigator.language.slice(0, 2).toLowerCase();
    if ((SUPPORTED_LANGUAGES as readonly string[]).includes(short)) {
      return short as LanguageCode;
    }
  }
  return DEFAULT_LANGUAGE;
}

export function saveLanguage(lang: LanguageCode): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* ignore — quota / private-mode */
  }
}

// ── i18next bootstrap ────────────────────────────────────────────────────
// `fallbackLng: "en"` means a missing key in the active language falls
// back to English, not to the key itself. Easier to spot a missing
// translation in testing — the English copy reads naturally, a literal
// key does not.
void i18n.use(initReactI18next).init({
  resources: { es, en, fr },
  lng: detectLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
