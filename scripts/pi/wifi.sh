#!/usr/bin/env bash
# The walkie's Wi-Fi networks from afar (T1.52, firmware F-0036): run from
# the Mac, in your own terminal. Everything goes through the production
# server, so the walkie must be online; for a walkie that is offline, see
# clients/firmware/tools/wifi.py (USB, on site).
#
#   scripts/pi/wifi.sh add "Home Wi-Fi"      # asks for the password, hidden
#   scripts/pi/wifi.sh forget "Old Wi-Fi"    # any network but the one in use
#   scripts/pi/wifi.sh list                  # what the walkie knows
#
# add: the walkie saves the network and tries it only if it hears it now
# (going back to the one it was on if that fails, keeping the entry); a
# network not on the air yet (a new router the home announced) waits until
# it is heard. A name it already knows takes the password as its second
# one. The walkie refuses while he is using it or while an update installs.
# Best at night: a try can take it off the air for up to two minutes.
# The password goes on stdin end to end (admin.sh `line@-`): never on a
# command line, in shell history or in a log.
set -euo pipefail

pi="${HUXLEY_PI:-mario@100.75.152.70}"
admin='~/huxley/scripts/pi/admin.sh'
log='~/huxley/server/runtime/logs/huxley.log'
bench='~/huxley/server/runtime/logs/bench.jsonl'
tz="${HUXLEY_TZ:-America/Bogota}"

usage() { sed -n '7,9p' "$0" | sed 's/^# *//' >&2; exit 2; }
hex() { printf '%s' "$1" | od -An -tx1 | tr -d ' \n'; }

cmd="${1:-}"
case "$cmd" in
  list)
    [ $# -eq 1 ] || usage
    ssh "$pi" "$admin dev-cmd 'line=wifi list'"
    exit 0 ;;
  add|forget)
    [ $# -eq 2 ] || usage
    ssid="$2" ;;
  *) usage ;;
esac
n=$(printf '%s' "$ssid" | wc -c | tr -d ' ')
if [ "$n" -lt 1 ] || [ "$n" -gt 32 ]; then
  echo "a network name is 1 to 32 bytes (this one: $n)" >&2
  exit 2
fi

if [ "$cmd" = forget ]; then
  ssh "$pi" "$admin dev-cmd 'line=wifi forget $(hex "$ssid")'"
  exit 0
fi

IFS= read -r -s -p "Password for \"$ssid\": " pw; echo >&2
IFS= read -r -s -p "Again: " pw2; echo >&2
[ "$pw" = "$pw2" ] || { echo "the two didn't match: nothing sent" >&2; exit 2; }
n=$(printf '%s' "$pw" | wc -c | tr -d ' ')
if [ "$n" -eq 0 ]; then
  # The walkie joins WPA2 or better only (F-0036): no open networks.
  echo "the walkie only joins networks with a password (WPA2 or better): nothing sent" >&2
  exit 2
fi
if [ "$n" -lt 8 ] || [ "$n" -gt 64 ]; then
  echo "a Wi-Fi password is 8 to 64 characters (this one: $n): nothing sent" >&2
  exit 2
fi

hour=$(TZ="$tz" date +%H)
if [ "${hour#0}" -ge 7 ] && [ "${hour#0}" -lt 22 ]; then
  IFS= read -r -p "It's daytime there: if the walkie hears this network now it tries it, off the air for up to 2 minutes. Send anyway? [y/N] " ok
  case "$ok" in y|Y|yes) ;; *) echo "nothing sent" >&2; exit 1 ;; esac
fi

# Keep phone alerts quiet through a try (the page cuts bench windows out).
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
end=$(date -u -r $(( $(date +%s) + 300 )) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || date -u -d "@$(( $(date +%s) + 300 ))" +%Y-%m-%dT%H:%M:%SZ)
printf '{"start": "%s", "end": "%s", "what": "wifi.sh add (a new network for the walkie, from afar)"}\n' "$start" "$end" \
  | ssh "$pi" "cat >> $bench"

# The outcome comes in the walkie's log lines after the try, not in the
# command's answer: follow them for a while, started before sending.
ssh -n "$pi" "timeout 150 tail -n0 -F $log 2>/dev/null | grep --line-buffered -o '\"line\": \"wifi\.[^\"]*'" \
  | while IFS= read -r l; do echo "  walkie: ${l#\"line\": \"}"; done &
watcher=$!
trap 'kill "$watcher" 2>/dev/null || true' EXIT
sleep 1

printf '%s' "wifi addhex $(hex "$ssid") $(hex "$pw")" \
  | ssh "$pi" "$admin dev-cmd line@- timeout_s=20"
unset pw pw2
echo "Saved on the walkie if it said queued. Following its log for two minutes (Ctrl-C to stop);"
echo "nothing more means it doesn't hear that network now: it tries it when it does."
wait "$watcher" 2>/dev/null || true
