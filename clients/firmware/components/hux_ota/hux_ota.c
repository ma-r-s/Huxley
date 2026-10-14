#include "hux_ota.h"

#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hux_audio.h"
#include "hux_link.h"
#include "hux_net.h"

static const char *TAG = "hux_ota";

#define OTA_TASK_STACK 8192
#define OTA_TASK_PRIO  3  /* below audio and the app */

static _Atomic bool s_busy = false;
static char s_url[256];
static bool s_force = false;
/* A pull from the server (firmware.update, F-0028): the version it offered,
 * the "Bearer <token>" for the download, where firmware.status goes. Empty
 * for the developer door's push. */
static char s_expect[48];
static char s_bearer[96];
static hux_ota_status_fn s_status = NULL;
static hux_ota_selftest_fn s_selftest = NULL;

void hux_ota_set_status_sink(hux_ota_status_fn fn) {
    s_status = fn;
}

void hux_ota_set_selftest(hux_ota_selftest_fn fn) {
    s_selftest = fn;
}

static void status(const char *version, const char *phase, const char *error) {
    ESP_LOGW(TAG, "ota.status version=%s phase=%s error=%s", version, phase,
             error != NULL ? error : "-");
    if (s_status != NULL) {
        s_status(version, phase, error);
    }
}

static esp_err_t add_auth(esp_http_client_handle_t client) {
    return s_bearer[0] != '\0' ? esp_http_client_set_header(client, "Authorization", s_bearer)
                               : ESP_OK;
}

static const char *state_name(esp_ota_img_states_t s) {
    switch (s) {
        case ESP_OTA_IMG_NEW:            return "new";
        case ESP_OTA_IMG_PENDING_VERIFY: return "pending";
        case ESP_OTA_IMG_VALID:          return "valid";
        case ESP_OTA_IMG_INVALID:        return "invalid";
        case ESP_OTA_IMG_ABORTED:        return "aborted";
        default:                         return "undefined";
    }
}

const char *hux_ota_running_slot(void) {
    const esp_partition_t *run = esp_ota_get_running_partition();
    return run != NULL ? run->label : "?";
}

bool hux_ota_busy(void) {
    return atomic_load(&s_busy);
}

const char *hux_ota_running_state(void) {
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) != ESP_OK) {
        return "undefined"; /* flashed over USB: no otadata entry, nothing to verify */
    }
    return state_name(st);
}

/* advanced_https_ota's check, plus the project name: an image for another
 * board (Fleet's, say) must never land in the walkie. */
static esp_err_t validate(const esp_app_desc_t *incoming) {
    const esp_app_desc_t *running = esp_app_get_description();
    if (strncmp(incoming->project_name, running->project_name, sizeof(incoming->project_name)) != 0) {
        ESP_LOGE(TAG, "ota.refused project=%s (this is %s)", incoming->project_name,
                 running->project_name);
        return ESP_FAIL;
    }
    if (s_expect[0] != '\0' && strncmp(incoming->version, s_expect, sizeof(incoming->version)) != 0) {
        ESP_LOGE(TAG, "ota.refused version=%s expected=%s", incoming->version, s_expect);
        return ESP_FAIL;
    }
    if (!s_force && strncmp(incoming->version, running->version, sizeof(incoming->version)) == 0) {
        ESP_LOGW(TAG, "ota.refused version=%s is already running (ota <url> force)",
                 incoming->version);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "ota.image version=%s (running %s)", incoming->version, running->version);
    return ESP_OK;
}

