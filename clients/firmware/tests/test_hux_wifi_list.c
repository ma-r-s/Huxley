/**
 * Host unit tests for `hux_wifi_list`: which known network the walkie joins
 * from what a scan heard (F-0027's Wi-Fi list), and the hex that carries
 * names with spaces over the console.
 */
#include <stdio.h>
#include <string.h>

#include "test_helpers.h"

#include "hux_wifi_list.h"

/* Home (compiled), a phone hotspot (compiled), the place's network (saved). */
static const hux_wifi_net_t KNOWN[] = {
    {.ssid = "Casa", .pass = "x", .compiled = true},
    {.ssid = "Telefono", .pass = "x", .compiled = true},
    {.ssid = "Hogar 2G", .pass = "x", .compiled = false},
};
#define N 3

static void test_heard_good_first_then_signal(void) {
    hux_wifi_heard_t heard[] = {{"Casa", -40}, {"Hogar 2G", -70}, {"Vecino", -30}};
    int order[8];
    int n = hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 3, order, 8);
    HX_ASSERT_EQ(n, 2, "only the known networks heard");
    HX_ASSERT_EQ(order[0], 2, "good first, though weaker");
    HX_ASSERT_EQ(order[1], 0, "then the other heard");
    n = hux_wifi_rank(KNOWN, N, "", heard, 3, order, 8);
    HX_ASSERT_EQ(order[0], 0, "no good: the strongest");
}

static void test_good_not_heard(void) {
    hux_wifi_heard_t heard[] = {{"Telefono", -60}, {"Casa", -50}};
    int order[8];
    int n = hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 2, order, 8);
    HX_ASSERT_EQ(n, 2, "never a network not heard when others are");
    HX_ASSERT_EQ(order[0], 0, "strongest first");
    HX_ASSERT_EQ(order[1], 1, "then the hotspot");
}

static void test_nothing_heard(void) {
    hux_wifi_heard_t heard[] = {{"Vecino", -30}};
    int order[8];
    HX_ASSERT_EQ(hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 1, order, 8), 0,
                 "none heard by name: stay (never a network not heard)");
    HX_ASSERT_EQ(hux_wifi_rank(KNOWN, N, "", NULL, 0, order, 8), 0, "a failed scan: stay");
}

static void test_hidden_network_never_counts(void) {
    hux_wifi_heard_t heard[] = {{"Telefono", -60}, {"", -55}};
    int order[8];
    int n = hux_wifi_rank(KNOWN, N, "Hogar 2G", heard, 2, order, 8);
    HX_ASSERT_EQ(n, 1, "only the one heard by name");
    HX_ASSERT_EQ(order[0], 1, "the hotspot");
}

static void test_cap(void) {
    hux_wifi_heard_t heard[] = {{"Casa", -40}, {"Hogar 2G", -70}, {"Telefono", -50}};
    int order[1];
    HX_ASSERT_EQ(hux_wifi_rank(KNOWN, N, "", heard, 3, order, 1), 1, "never more than cap");
}

static void test_hex(void) {
    char out[33];
    HX_ASSERT_EQ(hux_hex_decode("4361736120417a756c", out, sizeof(out)), 9, "a name");
    HX_ASSERT_TRUE(strcmp(out, "Casa Azul") == 0, "with its space");
    HX_ASSERT_EQ(hux_hex_decode("4A4b", out, sizeof(out)), 2, "either case");
    HX_ASSERT_EQ(hux_hex_decode("466", out, sizeof(out)), -1, "odd length");
    HX_ASSERT_EQ(hux_hex_decode("4g", out, sizeof(out)), -1, "not hex");
    HX_ASSERT_EQ(hux_hex_decode("4100", out, sizeof(out)), -1, "a NUL inside");
    char small[3];
    HX_ASSERT_EQ(hux_hex_decode("414243", small, sizeof(small)), -1, "too long for the buffer");
    HX_ASSERT_EQ(hux_hex_decode("4142", small, sizeof(small)), 2, "just fits");
}

