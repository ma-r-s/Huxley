// Spanish UI catalog — mirrors `en.ts` one key at a time.
const es = {
  translation: {
    orbStatus: {
      idle: "Mantén pulsado para hablar",
      listening: "Escuchando — suelta para enviar",
      thinking: "Enviado — esperando respuesta",
      speaking: "Respondiendo",
      live: "En vivo — mantén para terminar",
      playing: "Reproduciendo…",
      error: "Reconectando…",
      paused: "En pausa",
      wake: "Conectando",
    },
    orbHint: "Mantén el círculo pulsado. O mantén espacio.",
    call: {
      talkingWith: "Hablando con {{who}}",
    },
    mic: {
      cannotOpen: "No pude abrir el micrófono para la llamada",
      accessDenied: "Micrófono denegado — revisa los permisos del navegador",
    },
    header: {
      sessions: "Sesiones",
      device: "Dispositivo",
      connRetrying: "reconectando…",
    },
    sessions: {
      title: "Conversaciones",
      recent: "Recientes",
      empty: "Todavía nada — mantén pulsada la esfera para empezar.",
      turnsCount_one: "{{count}} turno",
      turnsCount_other: "{{count}} turnos",
      sample: {
        1: "Por el capítulo en el que ibas…",
        2: "Pusimos un temporizador mientras cocinabas pasta",
        3: "Revisamos la agenda de mañana",
      },
      when: {
        todayAt: "Hoy, {{time}}",
        yesterday: "Ayer",
      },
    },
    logs: {
      title: "Registros",
      recent: "Sesión actual",
      empty: "Aún no hay eventos — inicia un turno o envía un client_event.",
      clear: "Limpiar",
      statusTag: "estado",
    },
    device: {
      title: "Dispositivo",
      headline: "Tu Huxley",
      connected: "Conectado",
      offline: "Sin conexión",
      close: "Cerrar",
      sections: {
        appearance: "Apariencia",
        language: "Idioma",
        persona: "Persona",
        maintenance: "Mantenimiento",
      },
      appearance: {
        accent: "Acento",
        typeface: "Tipografía",
        orbPersonality: "Personalidad de la esfera",
        theme: "Tema",
        themeLight: "Claro",
        themeLightDesc: "Coral cálido",
        themeDark: "Oscuro",
        themeDarkDesc: "Nocturno",
        themeAuto: "Automático",
        themeAutoDesc: "Según el sistema",
        expr: {
          subtle: "Sutil",
          natural: "Natural",
          expressive: "Expresiva",
        },
        fontPair: {
          instrument: "Serif cálida",
          fraunces: "Editorial",
          "all-sans": "Limpia y moderna",
          mono: "Terminal",
        },
        accents: {
          coral: "Coral",
          amber: "Ámbar",
          clay: "Arcilla",
          rose: "Rosa",
          plum: "Ciruela",
          moss: "Musgo",
        },
      },
      maintenance: {
        reloadSkills: "Recargar skills",
        restartServer: "Reiniciar servidor",
        viewLogs: "Ver logs",
      },
    },
  },
};

export default es;
