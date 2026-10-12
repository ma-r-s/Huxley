#include "hux_battery.h"

#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"

#include "hux_audio.h"
#include "hux_battery_cutoff.h"
#include "hux_battery_level.h"

static const char *TAG = "hux_battery";

#define DIVIDER        3    /* R2 200K over R11 100K */
#define SAMPLES        16
#define SETTLE_MS      100  /* 200 nF (C10 + C11) on a 67K source: tau 13 ms */
#define WIRED_MIN_MV   700  /* a 2.1 V cell, well below any working one */
#define WIRED_MAX_MV   1500 /* a 4.5 V cell, above any Li-ion */
#define PULLED_MIN_MV  150  /* the divider holds the pin up against the ~45K pull-down */

/* Every ADC1 pin the firmware leaves free: the schematic's BAT_ADC options
 * (GPIO1, GPIO6) and Waveshare's Arduino demo's (GPIO8). Never GPIO7 (the
 * talk lever) or GPIO10 (I2C). */
static const int PINS[] = {1, 2, 3, 4, 5, 6, 8, 9};
#define NPINS (int)(sizeof(PINS) / sizeof(PINS[0]))

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali[NPINS]; /* per channel: the S3 calibrates each one */
static int s_level_pin = -1;      /* index into PINS once a wired pin is found */
static adc_cali_handle_t s_level_cali; /* that pin's 6 dB calibration */
/* The probe (console task) and the level task share the ADC. */
static SemaphoreHandle_t s_lock;

/* The level (F-0025): once the divider is wired (the solder blob on R78:
 * GPIO6; held the other way round it lands on GPIO1), a reading at boot,
 * every 20 min while the speaker is quiet (a charge drops about 1 % in
 * 15-30 min, so 20 % and 10 % are caught in time), and a fresh one at each
 * press, sent then whatever it reads: "how much battery" is answered from
 * now (Mario, 2026-10-12). Otherwise sent when the percentage changes. */
#define LEVEL_READ_EVERY_MS (20 * 60 * 1000)
#define LEVEL_NOT_WIRED_RETRY_MS (10 * 60 * 1000)
#define LEVEL_BUSY_RETRY_MS 5000
#define LEVEL_LOADED_AFTER_MS (5 * 60 * 1000) /* no quiet minute: read anyway */
#define LEVEL_SAMPLES      32
#define CAL_NVS_NS         "hux_bat"
#define CAL_NVS_KEY        "cal_ppm"
static const int CANDIDATES[] = {6, 1};
static uint32_t s_cal_ppm = 1000000;
static volatile int s_raw_cell_mv = -1; /* before the multimeter calibration */
static volatile int s_cell_mv = -1;
static volatile int s_pct = -1;
static hux_battery_sink_fn s_sink;
static TaskHandle_t s_level_task = NULL;
static volatile bool s_send_next = false; /* a press asked: send this reading */
/* The cutoff (F-0030): its verdict on every reading, and who acts on it. */
#define LEVEL_ZONE_EVERY_MS (2 * 60 * 1000) /* under 10 %: watch it closely */
#define QUIET_FOR_MS        30000          /* a resting reading: the amp quiet this long */
static hux_cutoff_t s_cutoff = {.zone_since_ms = -1};
static hux_battery_cutoff_fn s_cutoff_sink;
/* A remote off switch (NVS, `battery cutoff off`): a reading wrong but
 * believable would otherwise park a full cell, offline, again and again. */
#define CUTOFF_NVS_KEY "cutoff"
static bool s_cutoff_on = true;

