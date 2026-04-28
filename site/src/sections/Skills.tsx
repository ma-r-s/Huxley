// Skills grid — ~70 tiles (shipped + designed-for) staggering in once the
// section enters view, with a category filter row above. Below the grid,
// a code example showing what a skill looks like in Python.

import { useEffect, useState } from "react";
import { useRegisterSection, useInView } from "../lib/voiceThread.js";
import { useViewport } from "../lib/useViewport.js";
import { SectionHead } from "../components/Chrome.js";

interface Skill {
  id: string;
  cat: string;
  name: string;
  shipped?: boolean;
}

const ALL_SKILLS: Skill[] = [
  { id: "audiobooks", cat: "Audio", name: "Audiobooks", shipped: true },
  { id: "radio", cat: "Audio", name: "Radio", shipped: true },
  { id: "news", cat: "Info", name: "News + Weather", shipped: true },
  { id: "timers", cat: "System", name: "Timers", shipped: true },
  { id: "system", cat: "System", name: "System", shipped: true },
  { id: "comms-telegram", cat: "Comms", name: "Telegram Calls", shipped: true },

  { id: "sms", cat: "Comms", name: "SMS" },
  { id: "whatsapp", cat: "Comms", name: "WhatsApp" },
  { id: "email", cat: "Comms", name: "Email" },
  { id: "comms-pstn", cat: "Comms", name: "Phone (PSTN)" },
  { id: "slack", cat: "Comms", name: "Slack" },
  { id: "signal", cat: "Comms", name: "Signal" },

  { id: "hue", cat: "Home", name: "Philips Hue" },
  { id: "homeassistant", cat: "Home", name: "Home Assistant" },
  { id: "thermostat", cat: "Home", name: "Thermostat" },
  { id: "doorbell", cat: "Home", name: "Doorbell" },
  { id: "tv", cat: "Home", name: "TV" },
  { id: "sonos", cat: "Home", name: "Sonos" },
  { id: "robot-vacuum", cat: "Home", name: "Robot Vacuum" },
  { id: "air-quality", cat: "Home", name: "Air Quality" },
  { id: "appliances", cat: "Home", name: "Appliances" },

  { id: "podcasts", cat: "Audio", name: "Podcasts" },
  { id: "spotify", cat: "Audio", name: "Spotify" },
  { id: "youtube-audio", cat: "Audio", name: "YouTube Audio" },
  { id: "ambient", cat: "Audio", name: "Ambient" },
  { id: "text-to-audio", cat: "Audio", name: "Text-to-audio" },

  { id: "calendar", cat: "Productivity", name: "Calendar" },
  { id: "tasks", cat: "Productivity", name: "Tasks" },
  { id: "notes", cat: "Productivity", name: "Notes" },
  { id: "shopping-list", cat: "Productivity", name: "Shopping list" },
  { id: "reminders", cat: "Productivity", name: "Reminders" },
  { id: "focus", cat: "Productivity", name: "Focus / Pomodoro" },

  { id: "search", cat: "Info", name: "Web search" },
  { id: "wikipedia", cat: "Info", name: "Wikipedia" },
  { id: "translate", cat: "Info", name: "Translate" },
  { id: "dictionary", cat: "Info", name: "Dictionary" },
  { id: "wolfram", cat: "Info", name: "Wolfram" },
  { id: "flights", cat: "Info", name: "Flights" },
  { id: "packages", cat: "Info", name: "Packages" },
  { id: "stocks", cat: "Info", name: "Stocks" },
  { id: "sports", cat: "Info", name: "Sports" },
  { id: "traffic", cat: "Info", name: "Traffic" },

  { id: "medications", cat: "Care", name: "Medications" },
  { id: "vitals-log", cat: "Care", name: "Vitals log" },
  { id: "hydration", cat: "Care", name: "Hydration" },
  { id: "breathing", cat: "Care", name: "Breathing" },
  { id: "sleep", cat: "Care", name: "Sleep" },
  { id: "mood", cat: "Care", name: "Mood" },
  { id: "emergency", cat: "Care", name: "Emergency" },

  { id: "budget", cat: "Finance", name: "Budget (Plaid)" },
  { id: "expense-log", cat: "Finance", name: "Expense log" },
  { id: "bills", cat: "Finance", name: "Bills" },
  { id: "crypto", cat: "Finance", name: "Crypto" },

  { id: "vision", cat: "Vision", name: "Vision (camera)" },
  { id: "pdf-reader", cat: "Vision", name: "PDF reader" },
  { id: "scan", cat: "Vision", name: "Scan" },
  { id: "face-greeting", cat: "Vision", name: "Face greeting" },

  { id: "github", cat: "Dev", name: "GitHub" },
  { id: "server-monitor", cat: "Dev", name: "Server monitor" },
  { id: "shell", cat: "Dev", name: "Shell" },
  { id: "ntfy", cat: "Dev", name: "ntfy" },

  { id: "flashcards", cat: "Edu", name: "Flashcards" },
  { id: "language-tutor", cat: "Edu", name: "Language tutor" },
  { id: "storytime", cat: "Edu", name: "Storytime" },
  { id: "quiz", cat: "Edu", name: "Quiz" },

  { id: "recipes", cat: "Daily", name: "Recipes" },
  { id: "grocery-delivery", cat: "Daily", name: "Grocery delivery" },
  { id: "food-log", cat: "Daily", name: "Food log" },
  { id: "journal", cat: "Daily", name: "Journal" },
  { id: "affirmations", cat: "Daily", name: "Affirmations" },
];

