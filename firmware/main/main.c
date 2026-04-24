/**
 * Entry point. Keep this file tiny — it exists to wire components
 * together in dependency order. Anything more than bring-up belongs in
 * a component.
 */
#include <inttypes.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "nvs_flash.h"

#include "hux_app.h"
#include "hux_audio.h"
#include "hux_board.h"
#include "hux_button.h"
#include "hux_log.h"
#include "hux_net.h"
#include "secrets.h"

static const char *TAG = "huxley";

/* Boot-persistence stash.
 *
 * Variables marked RTC_NOINIT_ATTR live in RTC slow memory, which
 * survives software resets (panic, watchdog, brownout if above the
 * brownout threshold) but NOT a cold power-on. A magic sentinel
 * distinguishes "uninitialised garbage after cold boot" from
 * "valid value carried across a reboot." The counter is the cheapest
 * crash-loop detector there is — if it jumps faster than expected
 * between your sessions, the board has been rebooting on its own. */
#define HUX_BOOT_MAGIC 0x48554842 /* 'HUXB' */
RTC_NOINIT_ATTR static uint32_t s_boot_magic;
RTC_NOINIT_ATTR static uint32_t s_boot_counter;

static const char *reset_reason_str(esp_reset_reason_t r) {
    switch (r) {
        case ESP_RST_POWERON:   return "POWERON";
        case ESP_RST_EXT:       return "EXT";
        case ESP_RST_SW:        return "SW_RESET";
        case ESP_RST_PANIC:     return "PANIC";
        case ESP_RST_INT_WDT:   return "INT_WDT";
        case ESP_RST_TASK_WDT:  return "TASK_WDT";
        case ESP_RST_WDT:       return "WDT";
        case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
        case ESP_RST_BROWNOUT:  return "BROWNOUT";
        case ESP_RST_SDIO:      return "SDIO";
        default:                return "UNKNOWN";
    }
}

static void log_boot_banner(void) {
    /* Advance the boot counter first so it appears in the banner. */
    if (s_boot_magic != HUX_BOOT_MAGIC) {
        s_boot_magic = HUX_BOOT_MAGIC;
        s_boot_counter = 0;
    }
    s_boot_counter++;

    const esp_app_desc_t *desc = esp_app_get_description();
    esp_reset_reason_t reason = esp_reset_reason();

    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);

    size_t internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);

    ESP_LOGI(TAG,
             "boot version=%s mac=%02x:%02x:%02x:%02x:%02x:%02x "
             "reset=%s boot_counter=%" PRIu32
             " heap_internal=%uKB psram_free=%uKB",
             desc->version,
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
             reset_reason_str(reason),
             s_boot_counter,
             (unsigned)(internal_free / 1024),
             (unsigned)(psram_free / 1024));

    /* If we rebooted from a crash, surface it once. The full backtrace
     * is in the coredump partition — see firmware/docs/debugging.md. */
    if (reason == ESP_RST_PANIC || reason == ESP_RST_INT_WDT ||
        reason == ESP_RST_TASK_WDT) {
        ESP_LOGE(TAG, "previous_boot_crashed reset=%s — decode with "
                      "`esp_coredump info_corefile`",
                 reset_reason_str(reason));
    }
}

void app_main(void) {
    /* Install the log hook FIRST, before any other ESP_LOG call, so
     * every line in this boot — including Wi-Fi driver and WS client
     * output — flows through our parse-and-stream path once a sink is
     * registered. The sink is registered later (after hux_net), so
     * early lines stream nowhere (just serial), which is what we want. */
    hux_log_init();

    log_boot_banner();

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "nvs.reinit reason=%s", esp_err_to_name(ret));
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* App state machine first — net and button both post events into
     * it, so the queue must exist before they start. */
    hux_app_start();

    /* Shared on-board peripherals (I2C bus + TCA9555). Must run before
     * any consumer of either — buttons read it, audio enables the
     * speaker PA through it, audio registers ES7210/ES8311 on the
     * same I2C bus. */
    hux_board_init();

    /* Audio subsystem: I2S1 duplex + ES7210 mic. Task is created but
     * idle until `hux_audio_mic_start()`. v0.3 extends this to open
     * ES8311 for playback on the same duplex channel. */
    hux_audio_init();

    hux_net_start(&(hux_net_config_t){
        .wifi_ssid = HUX_WIFI_SSID,
        .wifi_password = HUX_WIFI_PASSWORD,
        .server_uri = HUX_SERVER_URI,
    });

    /* K2 press/release now drives events into `hux_app`. v0.2 wires
     * these to the mic pipeline; v0.2.0 just surfaces the edges in
     * the log so hardware can be verified ahead of audio. */
    hux_button_start();

    /* Plug the log drain into the net layer. From here on, log lines
     * at or above the remote threshold go to serial AND to the server
     * as client_event:huxley.firmware_log payloads (best-effort —
     * dropped silently when the WS is down). */
    hux_log_set_sink(hux_net_send_log);

    /* Threshold = INFO for dev: the server log becomes a full record
     * of what the board did. WARN/ERROR is the prod default once this
     * stops being a prototype; bump down then.
     * Worst case here is ~300 B/s of JSON over Wi-Fi, which is noise
     * next to audio frames. */
    hux_log_set_remote_level('I');

    ESP_LOGI(TAG, "app_main complete — tasks running");
}
