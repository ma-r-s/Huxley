#!/usr/bin/env bash
# Capture WebSocket traffic between the board and the Huxley server on
# port 8765. Needs sudo on macOS (BPF access).
#
# Usage:
#   sudo ./tcpdump-ws.sh
#   # press K2 on the board; when done, Ctrl+C
#
# Output: /tmp/huxley-debug/ws-YYYYmmdd-HHMMSS.pcap (+ ws-latest.pcap symlink)
# Read with:
#   tshark -r /tmp/huxley-debug/ws-latest.pcap
#   # or open in Wireshark
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
    echo "error: needs sudo (macOS needs root for BPF)" >&2
    echo "try:   sudo $0" >&2
    exit 1
fi

OUTDIR="/tmp/huxley-debug"
mkdir -p "$OUTDIR"
OUT="$OUTDIR/ws-$(date +%Y%m%d-%H%M%S).pcap"

echo "capturing port 8765 -> $OUT (Ctrl+C to stop)"
ln -sfn "$(basename "$OUT")" "$OUTDIR/ws-latest.pcap"

# -i any  capture on all interfaces (lo0 + en0 — WS goes over en0 since
#         the board reaches us via LAN IP)
# -s 0    full packet, not truncated
# -w FILE pcap format
# filter: just the server port
exec tcpdump -i any -s 0 -w "$OUT" 'port 8765'
