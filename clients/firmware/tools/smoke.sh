#!/usr/bin/env bash
# End-to-end boot check: reboots the board (through its console, never
# the USB reset lines) and waits for "state WAITING_HELLO -> READY".
# Exit 0 on pass.
#
# Prereqs: Huxley server on 0.0.0.0:8765, ESP-IDF env sourced
# (`. ~/esp/esp-idf/export.sh`; its python has pyserial).
#
# Usage:
#   tools/smoke.sh            # the firmware already on the board
#   tools/smoke.sh --flash    # tools/flash.sh first (build/ -> board)
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "--flash" ]]; then
    "$TOOLS_DIR/flash.sh" >/dev/null
fi
exec python "$TOOLS_DIR/bench.py" --reset --steps boot
