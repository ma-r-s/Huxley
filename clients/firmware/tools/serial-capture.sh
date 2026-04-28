#!/usr/bin/env bash
# Capture the board's USB-CDC serial output to a timestamped file under
# /tmp/huxley-debug/. Non-interactive; returns immediately with the
# capture running in the background.
#
# Usage:
#   ./serial-capture.sh            # uses /dev/cu.usbmodem2101
#   ./serial-capture.sh /dev/cu.usbmodem...  # explicit port
#
# Output files:
#   /tmp/huxley-debug/serial-YYYYmmdd-HHMMSS.log   (per-session)
#   /tmp/huxley-debug/serial-latest.log            (symlink to newest)
#   /tmp/huxley-debug/serial.pid                   (for serial-stop.sh)
set -euo pipefail

PORT="${1:-/dev/cu.usbmodem2101}"
OUTDIR="/tmp/huxley-debug"
mkdir -p "$OUTDIR"

# Reap any prior capture on the same port so we don't split bytes.
if [[ -f "$OUTDIR/serial.pid" ]]; then
    OLD_PID=$(cat "$OUTDIR/serial.pid" 2>/dev/null || true)
    if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
        sleep 0.3
    fi
fi

if [[ ! -e "$PORT" ]]; then
    echo "error: port $PORT not found. Plug the board in?" >&2
    exit 1
fi

# 115200 8N1 raw, no echo — matches ESP-IDF console default.
stty -f "$PORT" 115200 cs8 -cstopb -parenb raw -echo

OUT="$OUTDIR/serial-$(date +%Y%m%d-%H%M%S).log"
cat "$PORT" >"$OUT" 2>&1 &
PID=$!
disown "$PID" 2>/dev/null || true
echo "$PID" >"$OUTDIR/serial.pid"

ln -sfn "$(basename "$OUT")" "$OUTDIR/serial-latest.log"

cat <<EOF
serial capture started
  pid:    $PID
  port:   $PORT
  out:    $OUT
  latest: $OUTDIR/serial-latest.log
follow live:   tail -f $OUTDIR/serial-latest.log
stop:          $(dirname "$0")/serial-stop.sh
EOF
