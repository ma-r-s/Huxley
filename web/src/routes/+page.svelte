<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { createWsStore, type AppState } from '$lib/ws.svelte.js'
  import { MicCapture } from '$lib/audio/capture.js'
  import { AudioPlayback } from '$lib/audio/playback.js'
  import { cn } from '$lib/utils.js'

  const ws = createWsStore()
  const mic = new MicCapture()
  const playback = new AudioPlayback()

  // Persona list. Parsed from VITE_HUXLEY_PERSONAS=name1:url1,name2:url2.
  // Falls back to a single AbuelOS entry pointing at the conventional
  // dev port. Each entry is its own server process — start them with
  // `HUXLEY_PERSONA=<name> HUXLEY_SERVER_PORT=<port> uv run huxley`.
  type PersonaEntry = { name: string; url: string }
  function parsePersonas(): PersonaEntry[] {
    const raw = (import.meta.env.VITE_HUXLEY_PERSONAS as string | undefined) ?? ''
    const fallback: PersonaEntry[] = [
      { name: 'abuelos', url: `ws://${typeof window === 'undefined' ? 'localhost' : window.location.hostname}:8765` },
    ]
    if (!raw.trim()) return fallback
    const entries = raw.split(',').map(s => s.trim()).filter(Boolean).map(pair => {
      const idx = pair.indexOf(':')
      if (idx === -1) return null
      const name = pair.slice(0, idx).trim()
      const url = pair.slice(idx + 1).trim()
      return name && url ? { name, url } : null
    }).filter((e): e is PersonaEntry => e !== null)
    return entries.length > 0 ? entries : fallback
  }
  const personas = parsePersonas()
  let selectedPersona = $state(personas[0]?.name ?? 'abuelos')

  function handlePersonaChange(e: Event) {
    const target = e.currentTarget as HTMLSelectElement
    const next = personas.find(p => p.name === target.value)
    if (next) {
      selectedPersona = next.name
      ws.switchPersona(next.url)
    }
  }

  // ONE button. Press-and-hold = talk. No other buttons exist. This matches
  // the production hardware (walky-talky with a single big button). Every
  // interaction — start a session, push-to-talk, interrupt the assistant
  // mid-sentence, interrupt a book mid-stream — is the same physical gesture.
  //
  // Pending start: if the button is pressed from IDLE, we first send wake_word
  // and wait for the state to reach CONVERSING, then activate the mic and play
  // a ready tone. Once in CONVERSING, the press is immediate — book interrupts
  // and mid-speech interrupts both go through the turn coordinator.
  let pttHeld = $state(false)
  let pttPendingStart = $state(false)
  let micError = $state<string | null>(null)

  // Activate PTT as soon as CONVERSING is reached (after a pending start).
  $effect(() => {
    if (pttPendingStart && ws.appState === 'CONVERSING') {
      activatePtt()
    }
  })

  // Track previous state to detect "session dropped" transitions: any
  // CONNECTING/CONVERSING → IDLE that wasn't user-initiated. Plays the
  // descending two-tone error chime so a blind user can tell the device
  // hit a problem rather than just "stopped responding."
  let prevState: typeof ws.appState = ws.appState
  $effect(() => {
    const cur = ws.appState
    if ((prevState === 'CONNECTING' || prevState === 'CONVERSING') && cur === 'IDLE') {
      playback.playErrorTone()
    }
    prevState = cur
  })

  onMount(() => {
    ws.setOnAudio((data) => playback.play(data))
    ws.setOnAudioClear(() => playback.stop())
    ws.setOnThinkingTone(
      () => playback.playThinkingTone(),
      () => playback.stopThinkingTone(),
    )
    ws.setOnSetVolume((level) => playback.setVolume(level))
    mic.onFrame = (data) => ws.sendAudio(data)
    const initial = personas.find(p => p.name === selectedPersona) ?? personas[0]
    ws.connect(initial?.url)
  })

  onDestroy(() => {
    mic.destroy()
    playback.destroy()
  })

  function activatePtt() {
    pttPendingStart = false
    mic.active = true
    pttHeld = true
    playback.playTone()      // audible "dígame" cue — the user is blind
    ws.pttStart()
  }

  async function buttonDown(e: PointerEvent) {
    if (!ws.connected) return
    e.preventDefault()
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)

    micError = null

    try {
      // Init audio on first gesture (Safari/iOS autoplay policy).
      await mic.init()
      await mic.resume()
      await playback.resume()
    } catch {
      micError = 'Mic access denied — check browser permissions'
      return
    }

    // Cut any queued audio immediately on press — the user is taking over.
    playback.stop()

    switch (ws.appState) {
      case 'CONVERSING':
        // Session is already up. Go live immediately — the turn coordinator
        // handles interrupts (mid-speech model audio OR a streaming book) on
        // the server side, no special-casing needed here.
        activatePtt()
        break
      case 'IDLE':
        // Start a session. wake_word transitions IDLE → CONNECTING; the
        // pending-start effect activates PTT once CONVERSING is reached.
        pttPendingStart = true
        pttHeld = true           // visual "pressed" feedback while connecting
        ws.wakeWord()
        break
      case 'CONNECTING':
        // Already transitioning; wait for CONVERSING.
        pttPendingStart = true
        pttHeld = true
        break
    }
  }

  function buttonUp(e: PointerEvent) {
    if (!pttHeld && !pttPendingStart) return
    e.preventDefault()

    if (pttPendingStart && !mic.active) {
      // Released before the session came up — silent cancel. No commit,
      // no response. The connection may still complete; the server just
      // sits in CONVERSING until the next press.
      pttPendingStart = false
      pttHeld = false
      return
    }

    mic.active = false
    pttHeld = false
    ws.pttStop()
  }

  const stateMeta: Record<AppState, { label: string; color: string }> = {
    IDLE:       { label: 'Inactivo',     color: 'bg-zinc-600 text-zinc-200' },
    CONNECTING: { label: 'Conectando…',  color: 'bg-yellow-500 text-yellow-950' },
    CONVERSING: { label: 'Conversando',  color: 'bg-green-500 text-green-950' },
  }
  const meta = $derived(stateMeta[ws.appState])

  const buttonLabel = $derived.by(() => {
    if (!ws.connected) return 'Sin conexión'
    if (pttHeld && mic.active) return 'Escuchando…'
    if (pttPendingStart) return 'Conectando…'
    return 'Mantén para hablar'
  })
