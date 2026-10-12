#include "hux_proto.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"

typedef struct {
    const char *wire;
    hux_msg_kind_t kind;
} kind_entry_t;

/* Ordered to match the declaration in hux_proto.h for auditability. */
static const kind_entry_t KIND_TABLE[] = {
    {"hello", HUX_MSG_HELLO},
    {"state", HUX_MSG_STATE},
    {"status", HUX_MSG_STATUS},
    {"transcript", HUX_MSG_TRANSCRIPT},
    {"audio", HUX_MSG_AUDIO},
    {"model_speaking", HUX_MSG_MODEL_SPEAKING},
    {"set_volume", HUX_MSG_SET_VOLUME},
    {"input_mode", HUX_MSG_INPUT_MODE},
    {"claim_started", HUX_MSG_CLAIM_STARTED},
    {"claim_ended", HUX_MSG_CLAIM_ENDED},
    {"stream_started", HUX_MSG_STREAM_STARTED},
    {"stream_ended", HUX_MSG_STREAM_ENDED},
    {"dev_event", HUX_MSG_DEV_EVENT},
    {"audio_clear", HUX_MSG_AUDIO_CLEAR},
    {"server_event", HUX_MSG_SERVER_EVENT},
};

static hux_msg_kind_t kind_from_wire(const char *wire) {
    if (wire == NULL) {
        return HUX_MSG_UNKNOWN;
    }
    for (size_t i = 0; i < sizeof(KIND_TABLE) / sizeof(KIND_TABLE[0]); i++) {
        if (strcmp(wire, KIND_TABLE[i].wire) == 0) {
            return KIND_TABLE[i].kind;
        }
    }
    return HUX_MSG_UNKNOWN;
}

const char *hux_proto_kind_name(hux_msg_kind_t kind) {
    for (size_t i = 0; i < sizeof(KIND_TABLE) / sizeof(KIND_TABLE[0]); i++) {
        if (KIND_TABLE[i].kind == kind) {
            return KIND_TABLE[i].wire;
        }
    }
    return "unknown";
}

