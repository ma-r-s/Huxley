#include "hux_opus.h"

#include <stdatomic.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_opus_dec.h"
#include "esp_opus_enc.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "hux_opus";

/* Uplink is speech: VOIP mode at 24 kbit/s (xiaozhi's device encoder is
 * VOIP too). Complexity 3: cheap on the S3, still clean speech. */
#define ENC_BITRATE    24000
#define ENC_COMPLEXITY 3
#define DEC_TASK_STACK (24 * 1024) /* the codec README: ~20 KB for decoders */
#define DEC_TASK_PRIO  7           /* below the speaker (8), above the app (5) */
#define RX_QUEUE_DEPTH 48          /* 2.9 s of 60 ms packets, in PSRAM */
#define RX_WAIT_MS     5000

typedef enum { RX_PACKET = 0, RX_CLEAR, RX_RESET, RX_UNHOLD } rx_kind_t;

typedef struct {
    uint16_t kind; /* rx_kind_t */
    uint16_t len;
    uint8_t data[HUX_OPUS_MAX_PACKET];
} rx_item_t;

static void *s_enc;
static void *s_dec;
static QueueHandle_t s_rx_q;
static hux_opus_pcm_sink_fn s_pcm_sink;
static hux_opus_marker_fn s_clear_sink;
static hux_opus_marker_fn s_unhold_sink;
static TaskHandle_t s_dec_task;
static SemaphoreHandle_t s_ctl_lock; /* s_ctl_item: unhold/reset from any task */
static _Atomic uint32_t s_dec_errors;
/* Up to 120 ms per packet (an Opus packet may hold several frames). */
EXT_RAM_BSS_ATTR static int16_t s_dec_pcm[HUX_OPUS_SAMPLE_RATE / 1000 * 120];
/* Staging for the queue (PSRAM, 1.5 KB each): the decoder's; the
 * WebSocket task's (packets, clears); and one for the markers any task may
 * send (unhold from the app; reset from whichever task the library runs
 * its disconnect on), under s_ctl_lock. */
EXT_RAM_BSS_ATTR static rx_item_t s_dec_item;
EXT_RAM_BSS_ATTR static rx_item_t s_ws_item;
EXT_RAM_BSS_ATTR static rx_item_t s_ctl_item;
static _Atomic uint32_t s_enc_us, s_enc_n, s_dec_us, s_dec_n;
/* Items the decode task took off s_rx_q (packets and markers): the
 * supervisor's "work done" for the decoder (F-0027). */
static _Atomic uint32_t s_rx_taken = 0;
/* ms of packets hux_opus_rx_reset threw away, ever (the hello's
 * ring_left_ms counts them, T1.44). */
static _Atomic uint32_t s_rx_dropped_ms = 0;

