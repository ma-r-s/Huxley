"use client";
// Personas grid + active-persona detail. Five real shipped personas;
// click a cell to swap the description + YAML. Cells stagger in.

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useRegisterSection, useInView } from "../lib/voiceThread";
import { useViewport } from "../lib/useViewport";
import { SectionHead, coralSection } from "../components/Chrome";

interface Persona {
  id: string;
  name: string;
  tag: string;
  desc: string;
  facets: Array<[string, string]>;
  yaml: string;
}

const PERSONAS: Persona[] = [
  {
    id: "abuelos",
    name: "Abuelo",
    tag: "Elderly companion · Español",
    desc: "Slow warm voice for an elderly blind user. Audiobooks, radio, Telegram calls. Never says no.",
    facets: [
      ["Voice", 'OpenAI Realtime · "coral" · warm'],
      ["Language", "es · slow · warm"],
      ["Skills", "audiobooks · radio · news · search · timers · reminders · telegram"],
      ["Hardware", "WebSocket client, mic + speaker"],
      ["Rule", "never_say_no"],
    ],
    yaml: `name: Abuelo
voice: coral
language_code: es
system_prompt: |
  Eres un asistente de voz para una persona mayor ciega.
  Hablas despacio, con calma. Nunca dices "no puedo".
  Frases cortas. Palabras sencillas.
constraints: [never_say_no, echo_short_input, confirm_if_unclear]
skills:
  audiobooks: { library: audiobooks }
  radio: {}
  news: { location: Villavicencio, CO }
  search: { safesearch: moderate }
  timers: {}
  reminders: { timezone: America/Bogota }
  telegram: { contacts: { ... } }`,
  },
  {
    id: "basicos",
    name: "Basic",
    tag: "Developer reference · English",
    desc: "Minimal, terse English assistant. Two skills, a short prompt. The reference implementation for building your own persona.",
    facets: [
      ["Voice", 'OpenAI Realtime · "alloy" · neutral'],
      ["Language", "en · terse · neutral"],
      ["Skills", "news · system"],
      ["Hardware", "WebSocket client, any browser"],
      ["Rule", "confirm_destructive"],
    ],
    yaml: `name: Basic
voice: alloy
language_code: en
system_prompt: |
  Direct personal assistant. No greetings, no fluff.
  Reply with the fewest words needed.
  NEVER invent news — call get_news first.
constraints: [confirm_destructive]
skills:
  news: { location: New York, US, max_items: 5 }
  system: {}`,
  },
  {
    id: "chief",
    name: "Chief",
    tag: "Executive assistant · English",
    desc: "Action-oriented EA. Tracks tasks, searches facts, sets reminders. Gets things done without filler.",
    facets: [
      ["Voice", 'OpenAI Realtime · "echo" · professional'],
      ["Language", "en · direct · efficient"],
      ["Skills", "system · news · search · timers · reminders"],
      ["Hardware", "WebSocket client, any browser"],
      ["Rule", "confirm_destructive"],
    ],
    yaml: `name: Chief
voice: echo
language_code: en
system_prompt: |
  Executive assistant. Action-oriented, terse, reliable.
  Lead with what matters. Never invent facts.
  Confirm before any irreversible action.
constraints: [confirm_destructive]
skills:
  system: {}
  news: { location: New York, max_items: 3 }
  search: { safesearch: moderate }
  timers: {}
  reminders: { timezone: America/New_York }`,
  },
  {
    id: "librarian",
    name: "Librarian",
    tag: "Research authority · English",
    desc: "Quiet, precise. Retrieves from audiobooks, search, and news. Cites sources. Never invents.",
    facets: [
      ["Voice", 'OpenAI Realtime · "sage" · measured'],
      ["Language", "en · precise · complete sentences"],
      ["Skills", "audiobooks · search · news · system"],
      ["Hardware", "WebSocket client, any browser"],
      ["Rule", "confirm_destructive"],
    ],
    yaml: `name: Librarian
voice: sage
language_code: en
system_prompt: |
  Research librarian. Precise, authoritative.
  Retrieve and synthesize — never invent.
  Attribute the source when you know it.
constraints: [confirm_destructive]
skills:
  audiobooks: { library: audiobooks }
  search: { safesearch: moderate }
  news: { location: New York, max_items: 5 }
  system: {}`,
  },
  {
    id: "buddy",
    name: "Buddy",
    tag: "Kids companion · English",
    desc: "Friendly companion for kids. Simple words, cheerful tone, age-appropriate content. Never refuses — always finds a way.",
    facets: [
      ["Voice", 'OpenAI Realtime · "shimmer" · warm'],
      ["Language", "en · simple · cheerful"],
      ["Skills", "system · news · search · timers"],
      ["Hardware", "WebSocket client, tablet or phone"],
      ["Rule", "never_say_no · child_safe"],
    ],
    yaml: `name: Buddy
voice: shimmer
language_code: en
system_prompt: |
  Friendly companion for kids. Simple words,
  cheerful tone. Never refuse — always find
  a way to help. Age-appropriate content only.
constraints: [never_say_no, child_safe, echo_short_input]
skills:
  system: {}
  news: { location: New York, max_items: 3 }
  search: { safesearch: strict }
  timers: {}`,
  },
];