bool hux_proto_parse(const char *json, size_t len, hux_msg_t *out) {
    if (out == NULL) {
        return false;
    }
    memset(out, 0, sizeof(*out));

    cJSON *root = cJSON_ParseWithLength(json, len);
    if (root == NULL || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return false;
    }

    const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
    if (!cJSON_IsString(type) || type->valuestring == NULL) {
        cJSON_Delete(root);
        return false;
    }

    out->kind = kind_from_wire(type->valuestring);

    switch (out->kind) {
        case HUX_MSG_HELLO: {
            const cJSON *protocol = cJSON_GetObjectItemCaseSensitive(root, "protocol");
            out->as.hello.protocol = cJSON_IsNumber(protocol) ? (int)protocol->valuedouble : -1;
            const cJSON *ready = cJSON_GetObjectItemCaseSensitive(root, "session_ready");
            out->as.hello.session_ready = !cJSON_IsFalse(ready);
            const cJSON *ap = cJSON_GetObjectItemCaseSensitive(root, "audio_params");
            const cJSON *fmt = cJSON_GetObjectItemCaseSensitive(ap, "format");
            const cJSON *sr = cJSON_GetObjectItemCaseSensitive(ap, "sample_rate");
            const cJSON *ch = cJSON_GetObjectItemCaseSensitive(ap, "channels");
            const cJSON *fd = cJSON_GetObjectItemCaseSensitive(ap, "frame_duration");
            out->as.hello.audio_opus = cJSON_IsString(fmt) && strcmp(fmt->valuestring, "opus") == 0 &&
                                       cJSON_IsNumber(sr) && sr->valuedouble == 24000 &&
                                       cJSON_IsNumber(ch) && ch->valuedouble == 1 &&
                                       cJSON_IsNumber(fd) && fd->valuedouble == 60;
            break;
        }
        case HUX_MSG_STATE: {
            const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "value");
            const char *v = cJSON_IsString(value) ? value->valuestring : NULL;
            out->as.state.value = v == NULL                     ? HUX_SERVER_STATE_UNKNOWN
                                  : strcmp(v, "IDLE") == 0       ? HUX_SERVER_STATE_IDLE
                                  : strcmp(v, "CONNECTING") == 0 ? HUX_SERVER_STATE_CONNECTING
                                  : strcmp(v, "CONVERSING") == 0 ? HUX_SERVER_STATE_CONVERSING
                                                                 : HUX_SERVER_STATE_UNKNOWN;
            break;
        }
        case HUX_MSG_SET_VOLUME: {
            const cJSON *level = cJSON_GetObjectItemCaseSensitive(root, "level");
            if (cJSON_IsNumber(level)) {
                double l = level->valuedouble;
                out->as.set_volume.level = l < 0 ? 0 : l > 100 ? 100 : (int)(l + 0.5);
            } else {
                out->as.set_volume.level = -1;
            }
            break;
        }
        case HUX_MSG_MODEL_SPEAKING: {
            const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "value");
            out->as.model_speaking.known = cJSON_IsBool(value);
            out->as.model_speaking.value = cJSON_IsTrue(value);
            break;
        }
        case HUX_MSG_SERVER_EVENT: {
            hux_msg_server_event_t *se = &out->as.server_event;
            const cJSON *ev = cJSON_GetObjectItemCaseSensitive(root, "event");
            const cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
            if (cJSON_IsString(ev)) {
                snprintf(se->name, sizeof(se->name), "%s", ev->valuestring);
                se->event = strcmp(ev->valuestring, "dev.cmd") == 0           ? HUX_SERVER_EV_DEV_CMD
                            : strcmp(ev->valuestring, "firmware.update") == 0 ? HUX_SERVER_EV_FIRMWARE_UPDATE
                            : strcmp(ev->valuestring, "ota.accept") == 0      ? HUX_SERVER_EV_OTA_ACCEPT
                                                                              : HUX_SERVER_EV_UNKNOWN;
            }
            if (!cJSON_IsObject(data)) {
                break;
            }
            const cJSON *id = cJSON_GetObjectItemCaseSensitive(data, "id");
            if (cJSON_IsString(id)) {
                snprintf(se->id, sizeof(se->id), "%s", id->valuestring);
            } else if (cJSON_IsNumber(id)) {
                snprintf(se->id, sizeof(se->id), "%.0f", id->valuedouble);
            }
            const cJSON *line = cJSON_GetObjectItemCaseSensitive(data, "line");
            if (cJSON_IsString(line)) {
                int n = snprintf(se->line, sizeof(se->line), "%s", line->valuestring);
                se->line_truncated = n >= (int)sizeof(se->line);
            }
            const cJSON *version = cJSON_GetObjectItemCaseSensitive(data, "version");
            if (cJSON_IsString(version)) {
                snprintf(se->version, sizeof(se->version), "%s", version->valuestring);
            }
            const cJSON *url = cJSON_GetObjectItemCaseSensitive(data, "url");
            if (cJSON_IsString(url)) {
                snprintf(se->url, sizeof(se->url), "%s", url->valuestring);
            }
            se->force = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(data, "force"));
            break;
        }
        case HUX_MSG_INPUT_MODE: {
            const cJSON *mode = cJSON_GetObjectItemCaseSensitive(root, "mode");
            const char *m = cJSON_IsString(mode) ? mode->valuestring : NULL;
            bool continuous = m != NULL && strcmp(m, "skill_continuous") == 0;
            out->as.input_mode.continuous = continuous;
            out->as.input_mode.known = continuous || (m != NULL && strcmp(m, "assistant_ptt") == 0);
            break;
        }
        default:
            /* Kinds without populated payload yet — the caller logs by
             * name and ignores. Adding a field is additive: extend the
             * header union, add a case here, nothing else moves. */
            break;
    }

    cJSON_Delete(root);
    return true;
}