static void decode_task(void *unused) {
    (void)unused;
    rx_item_t *it = &s_dec_item;
    uint32_t errors = 0;
    for (;;) {
        if (xQueueReceive(s_rx_q, it, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        atomic_fetch_add(&s_rx_taken, 1);
        if (it->kind == RX_UNHOLD) {
            if (s_unhold_sink != NULL) {
                s_unhold_sink();
            }
            continue;
        }
        if (it->kind == RX_CLEAR || it->kind == RX_RESET) {
            esp_opus_dec_reset(s_dec);
            if (it->kind == RX_CLEAR && s_clear_sink != NULL) {
                s_clear_sink();
            }
            continue;
        }
        esp_audio_dec_in_raw_t raw = {.buffer = it->data, .len = it->len};
        esp_audio_dec_out_frame_t out = {.buffer = (uint8_t *)s_dec_pcm, .len = sizeof(s_dec_pcm)};
        esp_audio_dec_info_t info = {0};
        int64_t t0 = esp_timer_get_time();
        esp_audio_err_t err = esp_opus_dec_decode(s_dec, &raw, &out, &info);
        atomic_fetch_add(&s_dec_us, (uint32_t)(esp_timer_get_time() - t0));
        atomic_fetch_add(&s_dec_n, 1);
        if (err != ESP_AUDIO_ERR_OK) {
            atomic_fetch_add(&s_dec_errors, 1);
            if ((errors++ % 50) == 0) { /* one line per 50: a bad stream can't flood */
                ESP_LOGW(TAG, "opus.decode_failed err=%d len=%u needed=%u count=%u", (int)err,
                         (unsigned)it->len, (unsigned)out.needed_size, (unsigned)errors);
            }
            continue;
        }
        if (out.decoded_size > 0 && s_pcm_sink != NULL) {
            s_pcm_sink((const uint8_t *)s_dec_pcm, out.decoded_size);
        }
    }
}

bool hux_opus_init(hux_opus_pcm_sink_fn pcm_sink, hux_opus_marker_fn clear_sink,
                   hux_opus_marker_fn unhold_sink) {
    s_pcm_sink = pcm_sink;
    s_clear_sink = clear_sink;
    s_unhold_sink = unhold_sink;

    esp_opus_enc_config_t ecfg = ESP_OPUS_ENC_CONFIG_DEFAULT();
    ecfg.sample_rate = HUX_OPUS_SAMPLE_RATE;
    ecfg.channel = 1;
    ecfg.bits_per_sample = 16;
    ecfg.bitrate = ENC_BITRATE;
    ecfg.frame_duration = ESP_OPUS_ENC_FRAME_DURATION_60_MS;
    ecfg.application_mode = ESP_OPUS_ENC_APPLICATION_VOIP;
    ecfg.complexity = ENC_COMPLEXITY;
    ecfg.enable_fec = false;
    ecfg.enable_dtx = false;
    ecfg.enable_vbr = true;
    esp_audio_err_t eerr = esp_opus_enc_open(&ecfg, sizeof(ecfg), &s_enc);

    esp_opus_dec_cfg_t dcfg = ESP_OPUS_DEC_CONFIG_DEFAULT();
    dcfg.sample_rate = HUX_OPUS_SAMPLE_RATE;
    dcfg.channel = 1;
    dcfg.frame_duration = ESP_OPUS_DEC_FRAME_DURATION_60_MS; /* the contract: 60 ms packets */
    dcfg.self_delimited = false;
    esp_audio_err_t derr = esp_opus_dec_open(&dcfg, sizeof(dcfg), &s_dec);

    int in_size = 0, out_size = 0;
    if (eerr == ESP_AUDIO_ERR_OK) {
        esp_opus_enc_get_frame_size(s_enc, &in_size, &out_size);
    }
    bool ok = eerr == ESP_AUDIO_ERR_OK && derr == ESP_AUDIO_ERR_OK &&
              in_size == HUX_OPUS_FRAME_SAMPLES * 2 && out_size <= HUX_OPUS_MAX_PACKET;
    if (ok) {
        s_ctl_lock = xSemaphoreCreateMutex();
        s_rx_q = xQueueCreateWithCaps(RX_QUEUE_DEPTH, sizeof(rx_item_t), MALLOC_CAP_SPIRAM);
        ok = s_ctl_lock != NULL && s_rx_q != NULL &&
             xTaskCreateWithCaps(decode_task, "hux_opus_dec", DEC_TASK_STACK, NULL, DEC_TASK_PRIO,
                                 &s_dec_task, MALLOC_CAP_SPIRAM) == pdPASS;
        if (!ok && s_rx_q != NULL) {
            vQueueDeleteWithCaps(s_rx_q); /* no reader: nothing may queue into it */
            s_rx_q = NULL;
        }
    }
    ESP_LOG_LEVEL(ok ? ESP_LOG_INFO : ESP_LOG_ERROR, TAG,
                  "opus.init ok=%d enc=%d dec=%d rate=%d frame_ms=%d enc_in=%d enc_out=%d "
                  "bitrate=%d complexity=%d",
                  ok, (int)eerr, (int)derr, HUX_OPUS_SAMPLE_RATE, HUX_OPUS_FRAME_MS, in_size,
                  out_size, ENC_BITRATE, ENC_COMPLEXITY);
    return ok;
}

bool hux_opus_ready(void) {
    return s_rx_q != NULL && s_dec_task != NULL;
}

void hux_opus_rx_packet(const uint8_t *data, size_t len) {
    if (s_rx_q == NULL || len == 0) {
        return;
    }
    if (len > sizeof(s_ws_item.data)) {
        ESP_LOGW(TAG, "opus.rx_too_big len=%u", (unsigned)len);
        return;
    }
    s_ws_item.kind = RX_PACKET;
    s_ws_item.len = (uint16_t)len;
    memcpy(s_ws_item.data, data, len);
    if (xQueueSend(s_rx_q, &s_ws_item, pdMS_TO_TICKS(RX_WAIT_MS)) != pdTRUE) {
        ESP_LOGW(TAG, "opus.rx_dropped len=%u (decoder stuck %d ms)", (unsigned)len, RX_WAIT_MS);
    }
}

void hux_opus_rx_clear(void) {
    if (s_rx_q == NULL) {
        return;
    }
    s_ws_item.kind = RX_CLEAR;
    s_ws_item.len = 0;
    if (xQueueSend(s_rx_q, &s_ws_item, pdMS_TO_TICKS(RX_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "opus.clear_dropped (decoder stuck)");
    }
}

/* Queue an item from any task, staged in s_ctl_item under s_ctl_lock (the
 * WebSocket task stages in s_ws_item, so the two never trample). */
static bool queue_item(rx_kind_t kind, const uint8_t *data, size_t len, bool to_front) {
    if (s_rx_q == NULL || len > sizeof(s_ctl_item.data) ||
        xSemaphoreTake(s_ctl_lock, pdMS_TO_TICKS(RX_WAIT_MS)) != pdTRUE) {
        return false;
    }
    s_ctl_item.kind = (uint16_t)kind;
    s_ctl_item.len = (uint16_t)len;
    if (len > 0) {
        memcpy(s_ctl_item.data, data, len);
    }
    BaseType_t ok = to_front ? xQueueSendToFront(s_rx_q, &s_ctl_item, 0)
                             : xQueueSend(s_rx_q, &s_ctl_item, pdMS_TO_TICKS(RX_WAIT_MS));
    xSemaphoreGive(s_ctl_lock);
    if (ok != pdTRUE) {
        ESP_LOGE(TAG, "opus.ctl_dropped kind=%d (decoder stuck)", (int)kind);
    }
    return ok == pdTRUE;
}

static void send_ctl(rx_kind_t kind, bool to_front) {
    (void)queue_item(kind, NULL, 0, to_front);
}

void hux_opus_rx_fresh(void) {
    send_ctl(RX_RESET, false);
}

bool hux_opus_play_packet(const uint8_t *data, size_t len) {
    return len > 0 && queue_item(RX_PACKET, data, len, false);
}

void hux_opus_rx_unhold(void) {
    send_ctl(RX_UNHOLD, false);
}

void hux_opus_rx_reset(void) {
    if (s_rx_q == NULL) {
        return;
    }
    /* Markers count too: rare, and over rather than under is the safe side
     * (the server repeats a narration it can't prove was heard). */
    uint32_t waiting = (uint32_t)uxQueueMessagesWaiting(s_rx_q);
    xQueueReset(s_rx_q);
    atomic_fetch_add(&s_rx_dropped_ms, waiting * HUX_OPUS_FRAME_MS);
    send_ctl(RX_RESET, true);
}

uint32_t hux_opus_rx_waiting(void) {
    return s_rx_q != NULL ? (uint32_t)uxQueueMessagesWaiting(s_rx_q) : 0;
}

uint32_t hux_opus_rx_dropped_ms(void) {
    return atomic_load(&s_rx_dropped_ms);
}

uint32_t hux_opus_dec_errors(void) {
    return atomic_load(&s_dec_errors);
}

uint32_t hux_opus_dec_stack_free(void) {
    return s_dec_task != NULL ? (uint32_t)uxTaskGetStackHighWaterMark(s_dec_task) : 0;
}

int hux_opus_encode(const int16_t *pcm, uint8_t *out, size_t cap) {
    if (s_enc == NULL) {
        return -1;
    }
    esp_audio_enc_in_frame_t in = {.buffer = (uint8_t *)pcm, .len = HUX_OPUS_FRAME_SAMPLES * 2};
    esp_audio_enc_out_frame_t o = {.buffer = out, .len = (uint32_t)cap};
    int64_t t0 = esp_timer_get_time();
    esp_audio_err_t err = esp_opus_enc_process(s_enc, &in, &o);
    atomic_fetch_add(&s_enc_us, (uint32_t)(esp_timer_get_time() - t0));
    atomic_fetch_add(&s_enc_n, 1);
    if (err != ESP_AUDIO_ERR_OK) {
        ESP_LOGW(TAG, "opus.encode_failed err=%d", (int)err);
        return -1;
    }
    return (int)o.encoded_bytes;
}

void hux_opus_encode_reset(void) {
    if (s_enc != NULL) {
        esp_opus_enc_reset(s_enc);
    }
}

uint32_t hux_opus_take_enc_us(void) {
    uint32_t n = atomic_exchange(&s_enc_n, 0), us = atomic_exchange(&s_enc_us, 0);
    return n ? us / n : 0;
}

void hux_opus_rx_progress(uint32_t *done, uint32_t *pending) {
    *done = atomic_load(&s_rx_taken);
    *pending = s_rx_q != NULL ? (uint32_t)uxQueueMessagesWaiting(s_rx_q) : 0;
}

uint32_t hux_opus_take_dec_us(void) {
    uint32_t n = atomic_exchange(&s_dec_n, 0), us = atomic_exchange(&s_dec_us, 0);
    return n ? us / n : 0;
}
