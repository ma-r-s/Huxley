/**
 * hux_prompts: sentences and sounds the walkie plays on its own (F-0027,
 * F-0033).
 *
 * When there is no server (no connection, a battery about to cut off)
 * nobody else can speak, so a few sentences are rendered once in the
 * persona's voice (tools/render_prompts.py: TTS, then the server's own
 * Opus encoder, checked by transcribing them back) and embedded in the
 * firmware; an update carries them. They play through the same decoder and
 * speaker ring as the server's audio, so a talk press or a speaker clear
 * stops them like any reply.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The sentences are tools/render_prompts.py's PROMPTS; the sounds its
 * --sounds, from server/personas/_shared/sounds/device/ (F-0033). */
typedef enum {
    HUX_PROMPT_OFFLINE = 0,   /* "No tengo conexion. Lo sigo intentando." */
    HUX_PROMPT_BATTERY_EMPTY, /* "Bateria agotada. Ponme a cargar." */
    HUX_PROMPT_STARTUP,       /* sound: powered on */
    HUX_PROMPT_SEARCHING,     /* sound: a tick while it looks for the server */
    HUX_PROMPT_CONNECTED,     /* sound: back with the server */
    HUX_PROMPT_LOST,          /* sound: the server has been gone 20 s */
    HUX_PROMPT_UPDATING,      /* "Me estoy actualizando. Intenta otra vez en un minuto." */
    HUX_PROMPT_COUNT,
} hux_prompt_t;

/** Queue the prompt behind what the speaker has (call `hux_audio_spk_clear`
 * first to say it now). Returns once it is queued (well under its length:
 * the decoder takes 60 ms packets in ~12 ms). False, logged, if the decoder
 * isn't running or the stored file is bad: the caller falls back to tones. */
bool hux_prompt_play(hux_prompt_t id);

/** Its length in ms (0 for an unknown id or a bad file). */
uint32_t hux_prompt_ms(hux_prompt_t id);

/** "offline", "battery_empty", "startup", ...; NULL for an unknown id. */
const char *hux_prompt_name(hux_prompt_t id);

/** The id for a name (the console's `prompt <name>`), or -1. */
int hux_prompt_find(const char *name);

#ifdef __cplusplus
}
#endif
