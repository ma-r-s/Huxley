#!/bin/sh
# Calls the production server's admin routes, on the Pi (T1.43 hardening).
# The admin token comes from the server's .env, read with the server's own
# parser (python-dotenv, so `export`, quotes and comments mean the same),
# and reaches curl on a file descriptor: never on a command line, in a log
# or in shell history. Arguments are key=value query parameters, nothing
# else; one `key@-` instead takes that value from stdin (one line), for a
# line carrying a secret (a Wi-Fi password) that must stay off command lines.
#   scripts/pi/admin.sh firmware                              # status
#   scripts/pi/admin.sh firmware-offer version=<v> [force=1]
#   scripts/pi/admin.sh firmware-offer withdraw=1
#   scripts/pi/admin.sh dev-cmd 'line=battery cutoff off' [timeout_s=10]
#   printf %s 'wifi addhex ...' | scripts/pi/admin.sh dev-cmd line@-
# From the Mac: ssh mario@100.75.152.70 "~/huxley/scripts/pi/admin.sh firmware"
set -eu
[ $# -ge 1 ] || { sed -n '9,14p' "$0" | sed 's/^# *//'; exit 2; }
env="${HUXLEY_ENV:-$HOME/huxley/server/runtime/.env}"
route="$1"; shift
python="${HUXLEY_PYTHON:-$HOME/huxley/.venv/bin/python}"
stdin_key=
for arg in "$@"; do  # a plain key first: curl reads a file for name@file
  case "$arg" in
    *@-)  # key@-: curl reads this value from its stdin, never a file
      key="${arg%@-}"
      case "$key" in ''|*[!A-Za-z0-9_]*) bad=1 ;; *) bad= ;; esac
      [ -z "$bad" ] && [ -z "$stdin_key" ] || { echo "one plain key@- at most: $arg" >&2; exit 2; }
      stdin_key=$key
      continue ;;
  esac
  key="${arg%%=*}"
  case "$arg:$key" in
    *=*:?*) case "$key" in *[!A-Za-z0-9_]*) bad=1 ;; *) bad= ;; esac ;;
    *) bad=1 ;;
  esac
  [ -z "$bad" ] || { echo "not key=value: $arg" >&2; exit 2; }
done
token=$("$python" -c 'import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get("HUXLEY_ADMIN_TOKEN") or "")' "$env") \
  || { echo "could not read $env with $python" >&2; exit 2; }
[ -n "$token" ] || { echo "no HUXLEY_ADMIN_TOKEN in $env" >&2; exit 2; }
value=
if [ -n "$stdin_key" ]; then  # one line; a trailing newline is not part of it
  IFS= read -r value || [ -n "$value" ] || { echo "nothing on stdin for $stdin_key@-" >&2; exit 2; }
fi
n=$#
for arg in "$@"; do set -- "$@" --data-urlencode "$arg"; done
shift "$n"
# The value on curl's stdin (empty when there is none), the token on fd 3.
printf '%s' "$value" \
  | curl -sS --max-time 70 -H @/dev/fd/3 --get "$@" "http://127.0.0.1:${HUXLEY_SERVER_PORT:-8765}/admin/$route" 3<<EOF
X-Huxley-Admin: $token
EOF
echo
