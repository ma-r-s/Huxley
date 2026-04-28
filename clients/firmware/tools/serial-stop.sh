#!/usr/bin/env bash
# Stop any active serial capture started by serial-capture.sh.
set -euo pipefail

PIDFILE="/tmp/huxley-debug/serial.pid"

if [[ ! -f "$PIDFILE" ]]; then
    # Sweep anyway in case the pidfile was lost.
    pkill -f "cat /dev/cu.usbmodem" 2>/dev/null || true
    echo "no serial.pid; swept stray cats."
    exit 0
fi

PID=$(cat "$PIDFILE" 2>/dev/null || true)
if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    echo "stopped pid=$PID"
else
    echo "pid=$PID not alive; nothing to do."
fi
rm -f "$PIDFILE"
