#!/usr/bin/env bash
# Flash the walkie over USB and boot it. Use this, not `idf.py flash`.
#
# On the ESP32-S3's built-in USB (USB-Serial/JTAG) an RTS reset is only a
# core reset: it doesn't re-read the boot pins, so a chip that went into
# download mode stays there ("waiting for download") until a power cut.
# `idf.py flash` ends with that reset, and once left the walkie stuck
# inside its closed case. esptool's watchdog reset is a full system reset
# that re-reads the pins, so the app boots. (esptool docs, "Advanced
# Options": --after watchdog-reset.)
#
#   tools/flash.sh              build/ -> board, then boot it
#   tools/flash.sh --clips      also the bench clips (tools/clips.py)
#   tools/flash.sh --unstick    a board stuck in download mode: boot it
#
# Needs the ESP-IDF env (. ~/esp/esp-idf/export.sh). PORT= overrides.
set -euo pipefail

FW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -n 1 || true)}"
if [[ -z "$PORT" ]]; then
    echo "error: no /dev/cu.usbmodem* port: plug the board in" >&2
    exit 1
fi
ESPTOOL=(python -m esptool --chip esp32s3 -p "$PORT")

case "${1:-}" in
    --unstick)
        "${ESPTOOL[@]}" --before no_reset --after watchdog_reset read_mac
        echo "watchdog reset sent: the board boots its app"
        ;;
    ""|--clips)
        extra=()
        if [[ "${1:-}" == "--clips" ]]; then
            [[ -f "$FW_DIR/build/clips.bin" ]] || { echo "error: run tools/clips.py first" >&2; exit 1; }
            extra=(0x420000 clips.bin)
        fi
        cd "$FW_DIR/build"
        "${ESPTOOL[@]}" -b 921600 --before default_reset --after watchdog_reset \
            write_flash @flash_args "${extra[@]}"
        ;;
    *)
        sed -n '2,/^set/p' "$0" | sed 's/^# \{0,1\}//' | sed '$d'
        exit 2
        ;;
esac