static void ota_task(void *unused) {
    (void)unused;
    int64_t t0 = esp_timer_get_time();
    const char *v = s_expect[0] != '\0' ? s_expect : "?";
    ESP_LOGW(TAG, "ota.start url=%s from_slot=%s auth=%d", s_url, hux_ota_running_slot(),
             s_bearer[0] != '\0');
    status(v, "downloading", NULL);
    esp_http_client_config_t http = {
        .url = s_url,
        .timeout_ms = 10000,
        .keep_alive_enable = true,
        .crt_bundle_attach = esp_crt_bundle_attach, /* https through the tunnel */
    };
    esp_https_ota_config_t cfg = {.http_config = &http, .http_client_init_cb = add_auth};
    esp_https_ota_handle_t h = NULL;
    esp_err_t err = esp_https_ota_begin(&cfg, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ota.failed step=begin err=%s", esp_err_to_name(err));
        status(v, "failed", "begin");
        goto done;
    }
    esp_app_desc_t desc;
    err = esp_https_ota_get_img_desc(h, &desc);
    if (err != ESP_OK || validate(&desc) != ESP_OK) {
        esp_https_ota_abort(h);
        status(v, "failed", "image");
        goto done;
    }
    size_t last_kb = 0;
    while ((err = esp_https_ota_perform(h)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
        size_t kb = (size_t)esp_https_ota_get_image_len_read(h) / 1024;
        if (kb >= last_kb + 256) {
            ESP_LOGI(TAG, "ota.progress kb=%u", (unsigned)kb);
            last_kb = kb;
        }
    }
    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(h)) {
        ESP_LOGE(TAG, "ota.failed step=download err=%s", esp_err_to_name(err));
        esp_https_ota_abort(h);
        status(v, "failed", "download");
        goto done;
    }
    status(v, "verifying", NULL);
    err = esp_https_ota_finish(h); /* checks the image, sets it to boot next */
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ota.failed step=finish err=%s", esp_err_to_name(err));
        status(v, "failed", "verify");
        goto done;
    }
    ESP_LOGW(TAG, "ota.done version=%s ms=%u: restarting into it", desc.version,
             (unsigned)((esp_timer_get_time() - t0) / 1000));
    status(desc.version, "rebooting", NULL);
    vTaskDelay(pdMS_TO_TICKS(500)); /* let the lines reach the server */
    esp_restart();

done:
    atomic_store(&s_busy, false);
    vTaskDelete(NULL);
}

static bool start(const char *url, bool force);

bool hux_ota_start(const char *url, bool force) {
    s_expect[0] = '\0'; /* the developer door's push: no offer, no token */
    s_bearer[0] = '\0';
    return start(url, force);
}

static void set_bearer(const char *token) {
    if (token != NULL && token[0] != '\0') {
        snprintf(s_bearer, sizeof(s_bearer), "Bearer %s", token);
    } else {
        s_bearer[0] = '\0';
    }
}

bool hux_ota_pull(const char *url, const char *version, bool force, const char *token) {
    if (atomic_load(&s_busy)) {
        ESP_LOGW(TAG, "ota.pull_refused why=busy version=%s", version);
        return false; /* the running update reports its own status */
    }
    snprintf(s_expect, sizeof(s_expect), "%s", version != NULL ? version : "");
    set_bearer(token);
    if (!start(url, force)) {
        status(s_expect[0] != '\0' ? s_expect : "?", "failed", "busy");
        return false;
    }
    return true;
}

static bool start(const char *url, bool force) {
    /* An update ends in a restart: never in the middle of a reply or a
     * question (the user would hear it cut off) unless forced. */
    if (!force && (hux_audio_spk_ring_used() > 0 || hux_audio_mic_is_running())) {
        ESP_LOGW(TAG, "ota.not_started (in use: playing or listening; ota <url> force)");
        return false;
    }
    if (url == NULL || url[0] == '\0' || atomic_exchange(&s_busy, true)) {
        ESP_LOGW(TAG, "ota.not_started (%s)", url == NULL || url[0] == '\0' ? "no url" : "busy");
        return false;
    }
    strncpy(s_url, url, sizeof(s_url) - 1);
    s_url[sizeof(s_url) - 1] = '\0';
    s_force = force;
    if (xTaskCreate(ota_task, "hux_ota", OTA_TASK_STACK, NULL, OTA_TASK_PRIO, NULL) != pdPASS) {
        atomic_store(&s_busy, false);
        ESP_LOGE(TAG, "ota.not_started (no memory for the task)");
        return false;
    }
    return true;
}

static char s_last_invalid[64];

const char *hux_ota_last_invalid(void) {
    return s_last_invalid;
}

bool hux_ota_confirm(void) {
    if (strcmp(hux_ota_running_state(), "pending") != 0) {
        ESP_LOGW(TAG, "ota.confirm_refused state=%s (nothing pending)", hux_ota_running_state());
        return false;
    }
    int64_t up_s = esp_timer_get_time() / 1000000;
    if (up_s < HUX_OTA_SELFTEST_S) {
        ESP_LOGW(TAG, "ota.confirm_refused uptime_s=%d (needs %d)", (int)up_s, HUX_OTA_SELFTEST_S);
        return false;
    }
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    ESP_LOGW(TAG, "ota.confirmed slot=%s uptime_s=%d err=%s", hux_ota_running_slot(), (int)up_s,
             esp_err_to_name(err));
    return err == ESP_OK;
}

