#!/bin/bash
# Remove the Huxley launchd agent. Logs are kept; delete ~/Library/Logs/Huxley/ manually.
set -euo pipefail

DEST="$HOME/Library/LaunchAgents/com.huxley.server.plist"

if [[ -f "$DEST" ]]; then
    launchctl unload "$DEST" 2>/dev/null || true
    rm "$DEST"
    echo "✓ uninstalled"
else
    echo "(nothing to uninstall: $DEST not found)"
fi
