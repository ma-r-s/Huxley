#include "hux_park.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_rtc_time.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hux_audio.h"
#include "hux_battery.h"
#include "hux_battery_cutoff.h"
#include "hux_board.h"
#include "hux_net.h"
#include "hux_opus.h"
#include "hux_ota.h"
#include "hux_prompts.h"

static const char *TAG = "hux_park";

#define LEVER_GPIO      GPIO_NUM_7 /* hux_button's TALK_GPIO: to GND when held */
#define PA_EN_PIN       8          /* hux_board's HUX_TCA_AUDIO_PA_EN */
#define WAKE_EVERY_US   (60ULL * 1000 * 1000)
#define ESCAPE_US       (10LL * 1000 * 1000) /* hold the lever this long: boot anyway */
#define SAY_GAP_US      (30LL * 1000 * 1000) /* at most one sentence per 30 s */
#define SAY_MAX_MS      8000
#define DEBOUNCE_MS     50

/* RTC memory: survives the restarts (CPU resets) the park uses, not a power
 * cut (then the boot reading decides again). The magic carries the layout's
 * size, so an image with another layout never reads this one's flag. */
typedef struct {
    uint32_t magic;
    uint32_t armed;          /* park at the next boot */
    int64_t since_us;        /* esp_rtc_get_time_us() when the park began */
    uint32_t wakes;          /* light-sleep wakes since */
    uint32_t lever_wakes;    /* of them, by the lever */
    uint32_t presses;        /* real presses since (a release seen first) */
    int32_t min_mv;          /* the lowest reading since */
    uint32_t seen_release;   /* the lever was seen up during this park */
    uint32_t pressing;       /* a real press is under way */
    int64_t press_since_us;  /* when it began */
    int64_t said_at_us;      /* the sentence was last said (0: never) */
    int32_t test_mv;         /* bench: read this instead of the cell (0: off) */
    uint32_t test_wakes;     /* bench: boot by itself after this many wakes */
    char why[12];
} park_rtc_t;

#define PARK_MAGIC (0x50524b00u ^ (uint32_t)sizeof(park_rtc_t)) /* "PRK" + layout */

RTC_NOINIT_ATTR static park_rtc_t s_park;

static void arm(int mv, const char *why) {
    memset(&s_park, 0, sizeof(s_park));
    s_park.magic = PARK_MAGIC;
    s_park.armed = 1;
    s_park.since_us = (int64_t)esp_rtc_get_time_us();
    s_park.min_mv = mv;
    snprintf(s_park.why, sizeof(s_park.why), "%s", why);
}

static int64_t rtc_us(void) {
    return (int64_t)esp_rtc_get_time_us();
}

/* Held, read twice DEBOUNCE_MS apart: a wire touching GND on and off is
 * not a press. */
static bool lever_held(void) {
    if (gpio_get_level(LEVER_GPIO) != 0) {
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(DEBOUNCE_MS));
    return gpio_get_level(LEVER_GPIO) == 0;
}

static int read_mv(void) {
    return s_park.test_mv != 0 ? s_park.test_mv : hux_battery_read_cell_mv();
}

static bool cal_ok(void) {
    return s_park.test_mv != 0 || hux_battery_cal_ok();
}

static bool update_pending(void) {
    return strcmp(hux_ota_running_state(), "pending") == 0;
}

/* Release the expander's INT# (wired to GPIO0, the boot strap) before any
 * restart or sleep: left asserted, a reset can boot download mode (F-0020). */
static void release_strap(void) {
    uint16_t in = 0;
    (void)hux_board_tca_read_inputs(&in);
}

static void restart(void) {
    (void)hux_board_tca_write(PA_EN_PIN, false); /* no amp through the boot */
    release_strap();
    esp_restart();
}

static void unhold_noop(void) {}

/* Say the sentence and wait for it to play out (or SAY_MAX_MS). Audio must
 * be up (the running firmware) or `bring_up` starts it (the park). */
