import type { ReactNode } from "react";
import {
  Instrument_Serif,
  Inter_Tight,
  JetBrains_Mono,
} from "next/font/google";
import { RootProvider } from "fumadocs-ui/provider/next";
import "./global.css";

// Match the marketing site's typographic identity exactly:
// - Instrument Serif (italic) for the wordmark and biggest headings
// - Inter Tight for body and UI
// - JetBrains Mono for code
const instrumentSerif = Instrument_Serif({
  weight: "400",
  style: ["normal", "italic"],
  subsets: ["latin"],
  variable: "--font-instrument-serif",
  display: "swap",
});

const interTight = Inter_Tight({
  subsets: ["latin"],
  variable: "--font-inter-tight",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata = {
  metadataBase: new URL("https://huxley.example.com"),
  title: {
    default: "Huxley docs",
    template: "%s — Huxley docs",
  },
  description:
    "Build voice agents you own. Bring a persona and skills — Huxley handles turn coordination, interrupts, proactive speech, and audio bridging.",
};

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${instrumentSerif.variable} ${interTight.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <body className="flex flex-col min-h-screen">
        <RootProvider>{children}</RootProvider>
      </body>
    </html>
  );
}
