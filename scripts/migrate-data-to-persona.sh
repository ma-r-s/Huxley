#!/usr/bin/env bash
# Historical: migrate data/ from the pre-stage-4 location (the original was at
# packages/core/data/, which existed before personas got their own data dirs and
# before the packages/→server/ restructure) into the AbuelOS persona directory.
# Idempotent. Safe to delete this script if you've already migrated; kept here
# as a reference for anyone restoring an ancient install.
#
# Source path is the legacy location (does not exist in the current tree).
# Destination is the current persona-local data dir under server/personas/.

set -euo pipefail

cd "$(dirname "$0")/.."

SRC_DIR="packages/core/data"
DST_DIR="server/personas/abuelos/data"

mkdir -p "$DST_DIR/audiobooks"

# 1. Database rename: abuel_os.db → abuelos.db
if [ -f "$SRC_DIR/abuel_os.db" ]; then
    if [ -f "$DST_DIR/abuelos.db" ]; then
        echo "note: $DST_DIR/abuelos.db already exists, leaving $SRC_DIR/abuel_os.db in place"
    else
        mv "$SRC_DIR/abuel_os.db" "$DST_DIR/abuelos.db"
        echo "moved DB → $DST_DIR/abuelos.db"
    fi
fi

# 2. Audiobook library
if [ -d "$SRC_DIR/audiobooks" ]; then
    shopt -s nullglob dotglob
    items=("$SRC_DIR"/audiobooks/*)
    if [ ${#items[@]} -gt 0 ]; then
        mv "$SRC_DIR"/audiobooks/* "$DST_DIR/audiobooks/"
        echo "moved ${#items[@]} audiobook item(s) → $DST_DIR/audiobooks/"
    fi
fi

# 3. Clean up empty legacy dirs (ignore errors if non-empty or already gone)
rmdir "$SRC_DIR/audiobooks" 2>/dev/null || true
rmdir "$SRC_DIR" 2>/dev/null || true

echo "done."
