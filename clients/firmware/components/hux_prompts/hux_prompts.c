#include "hux_prompts.h"

#include <string.h>

#include "esp_log.h"
#include "hux_opus.h"
#include "hux_prompt_parse.h"

static const char *TAG = "hux_prompts";

/* EMBED_FILES in CMakeLists.txt. */
extern const uint8_t offline_start[] asm("_binary_offline_hxp_start");
extern const uint8_t offline_end[] asm("_binary_offline_hxp_end");
extern const uint8_t battery_empty_start[] asm("_binary_battery_empty_hxp_start");
extern const uint8_t battery_empty_end[] asm("_binary_battery_empty_hxp_end");
extern const uint8_t startup_start[] asm("_binary_startup_hxp_start");
extern const uint8_t startup_end[] asm("_binary_startup_hxp_end");
extern const uint8_t searching_start[] asm("_binary_searching_hxp_start");
extern const uint8_t searching_end[] asm("_binary_searching_hxp_end");
extern const uint8_t connected_start[] asm("_binary_connected_hxp_start");
extern const uint8_t connected_end[] asm("_binary_connected_hxp_end");
extern const uint8_t lost_start[] asm("_binary_lost_hxp_start");
extern const uint8_t lost_end[] asm("_binary_lost_hxp_end");

static const struct {
    const char *name;
    const uint8_t *start;
    const uint8_t *end;
} s_prompts[HUX_PROMPT_COUNT] = {
    [HUX_PROMPT_OFFLINE] = {"offline", offline_start, offline_end},
    [HUX_PROMPT_BATTERY_EMPTY] = {"battery_empty", battery_empty_start, battery_empty_end},
    [HUX_PROMPT_STARTUP] = {"startup", startup_start, startup_end},
    [HUX_PROMPT_SEARCHING] = {"searching", searching_start, searching_end},
    [HUX_PROMPT_CONNECTED] = {"connected", connected_start, connected_end},
    [HUX_PROMPT_LOST] = {"lost", lost_start, lost_end},
};

static int packets_of(hux_prompt_t id) {
    if ((unsigned)id >= HUX_PROMPT_COUNT) {
        return -1;
    }
    return hux_prompt_count(s_prompts[id].start, (size_t)(s_prompts[id].end - s_prompts[id].start));
}

bool hux_prompt_play(hux_prompt_t id) {
    int packets = packets_of(id);
    if (packets <= 0) {
        ESP_LOGE(TAG, "prompt.bad id=%d packets=%d", (int)id, packets);
        return false;
    }
    if (!hux_opus_ready()) {
        ESP_LOGW(TAG, "prompt.skipped name=%s why=no_decoder", s_prompts[id].name);
        return false;
    }
    if (!hux_opus_clip_begin()) {
        ESP_LOGW(TAG, "prompt.skipped name=%s why=queue_busy", s_prompts[id].name);
        return false;
    }
    hux_prompt_iter_t it;
    hux_prompt_iter_init(&it, s_prompts[id].start, (size_t)(s_prompts[id].end - s_prompts[id].start));
    /* Start clean: the decoder may hold the state of the server's last
     * packet; and leave it clean for the next stream. */
    hux_opus_rx_fresh();
    const uint8_t *pkt;
    size_t len;
    int queued = 0;
    while (hux_prompt_iter_next(&it, &pkt, &len) == 1) {
        if (!hux_opus_play_packet(pkt, len)) {
            break;
        }
        queued++;
    }
    hux_opus_rx_fresh();
    hux_opus_clip_end();
    /* The searching tick repeats every 3 s for minutes: its caller logs a
     * count instead, so an outage's log queue keeps room (F-0033). */
    esp_log_level_t ok_level = id == HUX_PROMPT_SEARCHING ? ESP_LOG_DEBUG : ESP_LOG_INFO;
    ESP_LOG_LEVEL(queued == packets ? ok_level : ESP_LOG_WARN, TAG,
                  "prompt.play name=%s ms=%d queued=%d of %d", s_prompts[id].name,
                  packets * 60, queued, packets);
    return queued == packets;
}

uint32_t hux_prompt_ms(hux_prompt_t id) {
    int packets = packets_of(id);
    return packets > 0 ? (uint32_t)packets * 60 : 0;
}

const char *hux_prompt_name(hux_prompt_t id) {
    return (unsigned)id < HUX_PROMPT_COUNT ? s_prompts[id].name : NULL;
}

int hux_prompt_find(const char *name) {
    for (int i = 0; i < HUX_PROMPT_COUNT; i++) {
        if (name != NULL && strcmp(name, s_prompts[i].name) == 0) {
            return i;
        }
    }
    return -1;
}
