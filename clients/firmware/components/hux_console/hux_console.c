#include "hux_console.h"

#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/usb_serial_jtag_ll.h"

#include "hux_alert.h"
#include "hux_app.h"
#include "hux_led.h"
#include "hux_opus.h"
#include "hux_audio.h"
#include "hux_battery.h"
#include "hux_button.h"
#include "hux_net.h"
#include "hux_ota.h"
#include "hux_park.h"
#include "hux_wifi_list.h"
#include "hux_prompts.h"
#include "hux_super.h"
#include "esp_core_dump.h"

static const char *TAG = "hux_console";

#define CONSOLE_TASK_STACK 5120  /* float printf + the Wi-Fi stats dump: 4 KB left 928 B */
#define CONSOLE_TASK_PRIO  3   /* below the button task (4) */
#define CONSOLE_POLL_MS    20
#define LINE_MAX_BYTES     256 /* a Wi-Fi name and password, in hex */
#define SAY_TAIL_MS        300  /* held past the clip: the mic starts ~40 ms after the press */

/* Tasks whose stack headroom `info` reports: ours, then the IDF ones that
 * run our code (the WS client task runs the audio data plane). */
static const char *const TASKS[] = {
    "hux_app", "hux_audio_tx", "hux_mic", "hux_spk", "hux_button", "hux_log_drain",
    "hux_console", "websocket_task", "sys_evt", "tiT", "esp_timer", "hux_opus_dec", "hux_ladder",
    "hux_super", "hux_health", "hux_bat", NULL,
};

static void cmd_info(void) {
    ESP_LOGI(TAG, "info uptime_s=%u heap_internal=%u min=%u largest=%u psram=%u psram_min=%u",
             (unsigned)(esp_timer_get_time() / 1000000),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM));
    int dac = hux_audio_dac_volume_reg();
    ESP_LOGI(TAG, "info ring_used=%u volume=%d dac_reg=0x%02x (%.1f dB) rssi=%d ws_rx_age_ms=%u",
             (unsigned)hux_audio_spk_ring_used(), hux_audio_get_volume(), dac & 0xFF,
             dac < 0 ? -999.0 : (dac - 0xBF) * 0.5, hux_net_rssi(),
             (unsigned)hux_net_rx_age_ms());
    /* Smallest free stack each task has ever had, in bytes. */
    char line[200];
    size_t used = 0;
    for (size_t i = 0; TASKS[i] != NULL && used < sizeof(line); i++) {
        TaskHandle_t t = xTaskGetHandle(TASKS[i]);
        int n = t == NULL ? snprintf(line + used, sizeof(line) - used, " %s=-", TASKS[i])
                          : snprintf(line + used, sizeof(line) - used, " %s=%u", TASKS[i],
                                     (unsigned)uxTaskGetStackHighWaterMark(t));
        if (n < 0) {
            break;
        }
        used += (size_t)n;
    }
    ESP_LOGI(TAG, "info stack_free_min%s", line);
}

static esp_timer_handle_t s_wifi_back;

static void wifi_back_on(void *arg) {
    (void)arg;
    esp_err_t err = esp_wifi_start(); /* STA_START connects */
    ESP_LOGI(TAG, "console.wifi back on err=%s", esp_err_to_name(err));
}

/* The next token of `p` (spaces and tabs between), its length in *n. */
static const char *next_token(const char *p, size_t *n) {
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    *n = strcspn(p, " \t");
    return p;
}

bool hux_console_is_secret(const char *line) {
    size_t n;
    const char *t = next_token(line, &n);
    if (n != 4 || strncmp(t, "wifi", 4) != 0) {
        return false;
    }
    t = next_token(t + n, &n);
    return n == 6 && strncmp(t, "addhex", 6) == 0; /* by token: any spacing */
}

/* Where the line came from (F-0036): adding a network works on USB (join
 * now) and from the server (wss with the device token: staged, or a known
 * name's second password), never over the LAN door (plain HTTP). */
