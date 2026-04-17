#!/usr/bin/env bash
# Migrate data/ from the pre-stage-4 location (packages/core/data/) into the
# AbuelOS persona directory. Idempotent — safe to run more than once.
#
# What it does, in order:
#   1. Moves packages/core/data/abuel_os.db → personas/abuelos/data/abuelos.db
#      (renames the file; audiobook positions and conversation summaries follow).
#   2. Moves packages/core/data/audiobooks/* → personas/abuelos/data/audiobooks/
#      (m4b/mp3 files; subdirectories by author).
#   3. Removes now-empty packages/core/data/audiobooks and packages/core/data.
#
# Nothing in git changes — packages/core/data/ was .gitignored. This script
# only moves local-only assets.

set -euo pipefail

cd "$(dirname "$0")/.."

SRC_DIR="packages/core/data"
DST_DIR="personas/abuelos/data"

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