static void say_battery_empty(bool bring_up) {
    if (bring_up) {
        hux_audio_init();
        hux_opus_init(hux_audio_spk_push, hux_audio_on_audio_clear, unhold_noop);
    } else {
        /* Nothing more from the server: a book still streaming would
         * interleave with the sentence in the decoder's queue. */
        hux_net_pause();
        hux_opus_rx_reset();
    }
    hux_audio_spk_clear();
    hux_audio_spk_unhold("battery_empty");
    if (!hux_prompt_play(HUX_PROMPT_BATTERY_EMPTY)) {
        hux_audio_tone(440, 120); /* the stored sentence can't play: still a sound */
        hux_audio_tone(0, 60);
        hux_audio_tone(330, 220);
    }
    vTaskDelay(pdMS_TO_TICKS(300)); /* the decoder fills the ring */
    for (int ms = 0; ms < SAY_MAX_MS; ms += 100) {
        if (hux_audio_spk_ring_used() == 0 && hux_opus_rx_waiting() == 0 &&
            hux_audio_quiet_ms() >= 200) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void hux_park_cutoff(int cell_mv, const char *why) {
    ESP_LOGW(TAG, "battery.cutoff mv=%d why=%s: saying it, then parked until %d mV", cell_mv, why,
             HUX_CUTOFF_RESUME_MV);
    /* Armed first: a crash while saying it must still park. */
    arm(cell_mv, why);
    int mv = 0, pct = 0;
    (void)hux_battery_last(&mv, &pct);
    char json[160];
    int n = snprintf(json, sizeof(json),
                     "{\"type\":\"client_event\",\"event\":\"system.battery\",\"data\":{"
                     "\"mv\":%d,\"pct\":%d,\"cutoff\":true}}",
                     cell_mv, pct);
    if (n > 0 && (size_t)n < sizeof(json)) {
        (void)hux_net_send_text(json, (size_t)n); /* no waiting: offline is fine */
    }
    vTaskDelay(pdMS_TO_TICKS(300)); /* the event out before the link pauses */
    s_park.said_at_us = rtc_us();
    say_battery_empty(false);
    vTaskDelay(pdMS_TO_TICKS(200));
    restart();
}

void hux_park_test(int mv, int minutes) {
    if (mv <= 0 || mv >= HUX_CUTOFF_RESUME_MV || minutes < 1 || minutes > 60 || update_pending()) {
        ESP_LOGW(TAG,
                 "battery.park_test refused mv=%d minutes=%d pending=%d (mv under %d, 1-60 min, "
                 "not during an update)",
                 mv, minutes, update_pending(), HUX_CUTOFF_RESUME_MV);
        return;
    }
    ESP_LOGW(TAG, "battery.park_test mv=%d minutes=%d: restarting into the park", mv, minutes);
    arm(mv, "test");
    s_park.test_mv = mv;
    s_park.test_wakes = (uint32_t)minutes;
    vTaskDelay(pdMS_TO_TICKS(500));
    restart();
}

static void lever_input(void) {
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << LEVER_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&io);
}

static void resume(int mv, const char *why) {
    int64_t parked_s = (rtc_us() - s_park.since_us) / 1000000;
    ESP_LOGW(TAG,
             "battery.resume why=%s mv=%d parked_s=%d wakes=%u lever_wakes=%u presses=%u "
             "min_mv=%d park_why=%s test=%d",
             why, mv, (int)parked_s, (unsigned)s_park.wakes, (unsigned)s_park.lever_wakes,
             (unsigned)s_park.presses, (int)s_park.min_mv, s_park.why, s_park.test_mv != 0);
    s_park.armed = 0;
    s_park.test_mv = 0;
}

static const char *resume_why(int mv, bool cal) {
    if (mv >= HUX_CUTOFF_RESUME_MV) {
        return "charged";
    }
    return cal ? "unreadable" : "calibration";
}

void hux_park_at_boot(void) {
    if (s_park.magic != PARK_MAGIC) {
        memset(&s_park, 0, sizeof(s_park)); /* a power cut, or another layout */
        s_park.magic = PARK_MAGIC;
    }
    int mv = read_mv();
    bool armed = s_park.armed != 0;
    if (!hux_battery_cutoff_on() || update_pending()) {
        if (armed || hux_cutoff_park_at_boot(false, mv, cal_ok())) {
            /* Off by the remote switch, or an update not yet kept: a park's
             * restarts would roll it back (and the update was refused under
             * 40 %, so the cell can't be spent). */
            ESP_LOGW(TAG, "battery.park_skipped mv=%d armed=%d why=%s", mv, armed,
                     update_pending() ? "update_pending" : "switched_off");
        }
        s_park.armed = 0;
        return;
    }
    if (!hux_cutoff_park_at_boot(armed, mv, cal_ok())) {
        return;
    }
    if (!armed) {
        arm(mv, "boot"); /* a resting boot under 3.45 V (the rocker after a cutoff) */
    }
    lever_input();
    (void)hux_board_tca_write(PA_EN_PIN, false);
    (void)hux_audio_codecs_power_down();
    ESP_LOGW(TAG, "battery.parked mv=%d why=%s wakes=%u presses=%u test=%d", mv, s_park.why,
             (unsigned)s_park.wakes, (unsigned)s_park.presses, s_park.test_mv != 0);
    bool sleep_failed_said = false;
    for (;;) {
        release_strap();
        mv = read_mv();
        bool cal = cal_ok();
        if (mv > 0 && mv < s_park.min_mv) {
            s_park.min_mv = mv;
        }
        if (hux_cutoff_resume(mv, cal)) {
            resume(mv, resume_why(mv, cal));
            return;
        }
        if (s_park.test_mv != 0 && s_park.wakes >= s_park.test_wakes) {
            resume(mv, "test_over");
            return;
        }
        bool held = lever_held();
        if (held && s_park.seen_release) {
            /* A real press: it began after the lever was seen up here. */
            int64_t now = rtc_us();
            if (!s_park.pressing) {
                s_park.pressing = 1;
                s_park.press_since_us = now;
                s_park.presses++;
            }
            if (s_park.said_at_us == 0 || now - s_park.said_at_us >= SAY_GAP_US) {
                s_park.said_at_us = now;
                ESP_LOGW(TAG, "battery.park_press mv=%d presses=%u: saying it", mv,
                         (unsigned)s_park.presses);
                say_battery_empty(true);
                restart(); /* audio up: start clean, the park closes the codecs again */
            }
            if (now - s_park.press_since_us >= ESCAPE_US) {
                ESP_LOGW(TAG, "battery.park_escape held_s=%d mv=%d: booting anyway",
                         (int)((now - s_park.press_since_us) / 1000000), mv);
                resume(mv, "escape");
                return;
            }
            vTaskDelay(pdMS_TO_TICKS(100)); /* awake while held: at most 10 s */
            continue;
        }
        if (!held) {
            s_park.seen_release = 1;
            s_park.pressing = 0;
        }
        /* A lever held since before this park (a stuck lever or a shorted
         * wire): timer-only wakes, no sentence and no escape for it. */
        esp_sleep_enable_timer_wakeup(WAKE_EVERY_US);
        if (!held) {
            gpio_wakeup_enable(LEVER_GPIO, GPIO_INTR_LOW_LEVEL);
            esp_sleep_enable_gpio_wakeup();
        }
        esp_err_t err = esp_light_sleep_start();
        if (!held) {
            gpio_wakeup_disable(LEVER_GPIO);
            esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_GPIO);
        }
        if (err != ESP_OK) {
            if (!sleep_failed_said) {
                ESP_LOGW(TAG, "battery.park_sleep_failed err=%s: waiting awake instead",
                         esp_err_to_name(err));
                sleep_failed_said = true;
            }
            vTaskDelay(pdMS_TO_TICKS(WAKE_EVERY_US / 1000));
        }
        s_park.wakes++;
        if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_GPIO) {
            s_park.lever_wakes++;
        }
    }
}
