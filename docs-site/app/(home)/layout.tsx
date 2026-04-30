import type { ReactNode } from "react";
import { I18nProvider } from "@/landing/I18nProvider";

export default function Layout({ children }: { children: ReactNode }) {
  return <I18nProvider>{children}</I18nProvider>;
}
