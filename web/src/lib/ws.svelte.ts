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

// 400ms is the threshold for a blind user to start hearing dead air as
// "the device is broken". Anything past that, fill with the thinking tone.
const SILENCE_TIMEOUT_MS = 400;

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

  // Silence-detection timer for the thinking-tone gap-filler. Started on
  // ptt_stop send and on `model_speaking: false` receive (inter-round gap).
  // Cancelled by audio arriving, model_speaking:true, audio_clear, ptt_start,
  // wake_word, or socket close. Grandpa is blind — silence ≥ 400 ms is
  // indistinguishable from a broken device.
  let silenceTimer: ReturnType<typeof setTimeout> | null = null;
  let thinkingToneActive = false;

  function startSilenceTimer() {
    if (silenceTimer !== null) clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => {
      silenceTimer = null;
      thinkingToneActive = true;
      _onThinkingToneStart?.();
    }, SILENCE_TIMEOUT_MS);
  }

  function cancelSilenceTimer() {
    if (silenceTimer !== null) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    if (thinkingToneActive) {
      thinkingToneActive = false;
      _onThinkingToneStop?.();
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
      cancelSilenceTimer();
      pushStatus("Disconnected — retrying in 2s…");
      setTimeout(connect, 2000);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as ServerMessage;
        switch (msg.type) {
          case "audio":
            // Real audio arrived — silence is over.
            cancelSilenceTimer();
            _onAudio?.(msg.data);
            break;
          case "audio_clear":
            cancelSilenceTimer();
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
              cancelSilenceTimer();
            }
            // We intentionally do NOT start the timer on model_speaking:false.
            // The terminal audio-done after a completed turn is
            // indistinguishable from an inter-round gap, and starting the
            // timer there causes the tone to play forever after the turn
            // ends. The ptt_stop trigger covers the main silence gap.
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
    setOnThinkingTone: (start: () => void, stop: () => void) => {
      _onThinkingToneStart = start;
      _onThinkingToneStop = stop;
    },
    sendAudio: (data: string) => send({ type: "audio", data }),
    wakeWord: () => {
      cancelSilenceTimer();
      send({ type: "wake_word" });
    },
    pttStart: () => {
      cancelSilenceTimer();
      send({ type: "ptt_start" });
    },
    pttStop: () => {
      send({ type: "ptt_stop" });
      // Initial gap: commit → OpenAI → first audio delta. Could be > 400 ms.
      startSilenceTimer();
    },
  };
}
