#include "hux_proto.h"

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
