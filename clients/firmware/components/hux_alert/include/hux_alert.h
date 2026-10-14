/**
 * hux_alert: the walkie tells Mario's phone itself when it can't reach the
 * server (F-0032), over ntfy.sh with the walkie's own IP (a cloud checker
 * shares Cloudflare's IPs and hits ntfy's per-IP quota). When to send:
 * hux_alert_logic.h. `alert.sent kind=down|fixed|test status=` is a warning,
 * so the black box carries it. The topic is a secret: compiled in from the
 * gitignored secrets.h, never logged.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Start the clock sync (SNTP, always: the connection sounds' night needs
 * it too) and the alert task. `topic` NULL or empty: alerts off (logged
 * once). `click`: the URL a tap on the alert opens. */
void hux_alert_start(const char *topic, const char *click);

/** Console `alert test`: one low-priority push now, end to end. */
void hux_alert_test(void);

#ifdef __cplusplus
}
#endif