bool hux_ota_rollback(void) {
    if (strcmp(hux_ota_running_state(), "pending") == 0) {
        ESP_LOGW(TAG, "ota.rollback from pending slot=%s", hux_ota_running_slot());
        vTaskDelay(pdMS_TO_TICKS(300)); /* let the line out */
        esp_ota_mark_app_invalid_rollback_and_reboot(); /* returns only on failure */
        ESP_LOGE(TAG, "ota.rollback_failed (no valid image in the other slot)");
        return false;
    }
    const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);
    esp_app_desc_t desc;
    esp_ota_img_states_t st;
    bool usable = other != NULL && esp_ota_get_partition_description(other, &desc) == ESP_OK &&
                  (esp_ota_get_state_partition(other, &st) != ESP_OK || st == ESP_OTA_IMG_VALID);
    if (!usable) {
        ESP_LOGE(TAG, "ota.rollback_refused (the other slot has no valid image)");
        return false;
    }
    esp_err_t err = esp_ota_set_boot_partition(other);
    ESP_LOGW(TAG, "ota.rollback to slot=%s version=%s err=%s", other->label, desc.version,
             esp_err_to_name(err));
    if (err != ESP_OK) {
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
    return true;
}

void hux_ota_boot_check(void) {
    const esp_app_desc_t *app = esp_app_get_description();
    const char *state = hux_ota_running_state();
    ESP_LOGI(TAG, "ota.boot slot=%s state=%s version=%s", hux_ota_running_slot(), state,
             app->version);
    const esp_partition_t *bad = esp_ota_get_last_invalid_partition();
    esp_app_desc_t bad_desc;
    if (bad && esp_ota_get_partition_description(bad, &bad_desc) == ESP_OK) {
        snprintf(s_last_invalid, sizeof(s_last_invalid), "%s %s", bad->label, bad_desc.version);
        ESP_LOGW(TAG, "ota.last_invalid %s (rolled back from it)", s_last_invalid);
    }
    if (strcmp(state, "pending") == 0) {
        ESP_LOGW(TAG, "ota.pending: kept only after `ota confirm` (tools/dev.py flash, after %d s); "
                      "a restart before that boots the previous image",
                 HUX_OTA_SELFTEST_S);
    }
}

/* --- Keeping a pulled image (F-0028, the accept flow agreed with the
 * server session): after its first hello a pending image runs its silent
 * self-test, then GETs the manifest with the update client's settings;
 * the server closes the connection once (1012) and, on the next hello,
 * sends ota.accept. The image keeps itself only if all of that held. */
static struct {
    bool started;
    bool selftest_done;
    bool selftest_ok;
    const char *selftest_why;
    bool manifest_ok;
    uint32_t closes_at; /* at the first manifest that matched: never moved */
    uint32_t connects_at;
} s_verify;
/* Each hello while pending: the self-test status again and the manifest
 * fetched again, so a server restart costs one round, not a reboot (the
 * server session's contract). */
static SemaphoreHandle_t s_verify_hello;

#define VERIFY_AFTER_S 20 /* the supervisor has watched every part this long */