/* -- F-0036: adding and forgetting from afar ---------------------------- */

static void test_add_plan(void) {
    hux_wifi_net_t k[HUX_WIFI_MAX] = {
        {.ssid = "Casa", .pass = "casa-pass", .compiled = true},
        {.ssid = "Hogar 2G", .pass = "hogar-pass", .compiled = false},
    };
    int n = 2;
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Nuevo", "nuevo-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_STAGE, "a new name from afar: staged, never joined blind");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Nuevo", "nuevo-pass", HUX_WIFI_SRC_USB),
                 HUX_WIFI_ADD_JOIN, "on USB: joined now, as before");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Nuevo", "nuevo-pass", HUX_WIFI_SRC_DOOR),
                 HUX_WIFI_ADD_REFUSED, "never over the LAN door");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Hogar 2G", "otra-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_SECOND, "a known name: a second password");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Casa", "otra-pass", HUX_WIFI_SRC_USB),
                 HUX_WIFI_ADD_SECOND, "a compiled name too (it shadowed saved ones before)");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Hogar 2G", "hogar-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_SAME, "the password it has: nothing");
    strcpy(k[1].pass2, "otra-pass");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Hogar 2G", "otra-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_SAME, "its second one: nothing");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Hogar 2G", "tercera", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_SECOND, "a new second replaces the untried second, never the first");
    strcpy(k[0].pass2, "casa-nueva");
    k[0].alt = k[0].alt_proven = true; /* the compiled name's stored password reached the server */
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Casa", "tercera", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_IN_USE, "never replaces the stored one that works");
    for (int i = 2; i < 2 + HUX_WIFI_SAVED_MAX - 1; i++) {
        snprintf(k[i].ssid, sizeof(k[i].ssid), "Red %d", i);
        n++;
    }
    HX_ASSERT_EQ(hux_wifi_add_plan(k, n, "Otra", "otra-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_FULL, "four saved or staged at most");
}

static void test_forget_plan(void) {
    hux_wifi_net_t k[] = {
        {.ssid = "Casa", .pass = "x", .compiled = true},
        {.ssid = "Hogar 2G", .pass = "x", .compiled = false},
        {.ssid = "Mala", .pass = "x", .compiled = false, .staged = true},
    };
    HX_ASSERT_EQ(hux_wifi_forget_plan(k, 3, "Mala", 1, HUX_WIFI_SRC_SERVER), HUX_WIFI_FORGET_OK,
                 "a staged mistake, from afar");
    HX_ASSERT_EQ(hux_wifi_forget_plan(k, 3, "Hogar 2G", 1, HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_FORGET_ACTIVE, "never the one it is on, from afar");
    HX_ASSERT_EQ(hux_wifi_forget_plan(k, 3, "Hogar 2G", 1, HUX_WIFI_SRC_USB), HUX_WIFI_FORGET_OK,
                 "on USB, as before");
    HX_ASSERT_EQ(hux_wifi_forget_plan(k, 3, "Casa", 1, HUX_WIFI_SRC_USB),
                 HUX_WIFI_FORGET_COMPILED, "a compiled one never");
    HX_ASSERT_EQ(hux_wifi_forget_plan(k, 3, "Nada", 1, HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_FORGET_NOT_KNOWN, "not known");
    HX_ASSERT_EQ(hux_wifi_forget_plan(k, 3, "Mala", 1, HUX_WIFI_SRC_DOOR),
                 HUX_WIFI_FORGET_REFUSED, "never over the LAN door");
}

static void test_second_password_after_three_wrong(void) {
    hux_wifi_auth_t a = {0};
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 1000, true, true), "one: maybe weak signal");
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 2000, false, true), "another reason breaks the run");
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 3000, true, true), "1");
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 4000, true, true), "2");
    HX_ASSERT_TRUE(hux_wifi_auth_step(&a, 5000, true, true), "3 in a row: the other password");
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 6000, true, true), "1 on the other");
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 7000, true, true), "2");
    HX_ASSERT_TRUE(hux_wifi_auth_step(&a, 8000, true, true),
                   "3: straight back (the first may have failed on a weak signal)");
    for (uint32_t t = 9000; t < 12000; t += 1000) {
        HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, t, true, true), "then no more this window");
    }
    HX_ASSERT_TRUE(!hux_wifi_auth_step(&a, 4999 + HUX_WIFI_FLIP_EVERY_MS, true, true),
                   "just under 10 min from the first switch: still none");
    HX_ASSERT_TRUE(hux_wifi_auth_step(&a, 5000 + HUX_WIFI_FLIP_EVERY_MS, true, true),
                   "a new window: switch");
    hux_wifi_auth_t b = {0};
    for (int i = 0; i < 5; i++) {
        HX_ASSERT_TRUE(!hux_wifi_auth_step(&b, (uint32_t)i, true, false),
                       "no second password: never");
    }
}

