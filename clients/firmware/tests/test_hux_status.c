/**
 * Host unit tests for `hux_status` (F-0033): when the walkie says its
 * connection state with sounds.
 */
#include "test_helpers.h"

#include "hux_status.h"

#define S 1000u

/* Steps every second from `from` to `to` (exclusive) with the same inputs,
 * counting each sound played and held. Counted in steps, so a span across
 * the uint32 wrap runs too. */
static void run(hux_status_t *st, uint32_t from, uint32_t to, bool conn, bool busy, bool night,
                int played[5], int held_n[5]) {
    for (uint32_t t = from; t - from < to - from; t += S) {
        hux_status_sound_t held;
        hux_status_sound_t s = hux_status_step(st, t, conn, busy, night, &held);
        played[s]++;
        held_n[held]++;
    }
}

static void test_power_on_startup_ticks_then_connected(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    HX_ASSERT_EQ(hux_status_step(&st, 0, false, false, false, &held), HUX_STATUS_STARTUP, "startup");
    int p[5] = {0}, h[5] = {0};
    run(&st, S, 7 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_SEARCHING], 2, "a tick at 3 s and 6 s");
    HX_ASSERT_EQ(hux_status_step(&st, 7 * S, true, false, false, &held), HUX_STATUS_CONNECTED,
                 "connected after power on");
    HX_ASSERT_EQ(hux_status_step(&st, 8 * S, true, false, false, &held), HUX_STATUS_NONE, "once");
}

static void test_a_blip_is_silent(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    hux_status_step(&st, S, true, false, false, &held); /* connected */
    int p[5] = {0}, h[5] = {0};
    run(&st, 2 * S, 21 * S, false, false, false, p, h); /* 19 s gone */
    HX_ASSERT_EQ(hux_status_step(&st, 21 * S, true, false, false, &held), HUX_STATUS_NONE,
                 "back before 20 s: nothing");
    HX_ASSERT_EQ(p[HUX_STATUS_LOST] + p[HUX_STATUS_SEARCHING], 0, "nothing during it either");
}

static void test_lost_after_20_s_then_ticks_then_connected(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    hux_status_step(&st, S, true, false, false, &held);
    hux_status_step(&st, 2 * S, true, false, false, &held);
    int p[5] = {0}, h[5] = {0};
    run(&st, 3 * S, 23 * S, false, false, false, p, h); /* drops at 3 s */
    HX_ASSERT_EQ(p[HUX_STATUS_LOST], 0, "not yet at 22 s");
    run(&st, 23 * S, 24 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_LOST], 1, "lost once, 20 s after the drop");
    run(&st, 24 * S, 34 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_SEARCHING], 3, "then the ticks from the start of the schedule");
    HX_ASSERT_EQ(hux_status_step(&st, 34 * S, true, false, false, &held), HUX_STATUS_CONNECTED,
                 "connected after a lost");
}

static void test_schedule_fast_slow_stop(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    int p[5] = {0}, h[5] = {0};
    run(&st, S, 120 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_SEARCHING], 39, "every 3 s for the first 2 min");
    int q[5] = {0};
    run(&st, 120 * S, 17 * 60 * S, false, false, false, q, h);
    HX_ASSERT_TRUE(q[HUX_STATUS_SEARCHING] >= 29 && q[HUX_STATUS_SEARCHING] <= 31,
                   "every 30 s until 17 min");
    int r[5] = {0};
    run(&st, 17 * 60 * S, 60 * 60 * S, false, false, false, r, h);
    HX_ASSERT_EQ(r[HUX_STATUS_SEARCHING], 0, "then silence");
}

static void test_night_holds_but_moves_on(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    HX_ASSERT_EQ(hux_status_step(&st, 0, false, false, true, &held), HUX_STATUS_STARTUP,
                 "power on sounds even at night");
    int p[5] = {0}, h[5] = {0};
    run(&st, S, 10 * S, false, false, true, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_SEARCHING], 0, "no ticks at night");
    HX_ASSERT_EQ(h[HUX_STATUS_SEARCHING], 3, "held instead");
    HX_ASSERT_EQ(hux_status_step(&st, 10 * S, true, false, true, &held), HUX_STATUS_NONE,
                 "connected silent at night");
    HX_ASSERT_EQ(held, HUX_STATUS_CONNECTED, "held");
    HX_ASSERT_EQ(hux_status_step(&st, 11 * S, true, false, false, &held), HUX_STATUS_NONE,
                 "not owed in the morning: it moved on");
}

