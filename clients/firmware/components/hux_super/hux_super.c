#include "hux_super.h"

#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "hux_app.h"
#include "hux_audio.h"
#include "hux_button.h"
#include "hux_log.h"
#include "hux_net.h"
#include "hux_opus.h"
#include "hux_super_check.h"

static const char *TAG = "hux_super";

#define SUPER_TICK_MS   1000
#define SUPER_PRIO      10   /* above the audio tasks: only a spinning task starves it */
#define SUPER_STACK     3072
#define EARLY_UPTIME_S  600  /* a stall this soon after boot counts toward a loop */
#define DEGRADE_AFTER   3

enum {
    P_APP,
    P_TIMER,
    P_LADDER,
    P_UPLINK,
    P_SPEAKER,
    P_MIC,
    P_DECODER,
    P_BUTTON,
    P_COUNT,
};

/* Limits: the longest legitimate wait of each, with room. The app can sit
 * in a client stop (5 s network timeout) or behind the link lock; the
 * ladder's tick is 5 s and a step can take a 10 s lock wait plus a stop;
 * an uplink send times out at 5 s. */
static hux_super_part_t s_parts[P_COUNT] = {
    [P_APP] = {.name = "app", .limit_ms = 30000},
    [P_TIMER] = {.name = "esp_timer", .limit_ms = 10000},
    [P_LADDER] = {.name = "ladder", .limit_ms = 60000},
    [P_UPLINK] = {.name = "uplink", .limit_ms = 15000},
    [P_SPEAKER] = {.name = "speaker", .limit_ms = 10000},
    [P_MIC] = {.name = "mic", .limit_ms = 10000},
    [P_DECODER] = {.name = "decoder", .limit_ms = 10000},
    [P_BUTTON] = {.name = "button", .limit_ms = 10000},
};

/* Across the reset the supervisor causes: which part, how soon after boot,
 * and how many early stalls in a row. A power cut leaves garbage: magic. */
#define SUPER_MAGIC 0x53555052u /* 'SUPR' */
typedef struct {
    uint32_t magic;
    uint32_t armed;    /* 1: the watchdog is about to fire for `part` */
    uint32_t part;
    uint32_t uptime_s; /* when it stalled */
    uint32_t early;    /* early stalls in a row */
} super_rtc_t;
RTC_NOINIT_ATTR static super_rtc_t s_rtc;

static _Atomic uint32_t s_timer_ticks = 0;
static bool s_degraded = false;
static const char *s_last_stall = "none";

static void timer_tick(void *arg) {
    (void)arg;
    atomic_fetch_add(&s_timer_ticks, 1);
}

static uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void sample(void) {
    s_parts[P_APP].done = hux_app_beats();
    s_parts[P_APP].pending = 1;
    s_parts[P_TIMER].done = atomic_load(&s_timer_ticks);
    s_parts[P_TIMER].pending = 1;
    s_parts[P_LADDER].done = hux_net_ladder_ticks();
    s_parts[P_LADDER].pending = 1;
    hux_app_uplink_progress(&s_parts[P_UPLINK].done, &s_parts[P_UPLINK].pending);
    s_parts[P_SPEAKER].done = hux_audio_spk_writes();
    s_parts[P_SPEAKER].pending = 1;
    s_parts[P_MIC].done = hux_audio_mic_turns();
    s_parts[P_MIC].pending = 1;
    hux_opus_rx_progress(&s_parts[P_DECODER].done, &s_parts[P_DECODER].pending);
    s_parts[P_BUTTON].done = hux_button_tca_reads();
    s_parts[P_BUTTON].pending = 1;
}

/* The boot's verdict on the last reset: was it ours, and early? */
static void boot_history(void) {
    esp_reset_reason_t reason = esp_reset_reason();
    if (s_rtc.magic != SUPER_MAGIC || s_rtc.part >= P_COUNT) {
        memset(&s_rtc, 0, sizeof(s_rtc));
        s_rtc.magic = SUPER_MAGIC;
    }
    bool ours = s_rtc.armed == 1 && (reason == ESP_RST_TASK_WDT || reason == ESP_RST_PANIC);
    if (ours) {
        s_last_stall = s_parts[s_rtc.part].name;
        s_rtc.early = s_rtc.uptime_s < EARLY_UPTIME_S ? s_rtc.early + 1 : 0;
    } else {
        s_rtc.early = 0;
    }
    s_rtc.armed = 0;
    s_degraded = s_rtc.early >= DEGRADE_AFTER;
    /* The app, the timer task and the ladder are the repair path: always
     * enforced. The audio parts only log on a degraded boot. */
    for (int i = 0; i < P_COUNT; i++) {
        bool repair = i == P_APP || i == P_TIMER || i == P_LADDER;
        s_parts[i].enforced = repair || !s_degraded;
    }
    if (ours || s_degraded) {
        ESP_LOGW(TAG, "super.boot reset=%d last_stall=%s at_s=%u early_in_a_row=%u degraded=%d",
                 (int)reason, s_last_stall, (unsigned)s_rtc.uptime_s, (unsigned)s_rtc.early,
                 s_degraded);
    } else {
        ESP_LOGI(TAG, "super.boot reset=%d last_stall=none degraded=0", (int)reason);
    }
}