</script>

<svelte:head><title>AbuelOS</title></svelte:head>

<div class="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">

  <!-- Header -->
  <header class="flex items-center justify-between px-6 py-4 border-b border-zinc-800">
    <div class="flex items-center gap-3">
      <span class="text-lg font-semibold tracking-tight">Huxley</span>
      {#if personas.length > 1}
        <select
          value={selectedPersona}
          onchange={handlePersonaChange}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200"
        >
          {#each personas as p (p.name)}
            <option value={p.name}>{p.name}</option>
          {/each}
        </select>
      {:else}
        <span class="text-xs text-zinc-500">{selectedPersona}</span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      <div class={cn(
        'w-2 h-2 rounded-full transition-colors',
        ws.connected ? 'bg-green-500' : 'bg-red-500 animate-pulse'
      )}></div>
      <span class="text-xs text-zinc-400">
        {ws.connected ? 'conectado' : 'desconectado'}
      </span>
    </div>
  </header>

  <main class="flex-1 flex flex-col items-center gap-8 p-8 max-w-xl mx-auto w-full">

    <!-- State badge (informational only — the end user never sees this) -->
    <div class={cn('px-5 py-2 rounded-full text-sm font-semibold transition-colors', meta.color)}>
      {meta.label}
    </div>

    <!-- THE button. One. Only. -->
    <button
      onpointerdown={buttonDown}
      onpointerup={buttonUp}
      onpointercancel={buttonUp}
      disabled={!ws.connected}
      aria-label="Mantén presionado para hablar"
      class={cn(
        'w-56 h-56 rounded-full font-bold text-xl transition-all duration-100 select-none touch-none',
        'disabled:opacity-25 disabled:cursor-not-allowed',
        pttHeld && mic.active
          ? 'bg-red-500 scale-110 shadow-2xl shadow-red-500/40 ring-4 ring-red-400'
          : pttPendingStart
            ? 'bg-yellow-600 scale-105 ring-2 ring-yellow-400 animate-pulse'
            : 'bg-red-800 hover:bg-red-700 active:scale-105',
      )}
    >
      {#if pttHeld && mic.active}
        🎙<br>Escuchando…
      {:else if pttPendingStart}
        ⏳<br>Conectando…
      {:else}
        Mantén<br>para hablar
      {/if}
    </button>

    {#if micError}
      <p class="text-sm text-red-400">{micError}</p>
    {/if}

    <!-- Dev: reset button — wipes conversation history, reconnects fresh -->
    <button
      onclick={() => ws.reset()}
      disabled={!ws.connected}
      class="text-xs text-zinc-500 hover:text-zinc-300 disabled:opacity-25 disabled:cursor-not-allowed transition-colors px-3 py-1 rounded border border-zinc-800 hover:border-zinc-600"
    >
      Nueva sesión
    </button>

    <!-- Status log -->
    {#if ws.statusLog.length}
      <section class="w-full">
        <h2 class="text-xs font-medium text-zinc-500 uppercase tracking-widest mb-2">Estado</h2>
        <div class="bg-zinc-900 border border-zinc-800 rounded-xl p-3 space-y-1.5 max-h-40 overflow-y-auto">
          {#each ws.statusLog.slice(0, 8) as entry (entry.id)}
            <div class="text-sm text-zinc-300 flex gap-2">
              <span class="text-zinc-600 font-mono text-xs shrink-0">{entry.ts}</span>
              <span>{entry.text}</span>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <!-- Transcript -->
    {#if ws.transcript.length}
      <section class="w-full">
        <h2 class="text-xs font-medium text-zinc-500 uppercase tracking-widest mb-2">Conversación</h2>
        <div class="bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-3 max-h-72 overflow-y-auto">
          {#each ws.transcript as entry (entry.id)}
            <div class={cn('flex', entry.role === 'user' ? 'justify-end' : 'justify-start')}>
              <div class={cn(
                'rounded-2xl px-4 py-2 text-sm max-w-xs leading-relaxed',
                entry.role === 'user'
                  ? 'bg-zinc-700 text-zinc-100 rounded-br-sm'
                  : 'bg-zinc-800 text-zinc-200 rounded-bl-sm',
              )}>
                {entry.text}
              </div>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <!-- Dev events -->
    {#if ws.devEvents.length}
      <section class="w-full">
        <h2 class="text-xs font-medium text-zinc-500 uppercase tracking-widest mb-2">Dev events</h2>
        <div class="bg-zinc-900 border border-zinc-800 rounded-xl p-3 space-y-2 max-h-64 overflow-y-auto">
          {#each ws.devEvents as ev (ev.id)}
            <details class="text-xs group">
              <summary class="cursor-pointer flex gap-2 items-center flex-wrap select-none">
                <span class="text-zinc-600 font-mono shrink-0">{ev.ts}</span>
                <span class="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 font-medium">{ev.kind}</span>
                {#if ev.kind === 'tool_call' && typeof ev.payload.name === 'string'}
                  <span class="font-mono text-zinc-200">{ev.payload.name}</span>
                {/if}
                {#if ev.kind === 'tool_call' && ev.payload.has_audio_stream === true}
                  <span class="px-1.5 py-0.5 rounded bg-blue-900/50 text-blue-300 text-[10px]">audio</span>
                {/if}
              </summary>
              <pre class="mt-2 p-2 bg-zinc-950 rounded text-[11px] text-zinc-400 overflow-x-auto whitespace-pre-wrap break-words">{JSON.stringify(ev.payload, null, 2)}</pre>
            </details>
          {/each}
        </div>
      </section>
    {/if}

  </main>
</div>
