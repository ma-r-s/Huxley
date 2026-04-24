#include "hux_audio.h"

#include <stdatomic.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "hux_board.h"

static const char *TAG = "hux_audio";

/* -- I2S topology -------------------------------------------------------
 *
 * Both ES7210 (mic ADC) and ES8311 (speaker codec) share a single duplex
 * I2S channel on I2S1. LRCK, BCLK, MCLK drive both; DIN is from ES7210,
 * DOUT goes to ES8311. One i2s_new_channel() call produces handles for
 * RX + TX; individual channels are enabled independently. v0.2.1 only
 * activates RX; v0.3 extends to TX.
 *
 * Pins (from Waveshare schematic, see firmware/docs/decisions.md):
 */
#define I2S_PORT     I2S_NUM_1
#define I2S_GPIO_MCLK   12
#define I2S_GPIO_BCLK   13
#define I2S_GPIO_LRCK   14
#define I2S_GPIO_DIN    15
#define I2S_GPIO_DOUT   16

/* -- Sample / frame math ------------------------------------------------
 *
 * 24 kHz mono PCM16 matches the Huxley server's wire format exactly
 * (docs/protocol.md), so no resampling is needed on either side.
 *
 * The ES7210 streams 4-channel TDM in the "RMNM" layout: each frame is
 * [Ref, Mic1, Noise, Mic2] interleaved 16-bit samples. We extract Mic1
 * (index 1) for a clean single-speaker voice capture. The other three
 * channels are a beamforming hint we won't use until there's a reason.
 */
#define MIC_SAMPLE_RATE_HZ    24000
#define MIC_CHANNELS_TDM      4        /* RMNM */
#define MIC_BITS_PER_SAMPLE   16
#define FRAME_MS              20
#define FRAME_SAMPLES         (MIC_SAMPLE_RATE_HZ * FRAME_MS / 1000)   /* 480 */
#define FRAME_RAW_BYTES       (FRAME_SAMPLES * MIC_CHANNELS_TDM * sizeof(int16_t)) /* 3840 */

#define MIC_TASK_STACK        6144
#define MIC_TASK_PRIO         8   /* above hux_app (5); audio pacing wins */

static i2s_chan_handle_t s_i2s_tx = NULL; /* opened but idle until v0.3 */
static i2s_chan_handle_t s_i2s_rx = NULL;
static const audio_codec_data_if_t *s_mic_data_if = NULL;
static const audio_codec_ctrl_if_t *s_mic_ctrl_if = NULL;
static const audio_codec_if_t *s_mic_codec_if = NULL;
static esp_codec_dev_handle_t s_mic_dev = NULL;

static _Atomic(hux_audio_mic_frame_fn) s_mic_sink = NULL;
static _Atomic bool s_mic_running = false;

/* Static capture scratch — zero heap on the hot path. BSS-resident,
 * single-writer (mic task). */
static uint8_t s_raw_buf[FRAME_RAW_BYTES];
static int16_t s_mono_buf[FRAME_SAMPLES];

static esp_err_t i2s_bring_up(void) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx),
                        TAG, "i2s.new_channel");

    /* Standard Philips I2S, 32-bit slots, stereo (ES7210 packs 4 mics
     * into the 2 × 32-bit slot pair via its own internal TDM-ish
     * muxing — the `esp_codec_dev` abstraction knows to read the
     * right count). Keeping the clock on a nice multiple of the
     * sample rate avoids needing APLL. */
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                       I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_MCLK,
            .bclk = I2S_GPIO_BCLK,
            .ws   = I2S_GPIO_LRCK,
            .dout = I2S_GPIO_DOUT,
            .din  = I2S_GPIO_DIN,
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_cfg),
                        TAG, "i2s.init_tx");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_cfg),
                        TAG, "i2s.init_rx");

    /* Only enable RX for v0.2.1 — TX stays idle until ES8311 is wired
     * in v0.3. Enabling an unused TX channel would park LRCK/BCLK at
     * the codec's expected cadence without driving DOUT, which is
     * fine but unnecessary. */
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_rx), TAG, "i2s.enable_rx");
    return ESP_OK;
}