/* The old saved entry, byte for byte: an image from before F-0036 reads w0..w3
 * with `len == sizeof(wifi_saved_t)` (98) and drops anything else. */
static void test_storage_an_older_image_reads(void) {
    HX_ASSERT_EQ((int)sizeof(hux_wifi_blob_t), 98, "w blobs keep the old size");
    hux_wifi_net_t nets[HUX_WIFI_MAX] = {
        {.ssid = "Casa", .pass = "casa-pass", .compiled = true, .alt = true, .pass2 = "casa-nueva", .alt_proven = true},
        {.ssid = "Hogar 2G", .pass = "hogar-pass", .compiled = false, .pass2 = "hogar-nueva"},
        {.ssid = "Lejana", .pass = "lejana-pass", .compiled = false, .staged = true},
    };
    hux_wifi_store_t st;
    hux_wifi_split(nets, 3, NULL, 0, &st);
    HX_ASSERT_EQ(st.nsaved, 1, "only the saved one that reached the server is old-format");
    HX_ASSERT_TRUE(strcmp(st.saved[0].pass, "hogar-pass") == 0, "with its first password");
    HX_ASSERT_EQ(st.nstaged, 1, "the staged one apart: a rollback never joins it");
    HX_ASSERT_EQ(st.nalts, 2, "two second passwords");
    HX_ASSERT_EQ(st.alts[0].prefer, 1, "the compiled name's stored one reached the server");
    HX_ASSERT_EQ(st.alts[1].prefer, 0, "a saved name's second is only a candidate");

    hux_wifi_net_t back[HUX_WIFI_MAX] = {{.ssid = "Casa", .pass = "casa-pass", .compiled = true}};
    int n = 1;
    hux_wifi_blob_t shadow[HUX_WIFI_SAVED_MAX];
    int nshadow = 0;
    hux_wifi_merge(back, &n, &st, shadow, &nshadow);
    HX_ASSERT_EQ(n, 3, "all three back");
    HX_ASSERT_TRUE(strcmp(back[0].pass2, "casa-nueva") == 0 && back[0].alt && back[0].alt_proven,
                   "compiled: on its stored one");
    HX_ASSERT_TRUE(strcmp(back[1].pass2, "hogar-nueva") == 0 && !back[1].alt, "saved: on its first");
    HX_ASSERT_TRUE(back[2].staged && strcmp(back[2].pass, "lejana-pass") == 0, "staged stays staged");
}