bool hux_battery_init(void) {
    adc_oneshot_unit_init_cfg_t unit = {.unit_id = ADC_UNIT_1};
    esp_err_t err = adc_oneshot_new_unit(&unit, &s_adc);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "battery.init_failed err=%s", esp_err_to_name(err));
        s_adc = NULL;
        return false;
    }
    for (int i = 0; i < NPINS; i++) {
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
    int cali = 0;
    for (int i = 0; i < NPINS; i++) {
        cali += s_cali[i] != NULL;
    }
    s_lock = xSemaphoreCreateMutex();
    nvs_handle_t h;
    if (nvs_open(CAL_NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        uint32_t ppm = 0;
        if (nvs_get_u32(h, CAL_NVS_KEY, &ppm) == ESP_OK && ppm > 800000 && ppm < 1200000) {
            s_cal_ppm = ppm;
        }
        uint8_t on = 1;
        if (nvs_get_u8(h, CUTOFF_NVS_KEY, &on) == ESP_OK) {
            s_cutoff_on = on != 0;
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "battery.init pins=%d calibrated=%d cal_ppm=%u cutoff=%s", NPINS, cali,
             (unsigned)s_cal_ppm, s_cutoff_on ? "on" : "off");
    if (!s_cutoff_on || !hux_battery_cal_ok()) {
        /* Said once: the cutoff (F-0030) won't act on any reading. */
        ESP_LOGW(TAG, "battery.cutoff_inactive why=%s cal_ppm=%u",
                 !s_cutoff_on ? "switched_off" : "calibration", (unsigned)s_cal_ppm);
    }
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
        /* The level pin runs at 6 dB once found: read it on its own scale,
         * or the probe reports it ~1.9x too high. */
        bool level = i == s_level_pin;
        adc_cali_handle_t cal = level ? s_level_cali : s_cali[i];
        if (cal == NULL || adc_cali_raw_to_voltage(cal, raw, &mv) != ESP_OK) {
            mv = raw * (level ? 1750 : 3100) / 4095; /* uncalibrated: the range of 6 / 12 dB */
        }
        sum += mv;
        *lo = mv < *lo ? mv : *lo;
        *hi = mv > *hi ? mv : *hi;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    *mean = sum / SAMPLES;
    return true;
}

/* A wired divider reads cell/3 and holds up against the pull-down; a
 * floating pin reads anything and falls to ~0 pulled down. */
static bool probe_pin(int i, bool log) {
    int f, flo, fhi, p, plo, phi;
    gpio_num_t pin = (gpio_num_t)PINS[i];
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool ok = sample(i, &f, &flo, &fhi);
    gpio_pulldown_en(pin);
    vTaskDelay(pdMS_TO_TICKS(SETTLE_MS));
    ok = ok && sample(i, &p, &plo, &phi);
    gpio_pulldown_dis(pin);
    xSemaphoreGive(s_lock);
    if (!ok) {
        ESP_LOGW(TAG, "battery.probe gpio=%d read_failed", PINS[i]);
        return false;
    }
    bool wired = f >= WIRED_MIN_MV && f <= WIRED_MAX_MV && p >= PULLED_MIN_MV;
    if (log || wired) {
        ESP_LOGI(TAG,
                 "battery.probe gpio=%d float_mv=%d float_spread=%d pulled_mv=%d "
                 "pulled_spread=%d cell_mv=%d verdict=%s",
                 PINS[i], f, fhi - flo, p, phi - plo, f * DIVIDER, wired ? "wired" : "not_wired");
    }
    return wired;
}

void hux_battery_probe(void) {
    if (s_adc == NULL) {
        return;
    }
    for (int i = 0; i < NPINS; i++) {
        probe_pin(i, true);
    }
    ESP_LOGI(TAG, "battery.level pin=%d cell_mv=%d raw_cell_mv=%d pct=%d cal_ppm=%u",
             s_level_pin >= 0 ? PINS[s_level_pin] : -1, s_cell_mv, s_raw_cell_mv, s_pct,
             (unsigned)s_cal_ppm);
}

static int index_of(int gpio) {
    for (int i = 0; i < NPINS; i++) {
        if (PINS[i] == gpio) {
            return i;
        }
    }
    return -1;
}

/* The wired pin, read at 6 dB (up to ~1.75 V: cell/3 tops out at 1.4 V)
 * for finer steps than the probe's 12 dB. */
static bool level_pin_find(void) {
    for (size_t c = 0; c < sizeof(CANDIDATES) / sizeof(CANDIDATES[0]); c++) {
        int i = index_of(CANDIDATES[c]);
        if (i < 0 || !probe_pin(i, false)) {
            continue;
        }
        adc_unit_t u;
        adc_channel_t ch;
        adc_oneshot_io_to_channel(PINS[i], &u, &ch);
        adc_oneshot_chan_cfg_t cfg = {.atten = ADC_ATTEN_DB_6, .bitwidth = ADC_BITWIDTH_DEFAULT};
        adc_oneshot_config_channel(s_adc, ch, &cfg);
        adc_cali_curve_fitting_config_t cc = {
            .unit_id = ADC_UNIT_1, .chan = ch, .atten = ADC_ATTEN_DB_6,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        if (adc_cali_create_scheme_curve_fitting(&cc, &s_level_cali) != ESP_OK) {
            s_level_cali = NULL;
        }
        s_level_pin = i;
        ESP_LOGW(TAG, "battery.wired gpio=%d calibrated=%d", PINS[i], s_level_cali != NULL);
        return true;
    }
    return false;
}

/* The median of LEVEL_SAMPLES readings at the pin, in mV (a Wi-Fi burst or
 * a click can't move a median). */
static int level_read_pin_mv(void) {
    adc_unit_t u;
    adc_channel_t ch;
    adc_oneshot_io_to_channel(PINS[s_level_pin], &u, &ch);
    int v[LEVEL_SAMPLES];
    int n = 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    for (int k = 0; k < LEVEL_SAMPLES; k++) {
        int raw = 0, mv = 0;
        if (adc_oneshot_read(s_adc, ch, &raw) != ESP_OK) {
            continue;
        }
        if (s_level_cali == NULL || adc_cali_raw_to_voltage(s_level_cali, raw, &mv) != ESP_OK) {
            mv = raw * 1750 / 4095; /* uncalibrated: 6 dB spans ~1.75 V */
        }
        int j = n++;
        for (; j > 0 && v[j - 1] > mv; j--) { /* insertion sort: keep v ascending */
            v[j] = v[j - 1];
        }
        v[j] = mv;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    xSemaphoreGive(s_lock);
    return n > 0 ? v[n / 2] : -1;
}

static uint32_t uptime_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void level_task(void *arg) {
    (void)arg;
    uint32_t last_quiet = uptime_ms();
    bool told_not_wired = false;
    for (;;) {
        if (s_level_pin < 0 && !level_pin_find()) {
            if (!told_not_wired) {
                ESP_LOGI(TAG, "battery.not_wired: no cell on GPIO6 or GPIO1 (F-0025's blob)");
                told_not_wired = true;
            }
            vTaskDelay(pdMS_TO_TICKS(LEVEL_NOT_WIRED_RETRY_MS));
            continue;
        }
        bool asked = s_send_next;
        bool busy = hux_audio_spk_ring_used() > 0;
        if (!asked && busy && uptime_ms() - last_quiet < LEVEL_LOADED_AFTER_MS) {
            vTaskDelay(pdMS_TO_TICKS(LEVEL_BUSY_RETRY_MS)); /* the amp sags the cell */
            continue;
        }
        if (!busy) {
            last_quiet = uptime_ms();
        }
        int pin_mv = level_read_pin_mv();
        if (pin_mv > 0) {
            int raw = pin_mv * DIVIDER;
            int cell = (int)((int64_t)raw * s_cal_ppm / 1000000);
            int pct = hux_battery_smooth(s_pct, hux_battery_pct(cell));
            bool changed = pct != s_pct;
            s_raw_cell_mv = raw;
            s_cell_mv = cell;
            s_pct = pct;
            if (changed || asked) {
                ESP_LOGI(TAG, "battery.level mv=%d pct=%d pin=%d raw_mv=%d loaded=%d why=%s", cell,
                         pct, PINS[s_level_pin], raw, busy, asked ? "press" : "changed");
                if (s_sink != NULL) {
                    s_sink(cell, pct);
                }
            }
            /* Resting: the amp quiet 30 s and no press under way (a press's
             * reading has the mic and Wi-Fi transmit starting: loaded). */
            bool quiet = !asked && !hux_audio_mic_is_running() &&
                         hux_audio_quiet_ms() >= QUIET_FOR_MS;
            int64_t now = esp_timer_get_time() / 1000;
            hux_cutoff_why_t why = hux_cutoff_step(&s_cutoff, cell, quiet,
                                                   hux_battery_cal_ok() && s_cutoff_on, now);
            if (hux_cutoff_in_zone(&s_cutoff)) {
                ESP_LOGI(TAG, "battery.zone mv=%d quiet=%d in_zone_s=%d loaded_low=%d", cell, quiet,
                         (int)((now - s_cutoff.zone_since_ms) / 1000), s_cutoff.loaded_low);
            }
            if (why != HUX_CUTOFF_NONE) {
                ESP_LOGW(TAG, "battery.cutoff_due mv=%d why=%s cal_ppm=%u", cell,
                         hux_cutoff_why_name(why), (unsigned)s_cal_ppm);
                if (s_cutoff_sink != NULL) {
                    s_cutoff_sink(cell, hux_cutoff_why_name(why)); /* restarts: no return */
                }
            }
        }
        s_send_next = false;
        /* Until the next 20 min (2 under 10 %), or a press asking for one now. */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(hux_cutoff_in_zone(&s_cutoff) ? LEVEL_ZONE_EVERY_MS
                                                                              : LEVEL_READ_EVERY_MS));
    }
}

void hux_battery_start(hux_battery_sink_fn sink) {
    if (s_adc == NULL) {
        return;
    }
    s_sink = sink;
    /* 6 KB: the cutoff (F-0030) runs on it: a TLS send, the prompt, logs. */
    if (xTaskCreate(level_task, "hux_bat", 6144, NULL, 2, &s_level_task) != pdPASS) {
        ESP_LOGE(TAG, "battery.task_create_failed");
    }
}

bool hux_battery_cal_ok(void) {
    return s_cal_ppm >= 950000 && s_cal_ppm <= 1050000; /* within 5 % of the divider */
}

int hux_battery_read_cell_mv(void) {
    if (s_adc == NULL || (s_level_pin < 0 && !level_pin_find())) {
        return -1;
    }
    int pin_mv = level_read_pin_mv();
    if (pin_mv <= 0) {
        return -1;
    }
    return (int)((int64_t)pin_mv * DIVIDER * s_cal_ppm / 1000000);
}

bool hux_battery_cutoff_on(void) {
    return s_cutoff_on;
}

void hux_battery_set_cutoff(bool on) {
    s_cutoff_on = on;
    nvs_handle_t h;
    esp_err_t err = nvs_open(CAL_NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u8(h, CUTOFF_NVS_KEY, on ? 1 : 0);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    ESP_LOGW(TAG, "battery.cutoff_switched on=%d saved=%s", on, esp_err_to_name(err));
}

void hux_battery_set_cutoff_sink(hux_battery_cutoff_fn sink) {
    s_cutoff_sink = sink;
}

bool hux_battery_last(int *mv, int *pct) {
    *mv = s_cell_mv;
    *pct = s_pct;
    return *pct >= 0;
}

void hux_battery_read_now(void) {
    if (s_level_task == NULL || s_level_pin < 0) {
        return;
    }
    s_send_next = true;
    xTaskNotifyGive(s_level_task);
}

void hux_battery_report_now(void) {
    int mv = s_cell_mv, pct = s_pct;
    if (pct >= 0 && s_sink != NULL) {
        s_sink(mv, pct);
    }
}

void hux_battery_calibrate(int measured_mv) {
    if (s_raw_cell_mv <= 0 || measured_mv < 2500 || measured_mv > 4500) {
        ESP_LOGW(TAG, "battery.cal_refused measured_mv=%d raw_cell_mv=%d (no reading yet, or "
                      "out of range)", measured_mv, s_raw_cell_mv);
        return;
    }
    uint32_t ppm = (uint32_t)((int64_t)measured_mv * 1000000 / s_raw_cell_mv);
    if (ppm < 800000 || ppm > 1200000) {
        ESP_LOGW(TAG, "battery.cal_refused ppm=%u: more than 20 %% off, check the meter",
                 (unsigned)ppm);
        return;
    }
    s_cal_ppm = ppm;
    nvs_handle_t h;
    esp_err_t err = nvs_open(CAL_NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u32(h, CAL_NVS_KEY, ppm);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    ESP_LOGW(TAG, "battery.calibrated measured_mv=%d raw_cell_mv=%d cal_ppm=%u err=%s",
             measured_mv, s_raw_cell_mv, (unsigned)ppm, esp_err_to_name(err));
}
