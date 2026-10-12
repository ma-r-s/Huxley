#!/usr/bin/env bash
# Capture the board's USB console to a timestamped file under
# /tmp/huxley-debug/, in the background, without resetting the board
# (tools/serial_log.py holds the USB control lines still).
#
# Usage:
#   ./serial-capture.sh
#
# Output files:
#   /tmp/huxley-debug/serial-YYYYmmdd-HHMMSS.log   (per-session)
#   /tmp/huxley-debug/serial-latest.log            (symlink to newest)
#   /tmp/huxley-debug/serial.pid                   (for serial-stop.sh)
#   /tmp/huxley-debug/console.fifo                 (echo info > it: a console command)
set -euo pipefail

OUTDIR="/tmp/huxley-debug"
mkdir -p "$OUTDIR"

# Reap any prior capture so we don't split bytes.
if [[ -f "$OUTDIR/serial.pid" ]]; then
    OLD_PID=$(cat "$OUTDIR/serial.pid" 2>/dev/null || true)
    if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
        sleep 0.3
    fi
fi

OUT="$OUTDIR/serial-$(date +%Y%m%d-%H%M%S).log"
python "$(dirname "$0")/serial_log.py" "$OUT" --cmd "$OUTDIR/console.fifo" 2>/dev/null &
PID=$!
disown "$PID" 2>/dev/null || true
echo "$PID" >"$OUTDIR/serial.pid"
ln -sfn "$(basename "$OUT")" "$OUTDIR/serial-latest.log"

cat <<MSG
serial capture started (no reset)
  pid:     $PID
  out:     $OUT
  latest:  $OUTDIR/serial-latest.log
follow:    tail -f $OUTDIR/serial-latest.log
command:   echo info > $OUTDIR/console.fifo
stop:      $(dirname "$0")/serial-stop.sh
MSG
