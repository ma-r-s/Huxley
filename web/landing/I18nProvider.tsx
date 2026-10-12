"use client";

import { useEffect, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import i18n, { LANG_KEY, preferredLang } from "./i18n/index";

export function I18nProvider({ children }: { children: ReactNode }) {
  // After hydration (the first pass is English, like the server's): the
  // visitor's language, then every later change saved and set on <html>,
  // so screen readers read each language with its own voice.
  useEffect(() => {
    const apply = (lng: string) => {
      document.documentElement.lang = lng;
      try {
        localStorage.setItem(LANG_KEY, lng);
      } catch {
        // storage blocked: the choice lasts until the page closes
      }
    };
    i18n.on("languageChanged", apply);
    const lng = preferredLang();
    if (lng !== i18n.language) void i18n.changeLanguage(lng);
    else document.documentElement.lang = lng;
    return () => i18n.off("languageChanged", apply);
  }, []);
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}