static void test_merge_drops_what_a_known_name_covers(void) {
    hux_wifi_store_t st = {0};
    strcpy(st.saved[0].ssid, "Casa"); /* compiled in by an update since */
    strcpy(st.saved[0].pass, "vieja");
    strcpy(st.saved[1].ssid, "Hogar 2G");
    strcpy(st.saved[1].pass, "hogar-pass");
    st.nsaved = 2;
    strcpy(st.staged[0].ssid, "Hogar 2G"); /* an older image added it on USB meanwhile */
    strcpy(st.staged[0].pass, "otra");
    st.nstaged = 1;
    strcpy(st.alts[0].ssid, "Nadie"); /* its name forgotten by an older image */
    strcpy(st.alts[0].pass, "x-pass-xx");
    strcpy(st.alts[1].ssid, "Hogar 2G"); /* the same as its first: no second */
    strcpy(st.alts[1].pass, "hogar-pass");
    st.nalts = 2;
    memset(st.saved[2].ssid, 'A', sizeof(st.saved[2].ssid)); /* no NUL: corrupt */
    st.nsaved = 3;
    hux_wifi_net_t nets[HUX_WIFI_MAX] = {{.ssid = "Casa", .pass = "casa-pass", .compiled = true}};
    int n = 1;
    hux_wifi_blob_t shadow[HUX_WIFI_SAVED_MAX];
    int nshadow = 0;
    hux_wifi_merge(nets, &n, &st, shadow, &nshadow);
    HX_ASSERT_EQ(n, 2, "Casa + Hogar 2G, nothing else");
    HX_ASSERT_EQ(nshadow, 1, "the shadowed save kept for a rollback");
    HX_ASSERT_TRUE(!nets[1].staged && nets[1].pass2[0] == '\0', "the saved entry wins, no second");
    hux_wifi_store_t again;
    hux_wifi_split(nets, n, shadow, nshadow, &again);
    HX_ASSERT_EQ(again.nsaved, 2, "the shadow is written back too");
}

/* The reviewer's cases (2026-10-17). */
static void test_confirm(void) {
    hux_wifi_net_t s = {.ssid = "Lejana", .pass = "lejana-pass", .staged = true};
    HX_ASSERT_TRUE(strcmp(hux_wifi_confirm(&s), "staged") == 0 && !s.staged, "staged -> saved");
    HX_ASSERT_TRUE(hux_wifi_confirm(&s) == NULL, "then nothing to save on every hello");

    hux_wifi_net_t r = {.ssid = "Telefono", .pass = "vieja-pass", .alt = true, .pass2 = "nueva-pass"};
    HX_ASSERT_TRUE(strcmp(hux_wifi_confirm(&r), "second") == 0, "the second got through");
    HX_ASSERT_TRUE(strcmp(r.pass, "nueva-pass") == 0 && !r.alt, "it is the first now");
    HX_ASSERT_TRUE(r.pass2[0] == '\0', "the dead old one is dropped: no switch can land on it");

    hux_wifi_net_t e = {.ssid = "Telefono", .pass = "vieja-pass", .alt = true};
    hux_wifi_confirm(&e);
    HX_ASSERT_TRUE(strcmp(e.pass, "vieja-pass") == 0, "an empty second never becomes the first");

    hux_wifi_net_t f = {.ssid = "Telefono", .pass = "uno-pass", .pass2 = "futura-pass"};
    HX_ASSERT_TRUE(hux_wifi_confirm(&f) == NULL && strcmp(f.pass2, "futura-pass") == 0,
                   "the first got through: a staged future password stays");

    hux_wifi_net_t c = {.ssid = "Casa", .pass = "casa-pass", .compiled = true, .alt = true,
                        .pass2 = "casa-nueva"};
    HX_ASSERT_TRUE(strcmp(hux_wifi_confirm(&c), "stored") == 0 && c.alt_proven,
                   "a compiled name: its stored one proven");
    HX_ASSERT_TRUE(hux_wifi_confirm(&c) == NULL, "once");
    c.alt = false; /* switched back, and the compiled one got through */
    HX_ASSERT_TRUE(strcmp(hux_wifi_confirm(&c), "compiled") == 0 && !c.alt_proven,
                   "proven the other way");
    hux_wifi_net_t u = {.ssid = "Casa", .pass = "casa-pass", .compiled = true, .alt = true,
                        .pass2 = "casa-nueva"};
    hux_wifi_store_t st;
    hux_wifi_split(&u, 1, NULL, 0, &st);
    HX_ASSERT_EQ(st.alts[0].prefer, 0, "on an unproven second, never saved as preferred");
}

