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
    sessionDetail: {
      title: "Transcription",
      recent: "Conversation",
      back: "Retour",
      loading: "Chargement…",
      empty: "Aucun tour enregistré.",
      user: "Utilisateur",
      assistant: "Assistant",
      delete: "Supprimer",
      confirmDelete: "Supprimer cette conversation ? Action irréversible.",
    },
    sessions: {
      title: "Conversations",
      recent: "Récentes",
      empty: "Rien pour l'instant — maintiens la sphère pour commencer.",
      loading: "Chargement…",
      live: "en cours",
      noTranscript: "(pas de transcription)",
      turnsCount_one: "{{count}} tour",
      turnsCount_other: "{{count}} tours",
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
        skills: "Compétences",
        maintenance: "Maintenance",
      },
      persona: {
        disabledHint:
          "Termine d'abord l'appel ou l'audio en cours — changer de persona ferme la connexion active.",
      },
      skills: {
        manage: "Gérer les compétences",
        loading: "Chargement…",
        none: "Aucune installée",
        summary: "{{enabled}} sur {{total}} actives",
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
    skills: {
      detailEyebrow: "Compétence",
      back: "Retour",
      enabled: "Active",
      disabled: "Inactive",
      noConfig: "Cette compétence n'a aucun réglage configurable.",
      dataSchemaVersion: "Version du schéma de données",
      entryPointName: "Nom de l'entry-point",
      secretSet: "Configuré ✓",
      secretNotSet: "Non configuré",
      default: "Par défaut",
      unset: "Non configuré",
      secretBadge: "Secret",
    },
    skillsSheet: {
      eyebrow: "Compétences",
      close: "Fermer",
      headline: "Tes compétences",
      tabs: {
        installed: "Installées",
        marketplace: "Marketplace",
      },
      installed: {
        loading: "Chargement…",
        empty:
          "Aucune compétence installée. Ajoute-en une avec `uv add huxley-skill-<nom>` et redémarre le serveur.",
      },
      card: {
        noDescription: "Aucune description fournie.",
      },
      marketplace: {
        intro:
          "Parcours les compétences communautaires curatées par le registre de Huxley. La Phase C remplira cet onglet avec des cartes depuis le flux canonique.",
        feed: "En attendant, parcours le registre directement :",
      },
    },
  },
};

export default fr;
