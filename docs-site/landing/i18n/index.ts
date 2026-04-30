// i18next configuration for the landing site.
// Three languages — EN base, ES + FR drafts (machine-translated; Mario and
// reviewers will polish before launch). Locale chosen by:
//   1. Persisted user choice in localStorage (key "huxley-site-lang")
//   2. Browser preference (navigator.language)
//   3. Fallback: en
//
// The LangToggle in Chrome.tsx writes back to localStorage and i18n.

import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
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

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      es: { translation: es },
      fr: { translation: fr },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGS as unknown as string[],
    nonExplicitSupportedLngs: true, // "en-US" matches "en"
    interpolation: { escapeValue: false }, // React handles escaping
    detection: {
      order: ["localStorage", "navigator", "htmlTag"],
      lookupLocalStorage: "huxley-site-lang",
      caches: ["localStorage"],
    },
  });

export default i18n;