static esp_err_t es7210_bring_up(void) {
    audio_codec_i2s_cfg_t data_cfg = {
        .port = I2S_PORT,
        .rx_handle = s_i2s_rx,
        .tx_handle = NULL,
    };
    s_mic_data_if = audio_codec_new_i2s_data(&data_cfg);
    if (s_mic_data_if == NULL) {
        ESP_LOGE(TAG, "es7210.data_if_null");
        return ESP_FAIL;
    }

    audio_codec_i2c_cfg_t ctrl_cfg = {
        .addr = ES7210_CODEC_DEFAULT_ADDR,
        .bus_handle = hux_board_i2c_bus(),
    };
    s_mic_ctrl_if = audio_codec_new_i2c_ctrl(&ctrl_cfg);
    if (s_mic_ctrl_if == NULL) {
        ESP_LOGE(TAG, "es7210.ctrl_if_null");
        return ESP_FAIL;
    }

    /* All four mic channels enabled so the TDM frame shape matches
     * the Waveshare reference. We pick ch1 downstream; enabling only
     * one would change the slot layout and complicate extraction. */
    es7210_codec_cfg_t es7210_cfg = {
        .ctrl_if = s_mic_ctrl_if,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3 | ES7210_SEL_MIC4,
    };
    s_mic_codec_if = es7210_codec_new(&es7210_cfg);
    if (s_mic_codec_if == NULL) {
        ESP_LOGE(TAG, "es7210.codec_if_null");
        return ESP_FAIL;
    }

    esp_codec_dev_cfg_t dev_cfg = {
        .codec_if = s_mic_codec_if,
        .data_if = s_mic_data_if,
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
    };
    s_mic_dev = esp_codec_dev_new(&dev_cfg);
    if (s_mic_dev == NULL) {
        ESP_LOGE(TAG, "es7210.dev_null");
        return ESP_FAIL;
    }

    /* Open at the wire-target rate. `channel = 2` means 2 × 32-bit
     * stereo slots per frame; ES7210 packs 4 × 16-bit mics into
     * those 64 bits. `bits_per_sample = 32` matches I2S slot width.
     * The decoded-into-int16_t buffer at the read site gives us the
     * 4-channel interleaved view. */
    esp_codec_dev_sample_info_t fs = {
        .sample_rate = MIC_SAMPLE_RATE_HZ,
        .channel = 2,
        .bits_per_sample = 32,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(s_mic_dev, &fs), TAG, "es7210.open");

    /* Moderate gain — 30 dB is the Waveshare default. The ES7210 has
     * per-channel gain registers; we set all four identically to
     * keep the raw frame layout uniform. */
    for (int ch = 0; ch < 4; ch++) {
        esp_codec_dev_set_in_channel_gain(s_mic_dev,
                                          ESP_CODEC_DEV_MAKE_CHANNEL_MASK(ch),
                                          30.0f);
    }
    return ESP_OK;
}

static void mic_task(void *unused) {
    (void)unused;
    ESP_LOGI(TAG, "mic_task started sr=%d frame_ms=%d samples=%d",
             MIC_SAMPLE_RATE_HZ, FRAME_MS, FRAME_SAMPLES);

    for (;;) {
        if (!atomic_load_explicit(&s_mic_running, memory_order_acquire)) {
            /* Idle — park for a frame period, not forever, so
             * start()/stop() transitions are responsive. */
            vTaskDelay(pdMS_TO_TICKS(FRAME_MS));
            continue;
        }

        esp_err_t err = esp_codec_dev_read(s_mic_dev, s_raw_buf, sizeof(s_raw_buf));
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "mic.read_failed err=%s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(FRAME_MS));
            continue;
        }

        /* RMNM: 4 × int16 per time instant. We want Mic1 at index 1. */
        const int16_t *in = (const int16_t *)s_raw_buf;
        for (size_t i = 0; i < FRAME_SAMPLES; i++) {
            s_mono_buf[i] = in[MIC_CHANNELS_TDM * i + 1];
        }

        hux_audio_mic_frame_fn sink =
            atomic_load_explicit(&s_mic_sink, memory_order_acquire);
        if (sink != NULL) {
            sink(s_mono_buf, FRAME_SAMPLES);
        }
    }
}

void hux_audio_init(void) {
    if (s_mic_dev != NULL) {
        return; /* Idempotent. */
    }

    if (i2s_bring_up() != ESP_OK) {
        ESP_LOGE(TAG, "i2s.bring_up_failed");
        return;
    }
    if (es7210_bring_up() != ESP_OK) {
        ESP_LOGE(TAG, "es7210.bring_up_failed");
        return;
    }

    BaseType_t ok = xTaskCreate(mic_task, "hux_mic", MIC_TASK_STACK,
                                NULL, MIC_TASK_PRIO, NULL);
    configASSERT(ok == pdPASS);

    ESP_LOGI(TAG, "audio.init sr=%d mic=ES7210 channels=RMNM(take ch1)",
             MIC_SAMPLE_RATE_HZ);
}

void hux_audio_set_mic_sink(hux_audio_mic_frame_fn sink) {
    atomic_store_explicit(&s_mic_sink, sink, memory_order_release);
}

void hux_audio_mic_start(void) {
    atomic_store_explicit(&s_mic_running, true, memory_order_release);
    ESP_LOGI(TAG, "mic.start");
}

void hux_audio_mic_stop(void) {
    atomic_store_explicit(&s_mic_running, false, memory_order_release);
    ESP_LOGI(TAG, "mic.stop");
}

bool hux_audio_mic_is_running(void) {
    return atomic_load_explicit(&s_mic_running, memory_order_acquire);
}