export function Skills() {
  const sectionRef = useRegisterSection<HTMLElement>("skills", "speaking");
  // Separate sentinel: useInView attaches its own ref to a small div near the
  // top of the section. We don't try to share a ref with sectionRef because
  // each hook needs its own element to observe.
  const [sentinelRef, inView] = useInView<HTMLDivElement>(0.15);
  const { isMobile } = useViewport();
  const [filter, setFilter] = useState("All");
  const cats = ["All", ...Array.from(new Set(ALL_SKILLS.map((s) => s.cat)))];

  // Stagger the tiles in once the section enters view.
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!inView) return;
    let i = 0;
    const id = setInterval(() => {
      i += 3;
      setCount((c) => Math.min(ALL_SKILLS.length, c + 3));
      if (i >= ALL_SKILLS.length) clearInterval(id);
    }, 40);
    return () => clearInterval(id);
  }, [inView]);

  const visible =
    filter === "All" ? ALL_SKILLS : ALL_SKILLS.filter((s) => s.cat === filter);
  const shipped = ALL_SKILLS.filter((s) => s.shipped).length;

  return (
    <section
      id="skills"
      ref={sectionRef}
      style={{
        position: "relative",
        zIndex: 2,
        padding: isMobile ? "72px 24px" : "120px 64px",
        borderTop: "1px solid var(--hux-fg-line)",
      }}
    >
      <SectionHead
        eyebrow="§ 04 — Skill system"
        title={
          <>
            Anything that speaks Python
            <br />
            <em style={{ fontStyle: "italic" }}>is a skill.</em>
          </>
        }
        subtitle="Skills register via Python entry-points. Add a line to persona.yaml, restart — the framework never changes."
      />

      {/* Invisible sentinel — gates the tile stagger animation */}
      <div ref={sentinelRef} aria-hidden style={{ height: 1 }} />

      <div
        style={{
          marginTop: 48,
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        {cats.map((c) => (
          <button
            key={c}
            onClick={() => setFilter(c)}
            style={{
              padding: "7px 14px",
              borderRadius: 999,
              background: filter === c ? "var(--hux-fg)" : "transparent",
              color: filter === c ? "var(--hux-coral)" : "var(--hux-fg)",
              border: "1px solid var(--hux-fg-line)",
              fontFamily: "var(--hux-sans)",
              fontSize: 12,
              letterSpacing: "0.04em",
              cursor: "pointer",
              transition: "background 180ms ease, color 180ms ease",
            }}
          >
            {c}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            fontFamily: "var(--hux-mono)",
            fontSize: 11,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            opacity: 0.65,
          }}
        >
          <span>
            <b style={{ color: "var(--hux-fg)" }}>{shipped}</b> shipped
          </span>
          <span style={{ opacity: 0.5 }}>·</span>
          <span>
            <b style={{ color: "var(--hux-fg)" }}>
              {ALL_SKILLS.length - shipped}
            </b>{" "}
            designed for
          </span>
          <span style={{ opacity: 0.5 }}>·</span>
          <span>
            <b style={{ color: "var(--hux-fg)" }}>∞</b> possible
          </span>
        </div>
      </div>

      <div
        style={{
          marginTop: 32,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
          gap: 0,
          borderTop: "1px solid var(--hux-fg-line)",
          borderLeft: "1px solid var(--hux-fg-line)",
        }}
      >
        {visible.map((s, i) => {
          const showing = inView && i < count;
          const matches = filter === "All" || s.cat === filter;
          return (
            <div
              key={s.id}
              style={{
                borderRight: "1px solid var(--hux-fg-line)",
                borderBottom: "1px solid var(--hux-fg-line)",
                padding: "16px 18px",
                minHeight: 84,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                background: s.shipped
                  ? "color-mix(in oklab, var(--hux-fg) 7%, transparent)"
                  : "transparent",
                opacity: showing && matches ? 1 : 0,
                transform:
                  showing && matches ? "translateY(0)" : "translateY(8px)",
                transition: `opacity 500ms cubic-bezier(.22,.9,.27,1) ${i * 15}ms, transform 500ms cubic-bezier(.22,.9,.27,1) ${i * 15}ms`,
              }}
            >
              <div
                style={{
                  fontFamily: "var(--hux-mono)",
                  fontSize: 9,
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  opacity: 0.55,
                  marginBottom: 6,
                }}
              >
                {s.cat}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {s.shipped && (
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: 999,
                      background: "var(--hux-fg)",
                      boxShadow: "0 0 8px var(--hux-fg)",
                      flexShrink: 0,
                    }}
                  />
                )}
                <span
                  style={{
                    fontFamily: "var(--hux-serif)",
                    fontSize: 18,
                    lineHeight: 1.2,
                  }}
                >
                  {s.name}
                </span>
              </div>
              <div
                style={{
                  fontFamily: "var(--hux-mono)",
                  fontSize: 9,
                  letterSpacing: "0.1em",
                  opacity: 0.4,
                  marginTop: 4,
                }}
              >
                huxley-skill-{s.id}
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 56,
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr",
          gap: 32,
          alignItems: "start",
        }}
      >
        <div>
          <div className="eyebrow" style={{ opacity: 0.6, marginBottom: 16 }}>
            § 04.1 — Writing one
          </div>
          <h3
            style={{
              fontFamily: "var(--hux-serif)",
              fontWeight: 400,
              fontSize: 36,
              lineHeight: 1.1,
              letterSpacing: "-0.01em",
              margin: "0 0 16px",
            }}
          >
            A skill is a Python package.
          </h3>
          <p
            style={{
              fontFamily: "var(--hux-serif)",
              fontStyle: "italic",
              fontSize: 18,
              lineHeight: 1.45,
              opacity: 0.8,
              maxWidth: 460,
            }}
          >
            Declare tools. Handle calls. Return a ToolResult — optionally with
            an AudioStream, a PlaySound, or an InputClaim for full-duplex audio.
            The framework sequences the rest.
          </p>
        </div>
        <pre
          style={{
            margin: 0,
            padding: 24,
            borderRadius: 14,
            background: "rgba(0,0,0,0.32)",
            border: "1px solid var(--hux-fg-line)",
            fontFamily: "var(--hux-mono)",
            fontSize: 12,
            lineHeight: 1.65,
            color: "var(--hux-fg)",
            overflow: "auto",
          }}
        >
          {`class LightsSkill:
    name = "lights"
    tools = [ToolDefinition(
        name="set_lights",
        description="Turn the lights on or off.",
        parameters={"on": "boolean"},
    )]

    async def handle(self, tool, args):
        await hue.set(args["on"])
        return ToolResult(output='{"ok": true}')

# pyproject.toml
[project.entry-points."huxley.skills"]
lights = "my_package.skill:LightsSkill"`}
        </pre>
      </div>
    </section>
  );
}