static void test_busy_defers_without_losing(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    HX_ASSERT_EQ(hux_status_step(&st, 5 * S, true, true, false, &held), HUX_STATUS_NONE,
                 "connected while a press: waits");
    HX_ASSERT_EQ(hux_status_step(&st, 6 * S, true, false, false, &held), HUX_STATUS_CONNECTED,
                 "plays once free");
    int p[5] = {0}, h[5] = {0};
    run(&st, 7 * S, 40 * S, false, true, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_LOST], 0, "never over playback");
    HX_ASSERT_EQ(hux_status_step(&st, 40 * S, false, false, false, &held), HUX_STATUS_LOST,
                 "the lost waits for a gap");
}

static void test_connected_let_go_when_the_server_talks(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    int p[5] = {0}, h[5] = {0};
    run(&st, 5 * S, 8 * S, true, true, false, p, h); /* a book resuming at once */
    HX_ASSERT_EQ(p[HUX_STATUS_CONNECTED] + h[HUX_STATUS_CONNECTED], 0, "waits 3 s");
    run(&st, 8 * S, 9 * S, true, true, false, p, h);
    HX_ASSERT_EQ(h[HUX_STATUS_CONNECTED], 1, "then let go, logged");
    run(&st, 9 * S, 60 * S, true, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_CONNECTED], 0, "and never played late");
}

static void test_connected_waits_out_a_short_sound(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    HX_ASSERT_EQ(hux_status_step(&st, 3 * S, true, true, false, &held), HUX_STATUS_NONE,
                 "the tick still playing");
    HX_ASSERT_EQ(hux_status_step(&st, 4 * S, true, false, false, &held), HUX_STATUS_CONNECTED,
                 "plays right after it");
}

/* A server in a crash loop: up 5 s, down 30 s, for 2 h. One lost, one
 * connected, the first schedule's ticks; not a lost and a connected each
 * time. */
static void test_a_crash_loop_is_one_outage(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    int p[5] = {0}, h[5] = {0};
    run(&st, S, 10 * 60 * S, true, false, false, p, h); /* a stable 10 min */
    int q[5] = {0};
    uint32_t t = 10 * 60 * S;
    while (t < 130 * 60 * S) {
        run(&st, t, t + 5 * S, true, false, false, q, h);
        run(&st, t + 5 * S, t + 35 * S, false, false, false, q, h);
        t += 35 * S;
    }
    HX_ASSERT_EQ(q[HUX_STATUS_LOST], 1, "one lost");
    HX_ASSERT_EQ(q[HUX_STATUS_CONNECTED], 1, "one connected");
    HX_ASSERT_TRUE(q[HUX_STATUS_SEARCHING] <= 70, "no more ticks than one schedule");
    /* Then the server holds: after 2 min up, a new drop is a new outage. */
    int r[5] = {0};
    run(&st, t, t + 3 * 60 * S, true, false, false, r, h);
    run(&st, t + 3 * 60 * S, t + 4 * 60 * S, false, false, false, r, h);
    HX_ASSERT_EQ(r[HUX_STATUS_LOST], 1, "a lost again after a stable connection");
}

static void test_drop_while_connected_waits_then_announces(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    HX_ASSERT_EQ(hux_status_step(&st, 5 * S, true, true, false, &held), HUX_STATUS_NONE, "owed");
    HX_ASSERT_EQ(hux_status_step(&st, 6 * S, false, false, false, &held), HUX_STATUS_SEARCHING,
                 "gone again before it played: still searching");
    HX_ASSERT_EQ(hux_status_step(&st, 20 * S, true, false, false, &held), HUX_STATUS_CONNECTED,
                 "still owed at the next connection");
}

static void test_back_to_back_blips_are_silent(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    hux_status_step(&st, S, true, false, false, &held);
    int p[5] = {0}, h[5] = {0};
    for (uint32_t t = 2 * S; t < 600 * S; t += 30 * S) {
        run(&st, t, t + 18 * S, false, false, false, p, h); /* 18 s down */
        run(&st, t + 18 * S, t + 30 * S, true, false, false, p, h);
    }
    HX_ASSERT_EQ(p[HUX_STATUS_LOST] + p[HUX_STATUS_SEARCHING] + p[HUX_STATUS_CONNECTED], 0,
                 "every one under 20 s: nothing");
}

