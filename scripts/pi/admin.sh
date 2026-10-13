#!/bin/sh
# Calls the production server's admin routes, on the Pi (T1.43 hardening).
# The admin token comes from the server's .env and reaches curl on stdin:
# never on a command line, in a log or in shell history.
#   scripts/pi/admin.sh firmware                              # status
#   scripts/pi/admin.sh firmware-offer version=<v> [force=1]
#   scripts/pi/admin.sh firmware-offer withdraw=1
#   scripts/pi/admin.sh dev-cmd 'line=battery cutoff off' [timeout_s=10]
# From the Mac: ssh mario@100.75.152.70 "~/huxley/scripts/pi/admin.sh firmware"
set -eu
[ $# -ge 1 ] || { sed -n '5,9p' "$0" | sed 's/^# *//'; exit 2; }
env="${HUXLEY_ENV:-$HOME/huxley/server/runtime/.env}"
route="$1"; shift
token=$(sed -n 's/^HUXLEY_ADMIN_TOKEN=//p' "$env" | tail -1 | tr -d "\"'")
[ -n "$token" ] || { echo "no HUXLEY_ADMIN_TOKEN in $env" >&2; exit 2; }
n=$#
for arg in "$@"; do set -- "$@" --data-urlencode "$arg"; done
shift "$n"
printf 'X-Huxley-Admin: %s\n' "$token" \
  | curl -sS --max-time 70 -H @- --get "$@" "http://127.0.0.1:${HUXLEY_SERVER_PORT:-8765}/admin/$route"
echo