static void super_task(void *arg) {
    (void)arg;
    esp_err_t err = esp_task_wdt_add(NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "super.wdt_add_failed err=%s: stalls are logged, not reset",
                 esp_err_to_name(err));
    }
    sample();
    hux_super_begin(s_parts, P_COUNT, now_ms());
    bool feeding = true;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(SUPER_TICK_MS));
        sample();
        uint32_t age = 0;
        int i = hux_super_check(s_parts, P_COUNT, now_ms(), &age);
        if (i >= 0) {
            hux_super_part_t *p = &s_parts[i];
            /* Synchronous: the line is in the RTC black box before the
             * watchdog fires. */
            ESP_LOGE(TAG, "super.stalled part=%s age_ms=%u done=%u pending=%u enforced=%d",
                     p->name, (unsigned)age, (unsigned)p->done, (unsigned)p->pending,
                     p->enforced);
            if (p->enforced && feeding) {
                s_rtc.part = (uint32_t)i;
                s_rtc.uptime_s = now_ms() / 1000;
                s_rtc.armed = 1;
                feeding = false; /* the task watchdog panics within its timeout */
            }
        }
        if (feeding && err == ESP_OK) {
            esp_task_wdt_reset();
        }
    }
}

/* From the watchdog's interrupt, just before it panics: which tasks it
 * caught, into the black box (the supervisor itself, or an idle task a
 * spinning task starved). */
static char s_wdt_msg[96];
static size_t s_wdt_len;

static void wdt_msg(void *opaque, const char *msg) {
    (void)opaque;
    size_t n = strlen(msg);
    if (s_wdt_len + n >= sizeof(s_wdt_msg)) {
        n = sizeof(s_wdt_msg) - 1 - s_wdt_len;
    }
    memcpy(s_wdt_msg + s_wdt_len, msg, n);
    s_wdt_len += n;
    s_wdt_msg[s_wdt_len] = '\0';
}

void esp_task_wdt_isr_user_handler(void) {
    int cpus = 0;
    strcpy(s_wdt_msg, "E twdt.fired tasks=");
    s_wdt_len = strlen(s_wdt_msg);
    esp_task_wdt_print_triggered_tasks(wdt_msg, NULL, &cpus);
    hux_log_blackbox_note_isr(s_wdt_msg);
}

void hux_super_start(void) {
    boot_history();
    const esp_timer_create_args_t args = {.callback = timer_tick, .name = "super_tick"};
    esp_timer_handle_t t = NULL;
    if (esp_timer_create(&args, &t) != ESP_OK || esp_timer_start_periodic(t, 1000000) != ESP_OK) {
        ESP_LOGE(TAG, "super.timer_failed: the esp_timer part will read as stalled");
    }
    if (xTaskCreate(super_task, "hux_super", SUPER_STACK, NULL, SUPER_PRIO, NULL) != pdPASS) {
        ESP_LOGE(TAG, "super.task_create_failed: no supervisor this boot");
    }
}

void hux_super_test_hang(const char *part) {
    ESP_LOGW(TAG, "super.test_hang part=%s", part);
    if (strcmp(part, "app") == 0) {
        hux_app_event_t ev = {.kind = HUX_APP_EV_DEBUG_HANG};
        hux_app_post_event(&ev, false);
    } else if (strcmp(part, "spk") == 0) {
        hux_audio_debug_hang_spk();
    }
}

void hux_super_status(void) {
    char buf[200];
    size_t n = 0;
    for (int i = 0; i < P_COUNT && n < sizeof(buf); i++) {
        n += (size_t)snprintf(buf + n, sizeof(buf) - n, "%s=%u/%u ", s_parts[i].name,
                              (unsigned)s_parts[i].done, (unsigned)s_parts[i].pending);
    }
    ESP_LOGI(TAG, "super.status %s last_stall=%s early_in_a_row=%u degraded=%d", buf,
             s_last_stall, (unsigned)s_rtc.early, s_degraded);
}