static bool fetch_manifest(const char *url, const char *version, const char *elf) {
    esp_http_client_config_t http = {
        .url = url,
        .timeout_ms = 10000,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t c = esp_http_client_init(&http);
    if (c == NULL) {
        return false;
    }
    add_auth(c);
    char body[512] = {0};
    int status_code = -1;
    int len = 0;
    if (esp_http_client_open(c, 0) == ESP_OK) {
        esp_http_client_fetch_headers(c);
        status_code = esp_http_client_get_status_code(c);
        len = esp_http_client_read_response(c, body, sizeof(body) - 1);
    }
    esp_http_client_cleanup(c);
    bool match = false;
    cJSON *root = len > 0 ? cJSON_Parse(body) : NULL;
    if (root != NULL) {
        const cJSON *v = cJSON_GetObjectItemCaseSensitive(root, "version");
        const cJSON *e = cJSON_GetObjectItemCaseSensitive(root, "elf_sha");
        match = cJSON_IsString(v) && strcmp(v->valuestring, version) == 0 && cJSON_IsString(e) &&
                strcmp(e->valuestring, elf) == 0;
        cJSON_Delete(root);
    }
    ESP_LOGW(TAG, "ota.manifest url=%s status=%d bytes=%d match=%d", url, status_code, len, match);
    return status_code == 200 && match;
}

static void verify_task(void *unused) {
    (void)unused;
    const char *version = esp_app_get_description()->version;
    while (esp_timer_get_time() < (int64_t)VERIFY_AFTER_S * 1000000) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    const char *why = NULL;
    s_verify.selftest_ok = s_selftest == NULL || s_selftest(&why);
    s_verify.selftest_why = s_verify.selftest_ok ? NULL : (why != NULL ? why : "unknown");
    s_verify.selftest_done = true;
    char elf[10] = {0};
    esp_app_get_elf_sha256(elf, sizeof(elf));
    /* One round per hello (the first hello gave the semaphore already),
     * for as long as the image is pending. */
    while (strcmp(hux_ota_running_state(), "pending") == 0) {
        status(version, "selftest", s_verify.selftest_why);
        if (s_verify.selftest_ok) {
            char base[160];
            char url[200];
            hux_net_server_uri(base, sizeof(base));
            bool ok = hux_link_http_url(base, "/firmware/manifest.json", url, sizeof(url)) &&
                      fetch_manifest(url, version, elf);
            if (ok && !s_verify.manifest_ok) {
                /* The reconnect the accept wants comes after this point;
                 * later rounds don't move it (they'd race the accept). */
                s_verify.closes_at = hux_net_server_closes();
                s_verify.connects_at = hux_net_connects();
            }
            s_verify.manifest_ok = s_verify.manifest_ok || ok;
        }
        ESP_LOGW(TAG, "ota.verify selftest=%d manifest=%d", s_verify.selftest_ok,
                 s_verify.manifest_ok);
        xSemaphoreTake(s_verify_hello, portMAX_DELAY);
    }
    vTaskDelete(NULL);
}

void hux_ota_after_hello(const char *token) {
    if (strcmp(hux_ota_running_state(), "pending") != 0) {
        return;
    }
    if (!atomic_load(&s_busy)) {
        /* The manifest GET carries the token too: a pending image booted
         * fresh, no pull of this boot set it (the server answered 401,
         * 2026-10-12). */
        set_bearer(token);
    }
    if (s_verify.started) {
        xSemaphoreGive(s_verify_hello); /* the next round, once the self-test ran */
        return;
    }
    if (s_verify_hello == NULL) {
        s_verify_hello = xSemaphoreCreateBinary();
    }
    s_verify.started = true;
    if (s_verify_hello == NULL ||
        xTaskCreate(verify_task, "hux_ota_verify", 6144, NULL, 3, NULL) != pdPASS) {
        s_verify.started = false;
        ESP_LOGE(TAG, "ota.verify task_create_failed: retried at the next hello");
    }
}

void hux_ota_accept(const char *version) {
    const char *running = esp_app_get_description()->version;
    const char *state = hux_ota_running_state();
    const char *why = NULL;
    if (strcmp(version, running) != 0) {
        why = "version";
    } else if (strcmp(state, "valid") == 0) {
        status(running, "accepted", NULL); /* already kept: say so again */
        return;
    } else if (strcmp(state, "pending") != 0) {
        why = "not_pending";
    } else if (!s_verify.selftest_ok) {
        why = "selftest";
    } else if (!s_verify.manifest_ok) {
        why = "manifest";
    } else if (!(hux_net_server_closes() > s_verify.closes_at &&
                 hux_net_connects() > s_verify.connects_at)) {
        why = "no_reconnect";
    }
    if (why != NULL) {
        ESP_LOGW(TAG, "ota.accept_refused reason=%s offered=%s running=%s state=%s", why, version,
                 running, state);
        status(running, "failed", why);
        return;
    }
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    ESP_LOGW(TAG, "ota.accepted slot=%s version=%s err=%s", hux_ota_running_slot(), running,
             esp_err_to_name(err));
    if (s_verify_hello != NULL) {
        xSemaphoreGive(s_verify_hello); /* the verify task sees "valid" and ends */
    }
    status(running, err == ESP_OK ? "accepted" : "failed", err == ESP_OK ? NULL : "mark_valid");
}