static void test_second_and_restage(void) {
    hux_wifi_net_t k[] = {
        {.ssid = "Telefono", .pass = "uno-pass", .alt = true, .pass2 = "dos-pass"},
        {.ssid = "Lejana", .pass = "mal-pass", .staged = true},
    };
    hux_wifi_second(&k[0], "tres-pass");
    HX_ASSERT_TRUE(strcmp(k[0].pass2, "tres-pass") == 0 && !k[0].alt,
                   "a new second: the first is tried first again");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, 2, "Lejana", "bien-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_RESTAGE, "a staged name corrected: replaced and tried again");
    HX_ASSERT_EQ(hux_wifi_add_plan(k, 2, "Lejana", "mal-pass", HUX_WIFI_SRC_SERVER),
                 HUX_WIFI_ADD_SAME, "the same password: nothing");
}

static void test_storage_caps(void) {
    hux_wifi_net_t nets[HUX_WIFI_MAX] = {{.ssid = "Casa", .pass = "c", .compiled = true}};
    int n = 1;
    for (int i = 0; i < HUX_WIFI_SAVED_MAX; i++, n++) {
        snprintf(nets[n].ssid, sizeof(nets[n].ssid), "Red %d", i);
        snprintf(nets[n].pass, sizeof(nets[n].pass), "pass-%d-xx", i);
    }
    hux_wifi_blob_t shadow[1] = {{"Casa", "vieja-pass"}};
    hux_wifi_store_t st;
    hux_wifi_split(nets, n, shadow, 1, &st);
    HX_ASSERT_EQ(st.nsaved, HUX_WIFI_SAVED_MAX, "w0..w3 full of saved networks");
    HX_ASSERT_TRUE(strcmp(st.saved[HUX_WIFI_SAVED_MAX - 1].ssid, "Red 3") == 0,
                   "a shadow gives way to a network in use");

    hux_wifi_store_t in = {0};
    for (int i = 0; i < HUX_WIFI_SAVED_MAX; i++) {
        snprintf(in.saved[i].ssid, sizeof(in.saved[i].ssid), "Red %d", i);
        snprintf(in.saved[i].pass, sizeof(in.saved[i].pass), "pass-%d-xx", i);
    }
    in.nsaved = HUX_WIFI_SAVED_MAX;
    snprintf(in.staged[0].ssid, sizeof(in.staged[0].ssid), "Quinta");
    snprintf(in.staged[0].pass, sizeof(in.staged[0].pass), "quinta-pass");
    in.nstaged = 1;
    hux_wifi_net_t back[HUX_WIFI_MAX] = {{.ssid = "Casa", .pass = "c", .compiled = true}};
    int nb = 1;
    hux_wifi_blob_t sh[HUX_WIFI_SAVED_MAX];
    int nsh = 0;
    hux_wifi_merge(back, &nb, &in, sh, &nsh);
    HX_ASSERT_EQ(nb, 1 + HUX_WIFI_SAVED_MAX, "four saved: a fifth (staged) doesn't fit");
}

int main(void) {
    HX_RUN(test_heard_good_first_then_signal);
    HX_RUN(test_good_not_heard);
    HX_RUN(test_nothing_heard);
    HX_RUN(test_hidden_network_never_counts);
    HX_RUN(test_cap);
    HX_RUN(test_hex);
    HX_RUN(test_add_plan);
    HX_RUN(test_forget_plan);
    HX_RUN(test_second_password_after_three_wrong);
    HX_RUN(test_storage_an_older_image_reads);
    HX_RUN(test_merge_drops_what_a_known_name_covers);
    HX_RUN(test_confirm);
    HX_RUN(test_second_and_restage);
    HX_RUN(test_storage_caps);
    HX_SUMMARY();
}
