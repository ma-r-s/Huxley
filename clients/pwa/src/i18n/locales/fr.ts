// French UI catalog — mirrors `en.ts` one key at a time.
const fr = {
  translation: {
    orbStatus: {
      idle: "Maintiens pour parler",
      listening: "À l'écoute — relâche pour envoyer",
      thinking: "Envoyé — en attente de réponse",
      speaking: "Je réponds",
      live: "En direct — maintiens pour terminer",
      playing: "Lecture en cours…",
      error: "Reconnexion…",
      paused: "En pause",
      wake: "Connexion",
    },
    orbHint: "Maintiens le cercle appuyé. Ou maintiens espace.",
    call: {
      talkingWith: "En communication avec {{who}}",
    },
    mic: {
      cannotOpen: "Impossible d'ouvrir le microphone pour l'appel",
      accessDenied: "Micro refusé — vérifie les autorisations du navigateur",
    },
    header: {
      sessions: "Sessions",
      device: "Appareil",
      connRetrying: "reconnexion…",
    },
    sessions: {
      title: "Conversations",
      recent: "Récentes",
      empty: "Rien pour l'instant — maintiens la sphère pour commencer.",
      turnsCount_one: "{{count}} tour",
      turnsCount_other: "{{count}} tours",
      sample: {
        1: "Sur le chapitre que tu lisais…",
        2: "On a mis une minuterie en faisant des pâtes",
        3: "On a parcouru l'agenda de demain",
      },
      when: {
        todayAt: "Aujourd'hui, {{time}}",
        yesterday: "Hier",
      },
    },
    logs: {
      title: "Journaux",
      recent: "Session en cours",
      empty: "Aucun événement — démarrez un tour ou envoyez un client_event.",
      clear: "Effacer",
      statusTag: "statut",
    },
    device: {
      title: "Appareil",
      headline: "Ton Huxley",
      connected: "Connecté",
      offline: "Hors ligne",
      close: "Fermer",
      sections: {
        appearance: "Apparence",
        language: "Langue",
        persona: "Persona",
        maintenance: "Maintenance",
      },
      appearance: {
        accent: "Accent",
        typeface: "Typographie",
        orbPersonality: "Personnalité de la sphère",
        theme: "Thème",
        themeLight: "Clair",
        themeLightDesc: "Corail chaleureux",
        themeDark: "Sombre",
        themeDarkDesc: "Soirée",
        themeAuto: "Auto",
        themeAutoDesc: "Selon le système",
        expr: {
          subtle: "Subtile",
          natural: "Naturelle",
          expressive: "Expressive",
        },
        fontPair: {
          instrument: "Serif chaleureuse",
          fraunces: "Éditoriale",
          "all-sans": "Claire et moderne",
          mono: "Terminal",
        },
        accents: {
          coral: "Corail",
          amber: "Ambre",
          clay: "Argile",
          rose: "Rose",
          plum: "Prune",
          moss: "Mousse",
        },
      },
      maintenance: {
        reloadSkills: "Recharger les skills",
        restartServer: "Redémarrer le serveur",
        viewLogs: "Voir les logs",
      },
    },
  },
};

export default fr;
