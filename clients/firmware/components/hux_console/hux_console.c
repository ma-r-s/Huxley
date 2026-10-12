#include "hux_console.h"

#include <stdio.h>
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

#include "hux_audio.h"
#include "hux_button.h"
#include "hux_net.h"
#include "hux_ota.h"

static const char *TAG = "hux_console";

#define CONSOLE_TASK_STACK 5120  /* float printf + the Wi-Fi stats dump: 4 KB left 928 B */
#define CONSOLE_TASK_PRIO  3   /* below the button task (4) */
#define CONSOLE_POLL_MS    20
#define LINE_MAX_BYTES     64
#define SAY_TAIL_MS        300  /* held past the clip: the mic starts ~40 ms after the press */

/* Tasks whose stack headroom `info` reports: ours, then the IDF ones that
 * run our code (the WS client task runs the audio data plane). */
static const char *const TASKS[] = {
    "hux_app", "hux_audio_tx", "hux_mic", "hux_spk", "hux_button", "hux_log_drain",
    "hux_console", "websocket_task", "sys_evt", "tiT", "esp_timer", NULL,
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

void hux_console_run(char *line) {
    char *argv[4] = {0};
    int argc = 0;
    for (char *tok = strtok(line, " \t"); tok != NULL && argc < 4; tok = strtok(NULL, " \t")) {
        argv[argc++] = tok;
    }
    if (argc == 0) {
        return;
    }
    const char *cmd = argv[0];
    ESP_LOGI(TAG, "console.cmd %s%s%s%s%s", cmd, argc > 1 ? " " : "", argc > 1 ? argv[1] : "",
             argc > 2 ? " " : "", argc > 2 ? argv[2] : "");

    if (strcmp(cmd, "talk") == 0) {
        hux_button_set_serial_talk(true);
    } else if (strcmp(cmd, "release") == 0) {
        hux_button_set_serial_talk(false);
    } else if (strcmp(cmd, "tone") == 0) {
        int hz = argc > 1 ? atoi(argv[1]) : 440;
        int ms = argc > 2 ? atoi(argv[2]) : 1000;
        hux_audio_tone(hz, ms);
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
    } else if (strcmp(cmd, "wifi") == 0 && argc > 1 && strcmp(argv[1], "on") == 0) {
        esp_err_t err = esp_wifi_start(); /* STA_START connects */
        ESP_LOGI(TAG, "console.wifi started err=%s", esp_err_to_name(err));
    } else if (strcmp(cmd, "info") == 0) {
        cmd_info();
    } else if (strcmp(cmd, "server") == 0 && argc > 1) {
        /* Until reboot: tests route the board through a proxy. */
        hux_net_set_server(argv[1]);
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
    } else {
        ESP_LOGW(TAG, "console.unknown %s (talk, release, say N, tone [hz] [ms], vol N, pa on|off, "
                      "selftest [hz], wifi off [s]|on, wifistat, info, server <uri>, reboot, "
                      "ota <url> [force])",
                 cmd);
    }
}

static void console_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "console_task started (talk, release, say, tone, vol, pa, selftest, wifi, info)");

    char line[LINE_MAX_BYTES];
    size_t len = 0;
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
                hux_console_run(line);
                len = 0;
            } else if (len < sizeof(line) - 1) {
                line[len++] = c;
            }
        }
    }
}

void hux_console_start(void) {
    BaseType_t ok = xTaskCreate(console_task, "hux_console", CONSOLE_TASK_STACK, NULL,
                                CONSOLE_TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);
}
