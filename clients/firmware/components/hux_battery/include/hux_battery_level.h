/**
 * hux_battery_level: the cell's voltage as a percentage (F-0025).
 *
 * Pure (no I/O), host-tested. A Li-ion cell's resting voltage maps to its
 * charge through a curve, not a line; readings are taken while the
 * speaker is quiet (the amp's draw sags the cell). Good for "about half"
 * and for warnings, not a precise gauge.
 */
#pragma once

/** Percent (0-100) for a resting cell voltage in mV, linear between the
 * points of a typical Li-ion curve. */
int hux_battery_pct(int cell_mv);

/** The percentage to report, from the last one reported (`prev`, -1 for
 * none) and the new reading: it follows a fall at once, but a rise only
 * past 5 points (charging), so a sag recovering doesn't make it flicker. */
int hux_battery_smooth(int prev, int now);
