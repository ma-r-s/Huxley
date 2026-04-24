#!/usr/bin/env bash
# End-to-end boot regression check.
# Resets the board and waits for the "state WAITING_HELLO -> READY"
# signal on serial within a timeout. Exit 0 on pass, non-zero on fail.
#
# Prereqs: Huxley server running on 0.0.0.0:8765, board flashed with
# current firmware, ESP-IDF env sourced (`. ~/esp/esp-idf/export.sh`).
#
# Usage:
#   firmware/tools/smoke.sh            # assumes board already flashed
#   firmware/tools/smoke.sh --flash    # reflash from current build first
#   firmware/tools/smoke.sh --timeout 30
set -euo pipefail

PORT="/dev/cu.usbmodem2101"
TIMEOUT_SEC=20
FLASH=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --flash)   FLASH=true; shift ;;
        --timeout) TIMEOUT_SEC="$2"; shift 2 ;;
        --port)    PORT="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^set/p' "$0" | sed 's/^# \{0,1\}//' | head -n -1
            exit 0 ;;
        *) echo "error: unknown arg $1" >&2; exit 2 ;;
    esac
done

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
FW_DIR="$(cd "$TOOLS_DIR/.." && pwd)"
OUTDIR="/tmp/huxley-debug"
mkdir -p "$OUTDIR"
LOG="$OUTDIR/smoke-$(date +%Y%m%d-%H%M%S).log"
READY_MARKER="state WAITING_HELLO -> READY"

if ! [[ -e "$PORT" ]]; then
    echo "FAIL port $PORT not found. Plug the board in?" >&2
    exit 1
fi

if $FLASH; then
    echo "flashing..."
    (cd "$FW_DIR" && idf.py -p "$PORT" flash >/dev/null 2>&1) || {
        echo "FAIL idf.py flash failed" >&2
        exit 1
    }
fi

# Clear any existing serial capture so we start from a known state.
"$TOOLS_DIR/serial-stop.sh" >/dev/null 2>&1 || true

# Reset the board via esptool so capture starts from a clean boot.
# `--no-stub run` toggles RTS and exits — fast, doesn't re-flash.
python -m esptool --chip esp32s3 --port "$PORT" --no-stub run >/dev/null 2>&1 || true

stty -f "$PORT" 115200 cs8 -cstopb -parenb raw -echo
cat "$PORT" >"$LOG" 2>&1 &
CAT_PID=$!
trap 'kill "$CAT_PID" 2>/dev/null || true' EXIT

secs=0
while (( secs < TIMEOUT_SEC )); do
    if grep -Fq "$READY_MARKER" "$LOG" 2>/dev/null; then
        echo "PASS boot-to-READY in ~${secs}s"
        echo "     log: $LOG"
        exit 0
    fi
    sleep 1
    secs=$((secs + 1))
done

echo "FAIL no READY transition seen in ${TIMEOUT_SEC}s" >&2
echo "     log: $LOG" >&2
echo "     last lines:" >&2
tail -15 "$LOG" >&2 || true
exit 1