static void run_line(char *line, hux_wifi_src_t src) {
    char *argv[4] = {0};
    int argc = 0;
    for (char *tok = strtok(line, " \t"); tok != NULL && argc < 4; tok = strtok(NULL, " \t")) {
        argv[argc++] = tok;
    }
    if (argc == 0) {
        return;
    }
    const char *cmd = argv[0];
    bool secret = strcmp(cmd, "wifi") == 0 && argc > 1 && strcmp(argv[1], "addhex") == 0;
    ESP_LOGI(TAG, "console.cmd %s%s%s%s%s", cmd, argc > 1 ? " " : "", argc > 1 ? argv[1] : "",
             argc > 2 ? " " : "", secret ? "<redacted>" : argc > 2 ? argv[2] : "");

    if (strcmp(cmd, "wifi") == 0 && argc > 1 &&
        (strcmp(argv[1], "addhex") == 0 || strcmp(argv[1], "forget") == 0) &&
        src == HUX_WIFI_SRC_DOOR) {
        ESP_LOGW(TAG, "console.wifi_%s refused: USB or the server only, never the LAN door",
                 argv[1]);
        return;
    }

    if (strcmp(cmd, "talk") == 0) {
        hux_button_set_serial_talk(true);
    } else if (strcmp(cmd, "release") == 0) {
        hux_button_set_serial_talk(false);
    } else if (strcmp(cmd, "tone") == 0) {
        int hz = argc > 1 ? atoi(argv[1]) : 440;
        int ms = argc > 2 ? atoi(argv[2]) : 1000;
        hux_audio_tone(hz, ms);
    } else if (strcmp(cmd, "prompt") == 0 && argc > 1) {
        /* A sentence stored on the walkie (hux_prompts): `prompt offline`. */
        int id = hux_prompt_find(argv[1]);
        if (id < 0) {
            ESP_LOGW(TAG, "console.prompt unknown=%s", argv[1]);
        } else {
            (void)hux_prompt_play((hux_prompt_t)id);
        }
    } else if (strcmp(cmd, "call") == 0 && argc > 1) {
        /* Bench (F-0031): the mic path as in a call (the echo gate on the
         * speaker's own output, audio.echo every 5 s) without one. */
        hux_audio_bench_call(strcmp(argv[1], "on") == 0);
    } else if (strcmp(cmd, "echo") == 0 && argc > 2 && strcmp(argv[1], "depth") == 0) {
        hux_audio_set_echo_depth(atoi(argv[2])); /* the gate in dB, saved (F-0031) */
    } else if (strcmp(cmd, "echo") == 0 && argc > 1) {
        /* A call's echo (F-0031): off, shadow (measure only), on (gate). */
        int mode = strcmp(argv[1], "off") == 0     ? HUX_ECHO_MODE_OFF
                   : strcmp(argv[1], "shadow") == 0 ? HUX_ECHO_MODE_SHADOW
                   : strcmp(argv[1], "on") == 0     ? HUX_ECHO_MODE_ON
                                                    : -1;
        if (mode < 0) {
            ESP_LOGW(TAG, "console.echo unknown=%s (off, shadow, on)", argv[1]);
        } else {
            hux_audio_set_echo_mode(mode);
        }
    } else if (strcmp(cmd, "opus") == 0 && argc > 2 && strcmp(argv[1], "yield") == 0) {
        hux_opus_set_yield(strcmp(argv[2], "on") == 0); /* the bench repro only (F-0035) */
    } else if (strcmp(cmd, "opus") == 0 && argc > 2 && strcmp(argv[1], "flood") == 0) {
        /* Bench (F-0035): the decoder fed with nothing ever waiting: the
         * speaker held (its audio dropped as decoded) and a stored
         * sentence queued again and again for N s. Amp off first. */
        int s = atoi(argv[2]);
        s = s < 1 ? 1 : s > 30 ? 30 : s;
        int64_t until = esp_timer_get_time() + (int64_t)s * 1000000;
        int clips = 0;
        ESP_LOGW(TAG, "console.opus_flood s=%d start", s);
        while (esp_timer_get_time() < until) {
            hux_audio_spk_hold();
            if (hux_prompt_play(HUX_PROMPT_OFFLINE)) {
                clips++;
            }
        }
        hux_audio_spk_unhold("opus_flood");
        ESP_LOGW(TAG, "console.opus_flood s=%d done clips=%d", s, clips);
    } else if (strcmp(cmd, "led") == 0 && argc > 2 && strcmp(argv[1], "bright") == 0) {
        hux_led_set_bright(atoi(argv[2])); /* the light's peak, % of full, saved (F-0034) */
    } else if (strcmp(cmd, "led") == 0 && argc > 2 && strcmp(argv[1], "demo") == 0) {
        hux_led_demo(argv[2]); /* rainbow, voice: look without a press */
    } else if (strcmp(cmd, "led") == 0 && argc > 2 && strcmp(argv[1], "gain") == 0) {
        /* The hidden LEDs' gain, e.g. `led gain 3.0`, saved (F-0034). */
        hux_led_set_gain((int)lroundf(strtof(argv[2], NULL) * 10.0f));
    } else if (strcmp(cmd, "led") == 0 && argc > 1 && strcmp(argv[1], "walk") == 0) {
        hux_led_demo("walk"); /* each position alone, bottom first: the order */
    } else if (strcmp(cmd, "status") == 0 && argc > 1 && strcmp(argv[1], "poweron") == 0) {
        hux_app_status_power_on(); /* the connection sounds as after a power on (F-0033) */
    } else if (strcmp(cmd, "alert") == 0 && argc > 1 && strcmp(argv[1], "test") == 0) {
        hux_alert_test(); /* one push to Mario's phone, end to end (F-0032) */
    } else if (strcmp(cmd, "calljb") == 0 && argc > 1) {
        hux_audio_set_call_jb(atoi(argv[1])); /* a call's jitter buffer, ms */
    } else if (strcmp(cmd, "super") == 0) {
        hux_super_status();
    } else if (strcmp(cmd, "hang") == 0 && argc > 1) {
        hux_super_test_hang(argv[1]); /* bench only: the walkie restarts */
    } else if (strcmp(cmd, "coredump") == 0 && argc > 1 && strcmp(argv[1], "erase") == 0) {
        /* The dump is never overwritten (a reset loop would wear its
         * sectors): erase it once it has been read (dev.py coredump). */
        esp_err_t err = esp_core_dump_image_erase();
        ESP_LOGW(TAG, "coredump.erased err=%s", esp_err_to_name(err));
    } else if (strcmp(cmd, "vol") == 0 && argc > 1) {
        hux_audio_set_volume(atoi(argv[1]));
    } else if (strcmp(cmd, "wifi") == 0 && argc > 1 && strcmp(argv[1], "off") == 0) {
        /* Back on by itself after N s (default 10): issued over Wi-Fi
         * (tools/dev.py), a plain `wifi off` would lock everyone out
         * until a power cut. */
        int s = argc > 2 ? atoi(argv[2]) : 10;
        if (s < 1 || s > 600) {
            s = 10;
        }
        vTaskDelay(pdMS_TO_TICKS(300)); /* let the developer door's "ok" out first */
        esp_err_t err = esp_wifi_stop();
        if (s_wifi_back == NULL) {
            const esp_timer_create_args_t args = {.callback = wifi_back_on, .name = "wifi_back"};
            esp_timer_create(&args, &s_wifi_back);
        }
        if (s_wifi_back != NULL) {
            esp_timer_stop(s_wifi_back);
            esp_timer_start_once(s_wifi_back, (uint64_t)s * 1000000);
        }
        ESP_LOGI(TAG, "console.wifi stopped err=%s back_in_s=%d", esp_err_to_name(err), s);
    } else if (strcmp(cmd, "wifi") == 0 && argc > 3 && strcmp(argv[1], "addhex") == 0) {
        /* tools/wifi.py: name and password as hex (spaces, quotes). */
        char ssid[33], pass[65];
        if (hux_hex_decode(argv[2], ssid, sizeof(ssid)) < 0 ||
            hux_hex_decode(argv[3], pass, sizeof(pass)) < 0) {
            ESP_LOGW(TAG, "wifi.add refused why=bad_hex");
        } else {
            (void)hux_net_wifi_add(ssid, pass, src);
        }
        memset(pass, 0, sizeof(pass));
    } else if (strcmp(cmd, "wifi") == 0 && argc > 2 && strcmp(argv[1], "forget") == 0) {
        char ssid[33];
        if (hux_hex_decode(argv[2], ssid, sizeof(ssid)) < 0) {
            ESP_LOGW(TAG, "wifi.forget refused why=bad_hex");
        } else {
            (void)hux_net_wifi_forget(ssid, src);
        }
    } else if (strcmp(cmd, "wifi") == 0 && argc > 2 && strcmp(argv[1], "join") == 0) {
        char ssid[33]; /* bench: a known network now, heard or not (F-0027) */
        if (hux_hex_decode(argv[2], ssid, sizeof(ssid)) < 0) {
            ESP_LOGW(TAG, "wifi.join refused why=bad_hex");
        } else {
            (void)hux_net_wifi_join(ssid);
        }
    } else if (strcmp(cmd, "wifi") == 0 && argc > 1 && strcmp(argv[1], "list") == 0) {
        hux_net_wifi_list();
    } else if (strcmp(cmd, "wifi") == 0 && argc > 1 && strcmp(argv[1], "on") == 0) {
        esp_err_t err = esp_wifi_start(); /* STA_START connects */
        ESP_LOGI(TAG, "console.wifi started err=%s", esp_err_to_name(err));
    } else if (strcmp(cmd, "info") == 0) {
        cmd_info();
    } else if (strcmp(cmd, "server") == 0 && argc == 1) {
        hux_net_server_status();
    } else if (strcmp(cmd, "server") == 0 && strcmp(argv[1], "reset") == 0) {
        hux_net_server_reset();
    } else if (strcmp(cmd, "server") == 0 && strcmp(argv[1], "keep") == 0 && argc > 2) {
        /* Moving to another server: saved once a hello succeeds there. */
        int min = argc > 3 ? atoi(argv[3]) : 15;
        hux_net_server_keep(argv[2], (uint32_t)(min > 0 ? min : 15) * 60);
    } else if (strcmp(cmd, "server") == 0 && argc > 1) {
        /* Tests route the board through a proxy: back by itself after
         * N minutes (default 15) or a reboot; never saved. */
        int min = argc > 2 ? atoi(argv[2]) : 15;
        hux_net_set_server(argv[1], (uint32_t)(min > 0 ? min : 15) * 60);
    } else if (strcmp(cmd, "ota") == 0 && argc > 1 && strcmp(argv[1], "confirm") == 0) {
        hux_ota_confirm();
    } else if (strcmp(cmd, "ota") == 0 && argc > 1 && strcmp(argv[1], "rollback") == 0) {
        hux_ota_rollback();
    } else if (strcmp(cmd, "ota") == 0 && argc > 1) {
        /* ESP-IDF's on-demand update: download, check, boot (hux_ota). */
        hux_ota_start(argv[1], argc > 2 && strcmp(argv[2], "force") == 0);
    } else if (strcmp(cmd, "reboot") == 0) {
        /* A software restart: boots the app again, whatever the USB lines
         * are doing. Tools use it instead of pulsing RTS, which on this
         * chip can land it in download mode until a power cut. */
        ESP_LOGW(TAG, "console.reboot");
        vTaskDelay(pdMS_TO_TICKS(100)); /* let the log line out */
        esp_restart();
    } else if (strcmp(cmd, "wifistat") == 0) {
        /* The Wi-Fi driver's own counters (buffers, RX/TX, hardware). */
        esp_wifi_statis_dump(WIFI_STATIS_BUFFER | WIFI_STATIS_RXTX | WIFI_STATIS_HW);
    } else if (strcmp(cmd, "wifiscan") == 0) {
        hux_net_wifi_scan();
    } else if (strcmp(cmd, "wifibw") == 0 && argc > 1) {
        hux_net_set_bandwidth(atoi(argv[1]));
    } else if (strcmp(cmd, "selftest") == 0) {
        hux_audio_selftest(argc > 1 ? atoi(argv[1]) : 1000);
    } else if (strcmp(cmd, "say") == 0 && argc > 1) {
        /* A whole turn with nobody speaking: hold talk for the clip's
         * length (it replaces the mic), then let go. */
        int ms = hux_audio_inject(atoi(argv[1]));
        if (ms > 0) {
            hux_button_set_serial_talk(true);
            vTaskDelay(pdMS_TO_TICKS(ms + SAY_TAIL_MS));
            hux_button_set_serial_talk(false);
            hux_audio_inject_cancel(); /* a press that was ignored left it armed */
            ESP_LOGI(TAG, "console.say released clip=%s held_ms=%d", argv[1], ms + SAY_TAIL_MS);
        }
    } else if (strcmp(cmd, "pa") == 0 && argc > 1) {
        hux_audio_pa(strcmp(argv[1], "on") == 0);
    } else if (strcmp(cmd, "battery") == 0 && argc > 2 && strcmp(argv[1], "cutoff") == 0) {
        /* The cutoff's remote switch (F-0030), kept in NVS. */
        hux_battery_set_cutoff(strcmp(argv[2], "off") != 0);
    } else if (strcmp(cmd, "battery") == 0 && argc > 3 && strcmp(argv[1], "park") == 0) {
        /* Bench (F-0030): park as if the cell read <mv>, for <minutes>. */
        hux_park_test(atoi(argv[2]), atoi(argv[3]));
    } else if (strcmp(cmd, "battery") == 0 && argc > 2 && strcmp(argv[1], "cal") == 0) {
        hux_battery_calibrate(atoi(argv[2])); /* a multimeter's reading of the cell, mV */
    } else if (strcmp(cmd, "battery") == 0) {
        hux_battery_probe();
    } else if (strcmp(cmd, "quiet") == 0) {
        if (argc > 1) {
            hux_audio_quiet((uint32_t)strtoul(argv[1], NULL, 10));
        }
        ESP_LOGI(TAG, "console.quiet left_s=%u", (unsigned)hux_audio_quiet_left_s());
    } else {
        ESP_LOGW(TAG, "console.unknown %s (talk, release, say N, tone [hz] [ms], vol N, pa on|off, "
                      "selftest [hz], wifi off [s]|on|list|join <ssid>|addhex <ssid> <pw>|forget <ssid>, wifistat, wifiscan, wifibw 20|40, info, "
                      "server [<uri> [min] | keep <uri> [min] | reset], quiet [s], battery [cal <mv> | cutoff on|off | park <mv> <min>], echo off|shadow|on, calljb <ms>, alert test, super, "
                      "hang app|spk, coredump erase, reboot, "
                      "ota <url> [force] | confirm | rollback)",
                 cmd);
    }
}

