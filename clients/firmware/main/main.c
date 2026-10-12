/**
 * Entry point. Keep this file tiny — it exists to wire components
 * together in dependency order. Anything more than bring-up belongs in
 * a component.
 */
#include <inttypes.h>
#include <stdio.h>
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
#include "hux_console.h"
#include "hux_dev.h"
#include "hux_log.h"
#include "hux_net.h"
#include "hux_ota.h"
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
        case ESP_RST_USB:       return "USB";      /* also esptool / the monitor resetting it */
        case ESP_RST_JTAG:      return "JTAG";
        case ESP_RST_EFUSE:     return "EFUSE";
        case ESP_RST_PWR_GLITCH: return "PWR_GLITCH";
        case ESP_RST_CPU_LOCKUP: return "CPU_LOCKUP";
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
    /* Same identity for the server, sent on every connect (hux_app). */
    hux_app_device_info_t info = {
        .version = desc->version,
        .reset = reset_reason_str(reason),
        .boot_counter = s_boot_counter,
    };
    snprintf(info.mac, sizeof(info.mac), "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    hux_app_set_device_info(&info);

    /* Not ESP_RST_WDT: tools/flash.sh ends every flash with esptool's
     * watchdog reset (F-0020); the watchdogs that catch a hang report
     * INT_WDT / TASK_WDT. */
    if (reason == ESP_RST_PANIC || reason == ESP_RST_INT_WDT ||
        reason == ESP_RST_TASK_WDT || reason == ESP_RST_BROWNOUT ||
        reason == ESP_RST_CPU_LOCKUP) {
        ESP_LOGE(TAG, "previous_boot_crashed reset=%s — decode with "
                      "`esp_coredump info_corefile`",
                 reset_reason_str(reason));
    }
}

void app_main(void) {
    /* Install the log hook FIRST, before any other ESP_LOG call, so
     * every line in this boot — including Wi-Fi driver and WS client
     * output — flows through our parse-and-stream path once a sink is
     * registered. The sink is registered later (after hux_net); early
     * lines wait in the queue and reach the server on the first
     * connect (F-0022), so the boot is in the server log too. */
    hux_log_init();
    hux_log_set_remote_level('I');

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

    /* Talk input: the case's lever on GPIO7 or the board's K2; its
     * press/release events drive the mic pipeline in `hux_app`. */
    hux_button_start();

    /* Bench commands on the USB serial console (talk, tone, vol, wifi,
     * info): drives the board from the Mac. */
    hux_console_start();

    /* Updates over Wi-Fi: confirm a freshly updated image once it has run
     * a while (ESP-IDF app rollback), and the developer door (HTTP) that
     * tools/dev.py uses to update and look at the board. */
    hux_ota_boot_check();
#ifdef HUX_DEV_TOKEN
    hux_dev_start(HUX_DEV_TOKEN);
#else
    hux_dev_start(NULL);
#endif

    /* Plug the log drain into the net layer: log lines at or above the
     * remote threshold go to serial AND to the server as
     * client_event:huxley.firmware_log payloads. While the WS is down
     * they wait (up to 256 lines; past that the newest are dropped and
     * counted in log.dropped). */
    hux_log_set_sink(hux_net_send_log);

    /* Route inbound audio messages straight to the speaker ring.
     * `hux_net` reassembles fragments + base64-decodes the payload,
     * then calls this sink with raw PCM bytes. hux_audio's consumer
     * task pulls from the ring and feeds I2S TX via ES8311. */
    hux_net_set_audio_sink(hux_audio_spk_push);
    /* `audio_clear` on the same task, in order with the audio. */
    hux_net_set_audio_clear_sink(hux_audio_on_audio_clear);

    /* Remote threshold is INFO (set right after hux_log_init, so boot
     * lines qualify): the server log is a full record of what the board
     * did. WARN/ERROR is the prod default once this stops being a
     * prototype. Worst case ~300 B/s of JSON, noise next to audio. */

    /* WS transport stack stays at default INFO. Uncomment the block
     * below (and keep `CONFIG_LOG_MAXIMUM_LEVEL_DEBUG=y` in
     * sdkconfig.defaults) to light up per-frame DEBUG logs on
     * serial — useful for WS / TCP / TLS diagnosis.
     * See firmware/docs/debugging.md.
     *
     *   esp_log_level_set("websocket_client", ESP_LOG_DEBUG);
     *   esp_log_level_set("transport_ws",     ESP_LOG_DEBUG);
     *   esp_log_level_set("transport_base",   ESP_LOG_DEBUG);
     *   esp_log_level_set("esp-tls",          ESP_LOG_DEBUG);
     */

    ESP_LOGI(TAG, "app_main complete — tasks running");
}
