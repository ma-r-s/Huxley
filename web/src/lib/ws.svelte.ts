export type AppState = "IDLE" | "CONNECTING" | "CONVERSING";

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

const EXPECTED_PROTOCOL = 1;

type ServerMessage =
  | { type: "hello"; protocol: number }
  | { type: "audio"; data: string }
  | { type: "audio_clear" }
  | { type: "state"; value: AppState }
  | { type: "status"; message: string }
  | { type: "transcript"; role: "user" | "assistant"; text: string }
  | { type: "model_speaking"; value: boolean }
  | { type: "set_volume"; level: number }
  | { type: "dev_event"; kind: string; payload: Record<string, unknown> };

let _id = 0;
function nextId() {
  return _id++;
}

// 1500ms threshold before filling silence with the thinking-tone drone.
// 400ms (the original) over-triggered: normal model first-token latency
// (400-800ms typical, 2-3s worst case) was firing the tone constantly,
// teaching grandpa to treat it as background noise. 1500ms means the tone
// fires only when something's actually wrong, so its presence still
// communicates "still working" instead of "always on."
const SILENCE_TIMEOUT_MS = 1500;

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
  let _onThinkingToneStart: (() => void) | null = null;
  let _onThinkingToneStop: (() => void) | null = null;
  let _onSetVolume: ((level: number) => void) | null = null;

  // Silence-detection timer for the thinking-tone gap-filler. Started on
  // ptt_stop send and on `model_speaking: false` receive (inter-round gap).
  // Cancelled by audio arriving, model_speaking:true, audio_clear, ptt_start,
  // wake_word, or socket close. Grandpa is blind — silence ≥ 400 ms is
  // indistinguishable from a broken device.
  let silenceTimer: ReturnType<typeof setTimeout> | null = null;
  let thinkingToneActive = false;

  function startSilenceTimer(trigger: string) {
    if (silenceTimer !== null) clearTimeout(silenceTimer);
    sendClientEvent("silence_timer_started", { trigger });
    silenceTimer = setTimeout(() => {
      silenceTimer = null;
      thinkingToneActive = true;
      sendClientEvent("thinking_tone_on", { reason: "silence_timeout" });
      _onThinkingToneStart?.();
    }, SILENCE_TIMEOUT_MS);
  }

  function cancelSilenceTimer(reason: string = "unknown") {
    const hadTimer = silenceTimer !== null;
    if (silenceTimer !== null) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    const hadTone = thinkingToneActive;
    if (thinkingToneActive) {
      thinkingToneActive = false;
      _onThinkingToneStop?.();
    }
    if (hadTimer || hadTone) {
      sendClientEvent("silence_timer_cancelled", {
        reason,
        had_timer: hadTimer,
        had_tone: hadTone,
      });
    }
  }

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
      cancelSilenceTimer("socket_close");
      pushStatus("Disconnected — retrying in 2s…");
      setTimeout(connect, 2000);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as ServerMessage;
        switch (msg.type) {
          case "hello":
            if (msg.protocol !== EXPECTED_PROTOCOL) {
              pushStatus(
                `Protocol mismatch: server=${msg.protocol} client=${EXPECTED_PROTOCOL} — reload required`,
              );
              ws.close(1002, "Protocol version mismatch");
            }
            break;
          case "audio":
            // Real audio arrived — silence is over.
            cancelSilenceTimer("audio_arrived");
            _onAudio?.(msg.data);
            break;
          case "audio_clear":
            cancelSilenceTimer("audio_clear");
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
            if (msg.value) {
              // Model is about to emit audio — kill any pending tone.
              cancelSilenceTimer("model_speaking_true");
            }
            // We intentionally do NOT start the timer on model_speaking:false.
            // The terminal audio-done after a completed turn is
            // indistinguishable from an inter-round gap, and starting the
            // timer there causes the tone to play forever after the turn
            // ends. The ptt_stop trigger covers the main silence gap.
            break;
          case "set_volume":
            _onSetVolume?.(msg.level);
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

  function sendClientEvent(event: string, data: Record<string, unknown> = {}) {
    // Pure observability — server logs as `client.<event>`. See protocol.md.
    send({ type: "client_event", event, data });
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
    setOnThinkingTone: (start: () => void, stop: () => void) => {
      _onThinkingToneStart = start;
      _onThinkingToneStop = stop;
    },
    setOnSetVolume: (fn: (level: number) => void) => {
      _onSetVolume = fn;
    },
    sendAudio: (data: string) => send({ type: "audio", data }),
    sendClientEvent,
    wakeWord: () => {
      cancelSilenceTimer("wake_word");
      send({ type: "wake_word" });
    },
    pttStart: () => {
      cancelSilenceTimer("ptt_start");
      send({ type: "ptt_start" });
    },
    pttStop: () => {
      send({ type: "ptt_stop" });
      // Initial gap: commit → OpenAI → first audio delta. Could be > 400 ms.
      startSilenceTimer("ptt_stop");
    },
    reset: () => {
      cancelSilenceTimer("reset");
      transcript = [];
      devEvents = [];
      statusLog = [];
      send({ type: "reset" });
    },
  };
}
