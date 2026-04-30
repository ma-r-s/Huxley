// English UI catalog.
//
// Nested namespaces (orbStatus, call, sheet, etc.) mirror where the
// strings surface, so adding or renaming a key is local to one feature.
// Keys use camelCase; the framework `ui_strings` namespace (server-sent
// persona labels) keeps Python's snake_case so the two vocabularies
// stay recognizable.
const en = {
  translation: {
    // Orb status labels — fallback when the persona doesn't provide one.
    orbStatus: {
      idle: "Hold to talk",
      listening: "Listening — release to send",
      thinking: "Sent — awaiting response",
      speaking: "Responding",
      live: "Live — hold to end",
      playing: "Playing…",
      error: "Reconnecting…",
      paused: "Paused",
      wake: "Connecting",
    },
    orbHint: "Press & hold the circle. Or hold space.",
    // Active-call banner: "Talking with Mario"
    call: {
      talkingWith: "Talking with {{who}}",
    },
    mic: {
      cannotOpen: "Could not open microphone for the call",
      accessDenied: "Mic access denied — check browser permissions",
    },
    header: {
      sessions: "Sessions",
      device: "Device",
      connRetrying: "reconnecting…",
    },
    sessions: {
      title: "Conversations",
      recent: "Recent",
      empty: "Nothing yet — hold the orb to begin.",
      // pluralized via i18next: _one vs default
      turnsCount_one: "{{count}} turn",
      turnsCount_other: "{{count}} turns",
      sample: {
        1: "On the chapter you were reading…",
        2: "Set a timer while making pasta",
        3: "Walked through tomorrow's schedule",
      },
      when: {
        todayAt: "Today, {{time}}",
        yesterday: "Yesterday",
      },
    },
    logs: {
      title: "Logs",
      recent: "Current session",
      empty: "No events yet — start a turn or send a client_event.",
      clear: "Clear",
      statusTag: "status",
    },
    device: {
      title: "Device",
      headline: "Your Huxley",
      connected: "Connected",
      offline: "Offline",
      close: "Close",
      sections: {
        appearance: "Appearance",
        language: "Language",
        persona: "Persona",
        maintenance: "Maintenance",
      },
      appearance: {
        accent: "Accent",
        typeface: "Typeface",
        orbPersonality: "Orb personality",
        theme: "Theme",
        themeLight: "Light",
        themeLightDesc: "Warm coral",
        themeDark: "Dark",
        themeDarkDesc: "Evening",
        themeAuto: "Auto",
        themeAutoDesc: "Match system",
        expr: {
          subtle: "Subtle",
          natural: "Natural",
          expressive: "Expressive",
        },
        fontPair: {
          instrument: "Warm serif",
          fraunces: "Editorial",
          "all-sans": "Clean & modern",
          mono: "Terminal",
        },
        accents: {
          coral: "Coral",
          amber: "Amber",
          clay: "Clay",
          rose: "Rose",
          plum: "Plum",
          moss: "Moss",
        },
      },
      maintenance: {
        reloadSkills: "Reload skills",
        restartServer: "Restart server",
        viewLogs: "View logs",
      },
    },
  },
};

export default en;