void hux_console_run(char *line) {
    run_line(line, HUX_WIFI_SRC_DOOR); /* the LAN developer door */
}

void hux_console_run_server(char *line) {
    run_line(line, HUX_WIFI_SRC_SERVER); /* the server's dev.cmd */
}

static void console_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "console_task started (talk, release, say, tone, vol, pa, selftest, wifi, info)");

    char line[LINE_MAX_BYTES];
    size_t len = 0;
    bool too_long = false;
    for (;;) {
        /* Straight from the USB RX FIFO, as IDF's own driverless console
         * does: stdin's read() can't see bytes without the USB driver, and
         * installing the driver would reroute every log line through it.
         * Logs only use TX, so this doesn't touch them. */
        uint8_t buf[32];
        uint32_t n = usb_serial_jtag_ll_rxfifo_data_available()
                         ? usb_serial_jtag_ll_read_rxfifo(buf, sizeof(buf))
                         : 0;
        if (n == 0) {
            vTaskDelay(pdMS_TO_TICKS(CONSOLE_POLL_MS));
            continue;
        }
        for (uint32_t i = 0; i < n; i++) {
            char c = (char)buf[i];
            if (c == '\r' || c == '\n') {
                line[len] = '\0';
                if (too_long) {
                    /* Never a cut line: a password cut short would be saved. */
                    ESP_LOGW(TAG, "console.line_too_long max=%d: refused", LINE_MAX_BYTES - 1);
                } else {
                    run_line(line, HUX_WIFI_SRC_USB);
                }
                memset(line, 0, sizeof(line)); /* a password doesn't linger */
                len = 0;
                too_long = false;
            } else if (len < sizeof(line) - 1) {
                line[len++] = c;
            } else {
                too_long = true;
            }
        }
        memset(buf, 0, sizeof(buf)); /* nor here */
    }
}

void hux_console_start(void) {
    BaseType_t ok = xTaskCreate(console_task, "hux_console", CONSOLE_TASK_STACK, NULL,
                                CONSOLE_TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);
}