static void test_the_clock_wraps(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    uint32_t t0 = UINT32_MAX - 5 * S;
    HX_ASSERT_EQ(hux_status_step(&st, t0, false, false, false, &held), HUX_STATUS_STARTUP, "start");
    int p[5] = {0}, h[5] = {0};
    run(&st, t0 + S, t0 + 13 * S, false, false, false, p, h); /* across the wrap */
    HX_ASSERT_EQ(p[HUX_STATUS_SEARCHING], 4, "every 3 s across the wrap");
    HX_ASSERT_EQ(hux_status_step(&st, t0 + 14 * S, true, false, false, &held),
                 HUX_STATUS_CONNECTED, "connected");
    run(&st, t0 + 15 * S, t0 + 16 * S, false, false, false, p, h);
    run(&st, t0 + 16 * S, t0 + 36 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_LOST], 1, "lost 20 s after a drop across the wrap");
}

static void test_busy_at_night(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, true, &held);
    hux_status_step(&st, S, true, false, true, &held);
    hux_status_step(&st, 2 * S, false, false, true, &held); /* drops */
    int p[5] = {0}, h[5] = {0};
    run(&st, 3 * S, 40 * S, false, true, true, p, h); /* busy past 20 s */
    HX_ASSERT_EQ(h[HUX_STATUS_LOST], 0, "the lost waits for the gap at night too");
    HX_ASSERT_EQ(hux_status_step(&st, 40 * S, false, false, true, &held), HUX_STATUS_NONE, "held");
    HX_ASSERT_EQ(held, HUX_STATUS_LOST, "then held by the night");
    HX_ASSERT_TRUE(!st.held_busy, "and says so");
    /* A connected let go while busy at night reports busy, not night. */
    run(&st, 41 * S, 45 * S, true, true, true, p, h);
    HX_ASSERT_EQ(h[HUX_STATUS_CONNECTED], 1, "let go");
    HX_ASSERT_TRUE(st.held_busy, "for busy");
}

static void test_restart_keeps_an_owed_connected(void) {
    hux_status_t st;
    hux_status_init(&st, false, true); /* the ladder's reboot an hour into an outage */
    int p[5] = {0}, h[5] = {0};
    run(&st, 0, 60 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_SEARCHING] + p[HUX_STATUS_STARTUP], 0, "still quiet");
    hux_status_sound_t held;
    HX_ASSERT_EQ(hux_status_step(&st, 60 * S, true, false, false, &held), HUX_STATUS_CONNECTED,
                 "but says when the server is back");
}

static void test_the_schedule_end_is_flagged_once(void) {
    hux_status_t st;
    hux_status_init(&st, true, false);
    hux_status_sound_t held;
    hux_status_step(&st, 0, false, false, false, &held);
    int p[5] = {0}, h[5] = {0};
    run(&st, S, 17 * 60 * S, false, false, false, p, h);
    HX_ASSERT_TRUE(!st.schedule_over, "not yet");
    HX_ASSERT_EQ(st.ticks, (uint32_t)p[HUX_STATUS_SEARCHING], "ticks counted");
    run(&st, 17 * 60 * S, 17 * 60 * S + S, false, false, false, p, h);
    HX_ASSERT_TRUE(st.schedule_over, "over at 17 min");
}

static void test_software_restart_is_silent(void) {
    hux_status_t st;
    hux_status_init(&st, false, false);
    int p[5] = {0}, h[5] = {0};
    run(&st, 0, 60 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_STARTUP] + p[HUX_STATUS_SEARCHING], 0,
                 "no startup, no ticks (the ladder's restart at 3 am)");
    hux_status_sound_t held;
    HX_ASSERT_EQ(hux_status_step(&st, 60 * S, true, false, false, &held), HUX_STATUS_NONE,
                 "no connected either");
    run(&st, 61 * S, 90 * S, false, false, false, p, h);
    HX_ASSERT_EQ(p[HUX_STATUS_LOST], 1, "a later drop is announced as usual");
}

int main(void) {
    HX_RUN(test_power_on_startup_ticks_then_connected);
    HX_RUN(test_a_blip_is_silent);
    HX_RUN(test_lost_after_20_s_then_ticks_then_connected);
    HX_RUN(test_schedule_fast_slow_stop);
    HX_RUN(test_night_holds_but_moves_on);
    HX_RUN(test_busy_defers_without_losing);
    HX_RUN(test_connected_let_go_when_the_server_talks);
    HX_RUN(test_connected_waits_out_a_short_sound);
    HX_RUN(test_software_restart_is_silent);
    HX_RUN(test_a_crash_loop_is_one_outage);
    HX_RUN(test_drop_while_connected_waits_then_announces);
    HX_RUN(test_back_to_back_blips_are_silent);
    HX_RUN(test_the_clock_wraps);
    HX_RUN(test_busy_at_night);
    HX_RUN(test_restart_keeps_an_owed_connected);
    HX_RUN(test_the_schedule_end_is_flagged_once);
    HX_SUMMARY();
}
