// i18next configuration for the landing site.
// Three languages: EN base, ES + FR. Every page renders in English first,
// on the server and in the browser's first pass, so the two match (React
// throws away and redraws a page whose first pass differs). Right after
// that, I18nProvider switches to the visitor's language:
//   1. Persisted user choice in localStorage (key LANG_KEY)
//   2. Browser preference (navigator.languages)
//   3. Fallback: en
// Every change after that is saved to localStorage and set on <html lang>.

import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import es from "./locales/es.json";
import fr from "./locales/fr.json";

export const SUPPORTED_LANGS = ["en", "es", "fr"] as const;
export type LangCode = (typeof SUPPORTED_LANGS)[number];

export const LANG_LABEL: Record<LangCode, string> = {
  en: "EN",
  es: "ES",
  fr: "FR",
};

export const LANG_KEY = "huxley-site-lang";

i18n
  .use(initReactI18next)
  .init({
    lng: "en", // the server's language; I18nProvider switches after hydration
    resources: {
      en: { translation: en },
      es: { translation: es },
      fr: { translation: fr },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGS as unknown as string[],
    nonExplicitSupportedLngs: true, // "en-US" matches "en"
    interpolation: { escapeValue: false }, // React handles escaping
  });

// The visitor's language: a saved choice, else the first supported one the
// browser prefers, else English.
export function preferredLang(): LangCode {
  const ok = (l: string | null | undefined) =>
    SUPPORTED_LANGS.find((c) => !!l && l.toLowerCase().split("-")[0] === c);
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(LANG_KEY);
  } catch {
    // storage blocked (private mode): fall through to the browser's
  }
  return ok(saved) ?? navigator.languages.map(ok).find(Boolean) ?? "en";
}

export default i18n;
