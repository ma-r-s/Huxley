#include "hux_battery.h"

#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "hux_battery";

#define DIVIDER        3    /* R2 200K over R11 100K */
#define SAMPLES        16
#define SETTLE_MS      100  /* 200 nF (C10 + C11) on a 67K source: tau 13 ms */
#define WIRED_MIN_MV   700  /* a 2.1 V cell, well below any working one */
#define WIRED_MAX_MV   1500 /* a 4.5 V cell, above any Li-ion */
#define PULLED_MIN_MV  150  /* the divider holds the pin up against the ~45K pull-down */

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali[2]; /* per channel: the S3 calibrates each one */
static const int PINS[2] = {1, 6};

bool hux_battery_init(void) {
    adc_oneshot_unit_init_cfg_t unit = {.unit_id = ADC_UNIT_1};
    esp_err_t err = adc_oneshot_new_unit(&unit, &s_adc);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "battery.init_failed err=%s", esp_err_to_name(err));
        s_adc = NULL;
        return false;
    }
    for (int i = 0; i < 2; i++) {
        adc_unit_t u;
        adc_channel_t ch;
        if (adc_oneshot_io_to_channel(PINS[i], &u, &ch) != ESP_OK) {
            continue;
        }
        adc_oneshot_chan_cfg_t cfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
        adc_oneshot_config_channel(s_adc, ch, &cfg);
        adc_cali_curve_fitting_config_t cc = {
            .unit_id = ADC_UNIT_1, .chan = ch, .atten = ADC_ATTEN_DB_12,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        if (adc_cali_create_scheme_curve_fitting(&cc, &s_cali[i]) != ESP_OK) {
            s_cali[i] = NULL;
        }
    }
    ESP_LOGI(TAG, "battery.init cali_gpio1=%d cali_gpio6=%d", s_cali[0] != NULL,
             s_cali[1] != NULL);
    return true;
}

/* Mean, min and max of SAMPLES calibrated readings, in mV at the pin. */
static bool sample(int i, int *mean, int *lo, int *hi) {
    adc_unit_t u;
    adc_channel_t ch;
    if (s_adc == NULL || adc_oneshot_io_to_channel(PINS[i], &u, &ch) != ESP_OK) {
        return false;
    }
    int sum = 0;
    *lo = 1 << 30;
    *hi = -1;
    for (int n = 0; n < SAMPLES; n++) {
        int raw = 0, mv = 0;
        if (adc_oneshot_read(s_adc, ch, &raw) != ESP_OK) {
            return false;
        }
        if (s_cali[i] == NULL || adc_cali_raw_to_voltage(s_cali[i], raw, &mv) != ESP_OK) {
            mv = raw * 3100 / 4095; /* uncalibrated: 12 dB spans ~3.1 V */
        }
        sum += mv;
        *lo = mv < *lo ? mv : *lo;
        *hi = mv > *hi ? mv : *hi;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    *mean = sum / SAMPLES;
    return true;
}

void hux_battery_probe(void) {
    for (int i = 0; i < 2; i++) {
        int f, flo, fhi, p, plo, phi;
        gpio_num_t pin = (gpio_num_t)PINS[i];
        bool ok = sample(i, &f, &flo, &fhi);
        gpio_pulldown_en(pin);
        vTaskDelay(pdMS_TO_TICKS(SETTLE_MS));
        ok = ok && sample(i, &p, &plo, &phi);
        gpio_pulldown_dis(pin);
        if (!ok) {
            ESP_LOGW(TAG, "battery.probe gpio=%d read_failed", PINS[i]);
            continue;
        }
        bool wired = f >= WIRED_MIN_MV && f <= WIRED_MAX_MV && p >= PULLED_MIN_MV;
        ESP_LOGI(TAG,
                 "battery.probe gpio=%d float_mv=%d float_spread=%d pulled_mv=%d "
                 "pulled_spread=%d cell_mv=%d verdict=%s",
                 PINS[i], f, fhi - flo, p, phi - plo, f * DIVIDER, wired ? "wired" : "not_wired");
    }
}
