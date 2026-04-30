"use client";
// Personas grid + active-persona detail. Six personas (Abuelo one of them);
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
      ["Voice", 'OpenAI Realtime · "alloy" at 0.85×'],
      ["Language", "es-ES · slow · warm"],
      ["Skills", "audiobooks · radio · news · timers · comms-telegram"],
      ["Hardware", "WebSocket client, mic + speaker"],
      ["Rule", "never_say_no"],
    ],
    yaml: `name: Abuelo
voice: alloy
language_code: es
system_prompt: |
  Eres Abuelo. Hablas despacio, con calma.
  Nunca dices "no puedo". Siempre intentas ayudar.
constraints: [never_say_no, confirm_destructive]
skills:
  audiobooks: { library: /media/audiolibros }
  radio: {}
  news: { location: Madrid, ES }
  timers: {}
  comms-telegram: { api_id: ..., api_hash: ... }`,
  },
  {
    id: "studio",
    name: "Studio",
    tag: "Writer’s desk companion",
    desc: "Quiet, precise. Captures thoughts mid-sentence, keeps a running journal, never interrupts.",
    facets: [
      ["Voice", "Neutral · terse · observational"],
      ["Language", "en-US · normal cadence"],
      ["Skills", "journal · notes · dictionary · translate · focus"],
      ["Hardware", "Desktop browser · push-to-talk"],
      ["Rule", "speak_only_when_asked"],
    ],
    yaml: `name: Studio
voice: echo
language_code: en
system_prompt: |
  You are a quiet writing companion.
  Never interrupt. Speak only when asked.
  Keep answers to one sentence unless expanded.
constraints: [speak_only_when_asked, no_small_talk]
skills:
  journal: { path: ~/Documents/journal }
  notes: {}
  dictionary: {}
  translate: {}
  focus: { default: 25m }`,
  },
  {
    id: "household",
    name: "Household",
    tag: "Family kitchen counter",
    desc: "Shared by a family of five. Lists, schedules, who-picks-up-who, the timer that saved the roast.",
    facets: [
      ["Voice", "Bright · efficient · bilingual"],
      ["Language", "en-US / es-MX auto-switch"],
      [
        "Skills",
        "shopping-list · calendar · reminders · hue · recipes · timers",
      ],
      ["Hardware", "ESP32 countertop puck"],
      ["Rule", "multi_user_voice_id"],
    ],
    yaml: `name: Household
voice: nova
language_code: auto
system_prompt: |
  Shared family assistant. Confirm who is asking
  when tasks are personal. Keep answers brief.
constraints: [multi_user_voice_id, confirm_purchases]
skills:
  shopping-list: { shared: true }
  calendar: { provider: google }
  reminders: {}
  hue: { bridge: 10.0.1.4 }
  recipes: {}
  timers: {}`,
  },
  {
    id: "devops",
    name: "Ops",
    tag: "On-call SRE",
    desc: "Wakes you at 3am. Reads the alert, pages the runbook, can restart the service if you say the word.",
    facets: [
      ["Voice", "Flat · urgent · no filler"],
      ["Language", "en-US · technical"],
      ["Skills", "server-monitor · github · shell · ntfy · pagerduty"],
      ["Hardware", "Phone app · Bluetooth headset"],
      ["Rule", "confirm_destructive_twice"],
    ],
    yaml: `name: Ops
voice: echo
language_code: en
system_prompt: |
  On-call SRE assistant. Lead with severity.
  Read the alert, then the runbook. Never act
  on prod without double confirmation.
constraints: [confirm_destructive_twice, audit_log]
skills:
  server-monitor: { targets: [api, web, db] }
  github: { org: acme }
  shell: { allow: [kubectl, systemctl] }
  ntfy: {}
  pagerduty: {}`,
  },
  {
    id: "tutor",
    name: "Tutor",
    tag: "After-school reading coach",
    desc: "For a 9-year-old learning to read in French. Patient. Repeats. Celebrates small wins.",
    facets: [
      ["Voice", "Cheerful · clear · encouraging"],
      ["Language", "fr-FR · slow"],
      [
        "Skills",
        "storytime · flashcards · quiz · language-tutor · affirmations",
      ],
      ["Hardware", "Tablet · touch-to-talk"],
      ["Rule", "positive_only"],
    ],
    yaml: `name: Tutor
voice: shimmer
language_code: fr
system_prompt: |
  Tu es un tuteur de lecture pour un enfant.
  Parle lentement. Félicite les essais.
  Jamais de correction négative.
constraints: [positive_only, session_cap_20m]
skills:
  storytime: { library: /media/livres }
  flashcards: { deck: cp }
  quiz: {}
  language-tutor: { level: a1 }
  affirmations: {}`,
  },
  {
    id: "clinic",
    name: "Clinic",
    tag: "Solo practitioner front desk",
    desc: "Books, reschedules, reads back charts. Transcripts stay on the local machine; the persona pattern shows how to keep PHI out of cloud logs (regulatory compliance is the operator's responsibility).",
    facets: [
      ["Voice", "Professional · calm · clinical"],
      ["Language", "en-US · medical"],
      ["Skills", "calendar · medications · vitals-log · email · pdf-reader"],
      ["Hardware", "Desk microphone · local server"],
      ["Rule", "no_cloud_storage"],
    ],
    yaml: `name: Clinic
voice: alloy
language_code: en
system_prompt: |
  Front-desk assistant for a solo clinic.
  Never discuss PHI outside an active session.
  All transcripts stay on this machine.
constraints: [no_cloud_storage, redact_pii_in_logs]
skills:
  calendar: { provider: local }
  medications: {}
  vitals-log: {}
  email: { provider: imap }
  pdf-reader: {}`,
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
