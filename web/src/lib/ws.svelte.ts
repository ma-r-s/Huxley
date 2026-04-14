export type AppState = "IDLE" | "CONNECTING" | "CONVERSING" | "PLAYING";

export interface StatusEntry {
  id: number;
  text: string;
  ts: string;
}

export interface TranscriptEntry {
  id: number;
  role: "user" | "assistant";
  text: string;
}

export interface DevEvent {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  ts: string;
}

type ServerMessage =
  | { type: "audio"; data: string }
  | { type: "audio_clear" }
  | { type: "state"; value: AppState }
  | { type: "status"; message: string }
  | { type: "transcript"; role: "user" | "assistant"; text: string }
  | { type: "model_speaking"; value: boolean }
  | { type: "dev_event"; kind: string; payload: Record<string, unknown> };

let _id = 0;
function nextId() {
  return _id++;
}

export function createWsStore() {
  let socket = $state<WebSocket | null>(null);
  let connected = $state(false);
  let appState = $state<AppState>("IDLE");
  let modelSpeaking = $state(false);
  let statusLog = $state<StatusEntry[]>([]);
  let transcript = $state<TranscriptEntry[]>([]);
  let devEvents = $state<DevEvent[]>([]);

  // Callbacks set by the page after construction.
  let _onAudio: ((data: string) => void) | null = null;
  let _onAudioClear: (() => void) | null = null;

  function nowTs(): string {
    return new Date().toLocaleTimeString("en", { hour12: false });
  }

  function pushStatus(text: string) {
    statusLog = [{ id: nextId(), text, ts: nowTs() }, ...statusLog].slice(
      0,
      30,
    );
  }

  function pushDevEvent(kind: string, payload: Record<string, unknown>) {
    devEvents = [
      { id: nextId(), kind, payload, ts: nowTs() },
      ...devEvents,
    ].slice(0, 50);
  }

  function connect() {
    const url = `ws://${window.location.hostname}:8765`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      connected = true;
      pushStatus("Connected to backend");
    };

    ws.onclose = () => {
      connected = false;
      socket = null;
      pushStatus("Disconnected — retrying in 2s…");
      setTimeout(connect, 2000);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as ServerMessage;
        switch (msg.type) {
          case "audio":
            _onAudio?.(msg.data);
            break;
          case "audio_clear":
            _onAudioClear?.();
            break;
          case "state":
            appState = msg.value;
            break;
          case "status":
            pushStatus(msg.message);
            break;
          case "transcript":
            transcript = [
              ...transcript,
              { id: nextId(), role: msg.role, text: msg.text },
            ];
            break;
          case "model_speaking":
            modelSpeaking = msg.value;
            break;
          case "dev_event":
            pushDevEvent(msg.kind, msg.payload);
            break;
        }
      } catch {
        // ignore malformed messages
      }
    };

    socket = ws;
  }

  function send(msg: object) {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(msg));
    }
  }

  return {
    get connected() {
      return connected;
    },
    get appState() {
      return appState;
    },
    get modelSpeaking() {
      return modelSpeaking;
    },
    get statusLog() {
      return statusLog;
    },
    get transcript() {
      return transcript;
    },
    get devEvents() {
      return devEvents;
    },
    connect,
    pushStatus,
    setOnAudio: (fn: (data: string) => void) => {
      _onAudio = fn;
    },
    setOnAudioClear: (fn: () => void) => {
      _onAudioClear = fn;
    },
    sendAudio: (data: string) => send({ type: "audio", data }),
    wakeWord: () => send({ type: "wake_word" }),
    pttStart: () => send({ type: "ptt_start" }),
    pttStop: () => send({ type: "ptt_stop" }),
  };
}
