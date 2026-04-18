#!/bin/bash
# Install the Huxley launchd agent so the server starts at login + restarts on crash.
# Idempotent — safe to re-run after editing the plist.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/com.huxley.server.plist"
DEST="$HOME/Library/LaunchAgents/com.huxley.server.plist"
LOG_DIR="$HOME/Library/Logs/Huxley"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

# Unload any previous instance so we install over a clean slate.
if launchctl list | grep -q com.huxley.server; then
    echo "→ unloading existing agent"
    launchctl unload "$DEST" 2>/dev/null || true
fi

cp "$SRC" "$DEST"
launchctl load "$DEST"

echo "✓ installed: $DEST"
echo "✓ logs:      $LOG_DIR/huxley.log"
echo
echo "Verify:    launchctl list | grep huxley"
echo "Tail logs: tail -f \"$LOG_DIR/huxley.log\""
echo "Uninstall: $(dirname "$0")/uninstall.sh"