function PersonaCell({
  idx,
  isActive,
  x,
  onClick,
}: {
  idx: number;
  isActive: boolean;
  x: Persona;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  const [ref, seen] = useInView<HTMLButtonElement>(0.2);
  const delay = idx * 70;
  return (
    <button
      ref={ref}
      onClick={onClick}
      style={{
        textAlign: "left",
        borderRight: "1px solid var(--hux-fg-line)",
        borderBottom: "1px solid var(--hux-fg-line)",
        borderTop: "none",
        borderLeft: "none",
        padding: "20px 18px",
        background: isActive
          ? "color-mix(in oklab, var(--hux-fg) 10%, transparent)"
          : "transparent",
        color: "var(--hux-fg)",
        cursor: "pointer",
        minHeight: 120,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        opacity: seen ? 1 : 0,
        transform: seen ? "translateY(0)" : "translateY(22px)",
        transition: `background 200ms ease, opacity 650ms cubic-bezier(.2,.7,.2,1) ${delay}ms, transform 650ms cubic-bezier(.2,.7,.2,1) ${delay}ms`,
        willChange: "opacity, transform",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontFamily: "var(--hux-mono)",
          fontSize: 9,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          opacity: 0.55,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: isActive ? "var(--hux-fg)" : "transparent",
            border: "1px solid var(--hux-fg)",
            boxShadow: isActive ? "0 0 8px var(--hux-fg)" : "none",
          }}
        />
        {t("personas.personaLabel")}
      </div>
      <div
        style={{
          fontFamily: "var(--hux-serif)",
          fontStyle: "italic",
          fontSize: 26,
          lineHeight: 1.05,
          letterSpacing: "-0.01em",
        }}
      >
        {x.name}
      </div>
      <div style={{ fontSize: 12, opacity: 0.7, lineHeight: 1.35 }}>
        {x.tag}
      </div>
    </button>
  );
}

export function Personas() {
  const { t } = useTranslation();
  const sectionRef = useRegisterSection<HTMLElement>("persona", "thinking");
  const { isMobile, isTablet } = useViewport();
  const [active, setActive] = useState(PERSONAS[0]!.id);
  const p = PERSONAS.find((x) => x.id === active) || PERSONAS[0]!;
  return (
    <section
      ref={sectionRef}
      id="persona"
      style={{
        ...coralSection,
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
      }}
    >
      <SectionHead
        eyebrow={t("personas.eyebrow")}
        title={
          <>
            {t("personas.titleA")}
            <br />
            <em style={{ fontStyle: "italic" }}>{t("personas.titleB")}</em>
          </>
        }
        subtitle={t("personas.subtitle")}
      />

      <div
        style={{
          marginTop: isMobile ? 32 : 56,
          display: "grid",
          gridTemplateColumns: isMobile
            ? "repeat(2, 1fr)"
            : isTablet
              ? "repeat(3, 1fr)"
              : `repeat(${PERSONAS.length}, 1fr)`,
          gap: 0,
          borderTop: "1px solid var(--hux-fg-line)",
          borderLeft: "1px solid var(--hux-fg-line)",
        }}
      >
        {PERSONAS.map((x, idx) => (
          <PersonaCell
            key={x.id}
            idx={idx}
            isActive={x.id === active}
            x={x}
            onClick={() => setActive(x.id)}
          />
        ))}
      </div>

      <div
        style={{
          marginTop: 40,
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr",
          gap: isMobile ? 32 : 48,
          alignItems: "start",
        }}
      >
        <div>
          <div
            style={{
              fontFamily: "var(--hux-serif)",
              fontStyle: "italic",
              fontSize: 28,
              lineHeight: 1.3,
              opacity: 0.92,
              maxWidth: 520,
              marginBottom: 28,
              textWrap: "pretty",
            }}
          >
            {p.desc}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
            {p.facets.map(([k, v]) => {
              // The persona data stores facet keys as English labels; map to
              // i18n keys when we recognise them, otherwise fall back to the
              // raw label (so unknown future facets still render).
              const keyLookup: Record<string, string> = {
                Voice: "voice",
                Language: "language",
                Skills: "skills",
                Hardware: "hardware",
                Rule: "rule",
              };
              const i18nKey = keyLookup[k];
              const label = i18nKey ? t(`personas.facetKeys.${i18nKey}`) : k;
              return (
                <div
                  key={k}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "110px 1fr",
                    gap: 16,
                    padding: "12px 0",
                    borderTop: "1px solid var(--hux-fg-line)",
                  }}
                >
                  <div
                    className="mono"
                    style={{
                      fontSize: 10,
                      letterSpacing: "0.14em",
                      textTransform: "uppercase",
                      opacity: 0.6,
                    }}
                  >
                    {label}
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--hux-sans)",
                      fontSize: 14,
                      lineHeight: 1.45,
                    }}
                  >
                    {v}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <pre
          key={p.id}
          style={{
            margin: 0,
            padding: 26,
            borderRadius: 16,
            background: "rgba(0,0,0,0.35)",
            border: "1px solid var(--hux-fg-line)",
            fontFamily: "var(--hux-mono)",
            fontSize: 12,
            lineHeight: 1.7,
            color: "var(--hux-fg)",
            overflow: "auto",
            animation: "hux-fade-up 400ms cubic-bezier(.22,.9,.27,1)",
          }}
        >
          {`# personas/${p.id}/persona.yaml\n${p.yaml}`}
        </pre>
      </div>
    </section>
  );
}
