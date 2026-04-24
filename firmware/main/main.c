/**
 * Entry point. Keep this file tiny — it exists to wire components
 * together in dependency order. Anything more than bring-up belongs in
 * a component.
 */
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "hux_app.h"
#include "hux_net.h"
#include "secrets.h"

static const char *TAG = "huxley";

void app_main(void) {
    ESP_LOGI(TAG, "huxley-firmware booting");

    /* NVS is required by Wi-Fi for storing calibration data. Erase on
     * version mismatch so a firmware update doesn't wedge on an old
     * layout. */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* App state machine first — net will post events into it as soon as
     * Wi-Fi associates. */
    hux_app_start();

    hux_net_start(&(hux_net_config_t){
        .wifi_ssid = HUX_WIFI_SSID,
        .wifi_password = HUX_WIFI_PASSWORD,
        .server_uri = HUX_SERVER_URI,
    });

    ESP_LOGI(TAG, "app_main complete — tasks running");
}
