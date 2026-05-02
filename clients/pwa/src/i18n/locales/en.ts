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
      loading: "Loading…",
      live: "live",
      noTranscript: "(no transcript)",
      // pluralized via i18next: _one vs default
      turnsCount_one: "{{count}} turn",
      turnsCount_other: "{{count}} turns",
      when: {
        todayAt: "Today, {{time}}",
        yesterday: "Yesterday",
      },
    },
    sessionDetail: {
      title: "Transcript",
      recent: "Conversation",
      back: "Back",
      loading: "Loading…",
      empty: "No turns recorded.",
      user: "User",
      assistant: "Assistant",
      delete: "Delete",
      confirmDelete: "Delete this conversation? This cannot be undone.",
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
        skills: "Skills",
        maintenance: "Maintenance",
      },
      persona: {
        disabledHint:
          "Finish the current call or audio first — switching personas closes the active connection.",
      },
      skills: {
        manage: "Manage skills",
        loading: "Loading…",
        none: "None installed",
        summary: "{{enabled}} of {{total}} enabled",
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
    skills: {
      detailEyebrow: "Skill",
      back: "Back",
      enabled: "Enabled",
      disabled: "Disabled",
      noConfig: "This skill has no configurable settings.",
      dataSchemaVersion: "Data schema version",
      entryPointName: "Entry-point name",
      secretSet: "Set ✓",
      secretNotSet: "Not set",
      default: "Default",
      unset: "Not configured",
      secretBadge: "Secret",
      toggleHint: "Toggle to add or remove from the active persona.",
      toggleAria: "Toggle skill",
      save: "Save",
      saving: "Saving…",
      cancel: "Cancel",
      unsavedHint: "Unsaved changes.",
      savedHint: "All changes saved.",
      savingHint: "Saving — applying changes…",
      writesDisabledHint:
        "Editing skills interrupts an active call or audio stream. Wait for it to finish, then make your changes.",
      secretSave: "Save secret",
      secretUpdate: "Update",
      secretClear: "Clear",
      secretPlaceholder: "Paste the secret value",
    },
    install: {
      confirmTitle: "Install {{name}}?",
      noTagline: "No description provided.",
      warning:
        "This will run `uv add {{pkg}}` and restart the server. Any active call or stream is preserved (mid-call installs are blocked).",
      cancel: "Cancel",
      install: "Install",
      installing: "Installing {{pkg}}…",
      installingBody:
        "Running `uv add`. This may take a minute on first install (C-extension wheels build from source on slower machines).",
      restartingTitle: "Restarting server…",
      restartingBody:
        "The server is replacing itself with a fresh interpreter so the new skill's entry point is visible. ~5 seconds.",
      installedTitle: "Installed ✓",
      installedBody:
        "{{pkg}} is now available. Open the Skills tab to enable it on this persona.",
      errorTitle: "Install failed",
      errorGeneric: "uv add returned an error. Check the server log.",
      restartTimedOutTitle: "Server didn't come back",
      restartTimedOutBody:
        "The install completed but the server hasn't responded in 30 seconds. Check the server log (~/Library/Logs/Huxley/huxley.log) — the new skill may have a broken setup.",
      dismiss: "Done",
    },
    skillsSheet: {
      eyebrow: "Skills",
      close: "Close",
      headline: "Your skills",
      tabs: {
        installed: "Installed",
        marketplace: "Marketplace",
      },
      installed: {
        loading: "Loading…",
        empty:
          "No skills installed. Add one with `uv add huxley-skill-<name>` and restart the server.",
      },
      card: {
        noDescription: "No description provided.",
      },
      marketplace: {
        installButton: "Install",
        installedHint: "Already installed",
        loading: "Loading registry…",
        empty:
          "Registry is empty. Submit a PR at ma-r-s/huxley-registry to add a skill.",
        retry: "Retry",
        staleHint: "Showing cached registry — couldn't reach the live feed.",
        installed: "Installed ✓",
        tierFirst: "First-party",
        tierCommunity: "Community",
        tierExperimental: "Experimental",
      },
    },
  },
};

export default en;
