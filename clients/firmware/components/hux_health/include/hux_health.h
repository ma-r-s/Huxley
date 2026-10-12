/**
 * hux_health: the device's minute line (`client_event device.health`,
 * remote-operation W5, F-0027).
 *
 * Every 60 s while connected: memory floors, the Wi-Fi link, disconnect
 * reasons and TLS errors with counts, reconnects, speaker underruns and
 * dropped audio, volume, quiet, the update state and the battery. Counts
 * are since the previous line that reached the server (a line that
 * couldn't be sent folds into the next), so they sum. The caregiver's
 * view on the server reads these instead of every log line.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Start the minute line. After the network and audio are up. */
void hux_health_start(void);

#ifdef __cplusplus
}
#endif
