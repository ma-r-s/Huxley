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
#include "hux_battery.h"
#include "hux_board.h"
#include "hux_button.h"
#include "hux_console.h"
#include "hux_dev.h"
#include "hux_log.h"
#include "hux_net.h"
#include "hux_opus.h"
#include "hux_health.h"
#include "hux_super.h"
#include "hux_ota.h"
#include "hux_park.h"
#include "secrets.h"

/* The developer door is how this walkie is updated and debugged without
 * opening its case: a build without it would need USB forever, and the
 * update it arrived by could never be confirmed (hux_ota). */
#ifndef HUX_DEV_TOKEN
#error "HUX_DEV_TOKEN missing from main/secrets.h (see secrets.example.h)"
#endif
_Static_assert(sizeof(HUX_DEV_TOKEN) - 1 <= 64, "HUX_DEV_TOKEN: 64 characters at most");

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

/* A pending image's silent self-test (F-0028): no sound, nothing he
 * notices. Every part kept working (the supervisor reported no stall),
 * both codecs and the expander answer on I2C, and memory has room. */
#define SELFTEST_HEAP_MIN 40000

static bool update_selftest(const char **why) {
    if (hux_super_stalls() > 0) {
        *why = "stall";
        return false;
    }
    int dac = -1, adc = -1;
    bool codecs = hux_audio_codecs_answer(&dac, &adc);
    uint32_t reads = hux_button_tca_reads();
    vTaskDelay(pdMS_TO_TICKS(200));
    bool expander = hux_button_tca_reads() > reads;
    size_t heap_min = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    ESP_LOGW(TAG, "selftest dac_id=0x%02x adc_id=0x%02x expander=%d heap_min=%u", dac & 0xFF,
             adc & 0xFF, expander, (unsigned)heap_min);
    if (!codecs) {
        *why = dac < 0 ? "codec_es8311" : "codec_es7210";
        return false;
    }
    if (!expander) {
        *why = "expander";
        return false;
    }
    if (heap_min < SELFTEST_HEAP_MIN) {
        *why = "heap";
        return false;
    }
    return true;
}

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

/* The barge-in hold released from the Opus queue (model_speaking false
 * after the packets queued before it). */
static void unhold_after_queue(void) {
    hux_audio_spk_unhold("model_speaking_false");
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

    /* Shared on-board peripherals (I2C bus + TCA9555). Must run before
     * any consumer of either — buttons read it, audio enables the
     * speaker PA through it, audio registers ES7210/ES8311 on the
     * same I2C bus. It leaves the amp off. */
    hux_board_init();

    /* Cell voltage on ADC1 (Wi-Fi only uses ADC2). The console's
     * `battery` probes whether the divider is wired at all. */
    hux_battery_init();

    /* The low-battery cutoff (F-0030): a spent cell parks here, before
     * Wi-Fi, audio or the supervisor draw anything, until a charger lifts
     * it; returns at once otherwise. */
    hux_park_at_boot();

    /* App state machine next — net and button both post events into
     * it, so the queue must exist before they start. */
    hux_app_start();

    /* Audio subsystem: I2S1 duplex + ES7210 mic. Task is created but
     * idle until `hux_audio_mic_start()`. v0.3 extends this to open
     * ES8311 for playback on the same duplex channel. */
    hux_audio_init();

    /* Opus (F-0024), before the network: the WebSocket asks the server
     * for it only if the codec is up. Decoded packets go to the same ring,
     * a queued audio_clear / release to the same calls, in order. */
    hux_opus_init(hux_audio_spk_push, hux_audio_on_audio_clear, unhold_after_queue);

    hux_net_start(&(hux_net_config_t){
        .wifi_ssid = HUX_WIFI_SSID,
        .wifi_password = HUX_WIFI_PASSWORD,
#ifdef HUX_WIFI2_SSID
        .wifi2_ssid = HUX_WIFI2_SSID,
        .wifi2_password = HUX_WIFI2_PASSWORD,
#endif
#ifdef HUX_WIFI3_SSID
        .wifi3_ssid = HUX_WIFI3_SSID,
        .wifi3_password = HUX_WIFI3_PASSWORD,
#endif
        .server_uri = HUX_SERVER_URI,
        .device_token = HUX_DEVICE_TOKEN,
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
    hux_dev_start(HUX_DEV_TOKEN);

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

    /* server_event dev.cmd runs console lines; updates pulled from the
     * server report through the app and keep themselves only after the
     * silent self-test below (F-0028). */
    hux_app_set_dev_cmd_runner(hux_console_run);
    hux_ota_set_status_sink(hux_app_firmware_status);
    hux_ota_set_selftest(update_selftest);

    /* The battery level once a minute, to the server through the app
     * (F-0025); without the solder blob it says not_wired once. */
    hux_battery_set_cutoff_sink(hux_park_cutoff);
    hux_battery_start(hux_app_report_battery);

    /* The minute line for the caregiver's view (device.health, F-0027). */
    hux_health_start();

    /* Last: the supervisor watches every part started above and feeds the
     * task watchdog only while they all keep working (F-0027). */
    hux_super_start();


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
