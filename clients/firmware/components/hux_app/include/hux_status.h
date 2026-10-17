/**
 * hux_status: when the walkie says its connection state with sounds
 * (F-0033). Pure, host-tested (tests/test_hux_status.c); hux_app plays them.
 *
 * Power on: startup, then searching. Searching (no server): a tick every
 * ~3 s for 2 min, then every 30 s, stopping after 17 min (battery; no
 * beeping all night). Lost: only after 20 s without the server (deploys and
 * restarts take 3-20 s), once, then the searching schedule from its start.
 * Connected: on the first connection after power on, and after a lost;
 * never for a blip. An outage ends only with a connection that lasts 2 min:
 * a server that comes up for seconds and falls again (a crash loop) goes on
 * quietly on the first schedule, not with a lost and a connected each time.
 * Night: lost, the ticks and connected are held silent (power on and a
 * press still sound). Busy (playback, a press): nothing plays over it; a
 * due sound waits, except a "connected" still blocked 3 s after the
 * connection: the server talking (a book resuming) or a press already says
 * it, so it is let go rather than played minutes late.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HUX_STATUS_LOST_AFTER_MS   20000u
#define HUX_STATUS_FAST_MS         3000u
#define HUX_STATUS_SLOW_MS         30000u
#define HUX_STATUS_FAST_FOR_MS     (2u * 60u * 1000u)
#define HUX_STATUS_SEARCH_FOR_MS   (17u * 60u * 1000u)
#define HUX_STATUS_OWED_FOR_MS     3000u
#define HUX_STATUS_STABLE_MS       (2u * 60u * 1000u)

typedef enum {
    HUX_STATUS_NONE = 0,
    HUX_STATUS_STARTUP,
    HUX_STATUS_SEARCHING,
    HUX_STATUS_CONNECTED,
    HUX_STATUS_LOST,
} hux_status_sound_t;

typedef enum {
    HUX_PHASE_BOOT = 0,
    HUX_PHASE_SEARCHING, /* no server: the tick schedule runs */
    HUX_PHASE_CONNECTED,
    HUX_PHASE_DROPPED,   /* the server went away under 20 s ago: maybe a blip */
} hux_status_phase_t;

typedef struct {
    hux_status_phase_t phase;
    bool announce;          /* a "connected" is owed (power on, or after a lost) */
    bool owed;              /* connected, the "connected" waiting for a gap since owed_at */
    uint32_t owed_at;
    uint32_t search_since;  /* the tick schedule's start */
    uint32_t last_tick;
    uint32_t dropped_at;
    uint32_t connected_at;  /* this connection's start (a blip doesn't restart it) */
    bool in_outage;         /* a lost went out; only a stable connection ends it */
    bool ticks_off;         /* after a software restart: no ticks until a new drop */
    /* For the caller's logs: */
    uint32_t ticks;         /* ticks due in this search, played or held */
    bool schedule_over;     /* this search reached SEARCH_FOR_MS (set once) */
    bool held_busy;         /* the last *held was a let-go, not the night */
} hux_status_t;

/** `power_on`: a real power on (startup plays, then the ticks). Any other
 * boot (an update's, a crash's, the ladder's hourly restart during a long
 * outage) resumes silently: no startup, no ticks, until a new drop; a
 * "connected" still owed from before it (`connected_owed`, kept by the
 * caller across the restart) plays when the server is back. */
void hux_status_init(hux_status_t *s, bool power_on, bool connected_owed);

/** One tick. Returns the sound to play now; a sound due but not played
 * (the night, or a "connected" let go while busy: `held_busy` says which)
 * returns NONE with *held set to it. */
hux_status_sound_t hux_status_step(hux_status_t *s, uint32_t now_ms, bool connected, bool busy,
                                   bool night, hux_status_sound_t *held);

/* -- A press with no answer coming because of an update (F-0037) ------- */

#define HUX_STATUS_RESTART_SAY_MS (90u * 1000u)     /* after a server restart's close */
#define HUX_STATUS_PENDING_SAY_MS (3u * 60u * 1000u) /* after booting a new image */

typedef enum {
    HUX_UPDATING_NO = 0,
    HUX_UPDATING_OTA,     /* an update downloading or verifying: a restart is next */
    HUX_UPDATING_PENDING, /* a new image booted, not yet accepted */
    HUX_UPDATING_SERVER,  /* the server closed for a restart, not back yet */
} hux_status_updating_t;

/** Why a press now should say "Me estoy actualizando" rather than "No tengo
 * conexion" (or reach the server): the update's own download, a new image
 * still on probation (for 3 min after boot at most: past that it is no
 * longer "a minute"), or the server's restart close in the last 90 s
 * (`restart_close_age_ms` UINT32_MAX: none). */
hux_status_updating_t hux_status_updating(bool ota_busy, bool image_pending, uint32_t uptime_ms,
                                          uint32_t restart_close_age_ms);
const char *hux_status_updating_name(hux_status_updating_t why);

/* -- Which "offline" a press hears (F-0039) ----------------------------- */

typedef enum {
    HUX_OFFLINE_SERVER = 0,  /* internet works, our server doesn't: "No tengo conexion" */
    HUX_OFFLINE_NO_WIFI,     /* no IP: his phone's hotspot off or out of reach */
    HUX_OFFLINE_NO_INTERNET, /* an IP, but nothing beyond it: the phone has no data */
} hux_status_offline_t;

/** `internet_failed`: the last attempt broke below HTTP (hux_link_fail_kind). */
hux_status_offline_t hux_status_offline_kind(bool have_ip, bool internet_failed);
const char *hux_status_offline_name(hux_status_offline_t kind);

/* -- Lever chatter (T1.53 item 27) ------------------------------------
 * A release commits the request only after HUX_LEVER_MERGE_MS; a press
 * inside that window is the lever bouncing (after letting go, or while
 * still held) and continues the same press. Seen live: a re-press 71 ms
 * after a release interrupted his own committed request (5 of 808 gaps
 * under 100 ms). The cost: each request commits 100 ms later. */

#define HUX_LEVER_MERGE_MS 100u

typedef struct {
    bool down;            /* the press as the app treats it (merged) */
    bool release_pending; /* let go, not yet committed */
    uint32_t release_at;
} hux_lever_t;

typedef enum {
    HUX_LEVER_NONE = 0,
    HUX_LEVER_START,   /* a new press: start the turn */
    HUX_LEVER_REBOUND, /* a press inside the window: the same press goes on */
    HUX_LEVER_STOP,    /* the release committed: end the turn */
    HUX_LEVER_RESTART, /* a press after a release not yet committed (the app
                        * was held up past the window) or never seen (a
                        * dropped edge): end the turn, then start a new one */
} hux_lever_action_t;

/** `now_ms`: when the edge happened (hux_button's stamp), not when it is
 * handled: a held-up app task must not merge two real presses. */
hux_lever_action_t hux_lever_press(hux_lever_t *l, uint32_t now_ms);
void hux_lever_release(hux_lever_t *l, uint32_t now_ms);
/** Each loop pass: STOP once a pending release is HUX_LEVER_MERGE_MS old. */
hux_lever_action_t hux_lever_tick(hux_lever_t *l, uint32_t now_ms);

const char *hux_status_name(hux_status_sound_t sound);
const char *hux_status_phase_name(hux_status_phase_t phase);

#ifdef __cplusplus
}
#endif
