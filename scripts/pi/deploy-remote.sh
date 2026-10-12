#!/bin/sh
# Runs ON the Pi (called by scripts/pi/deploy.sh). Checks the walkie is
# idle BEFORE touching the working tree, then fast-forwards, syncs, marks
# the restart in bench.jsonl, restarts the user unit and waits for /health
# to report the new commit. Result line in /tmp/huxley-deploy.result.
#   deploy-remote.sh <commit> [--force | --when-idle]
set -u
want="$1"; mode="${2:-}"
logs="$HOME/huxley/server/runtime/logs"
result=/tmp/huxley-deploy.result
busy() {
  last=$(grep -hE '"(coord.ptt_start|server.tx.model_speaking|server.tx.stream_started)"' "$logs/huxley.log" 2>/dev/null \
    | tail -1 | sed -n 's/.*"timestamp": "\([^"]*\)".*/\1/p')
  if [ -n "$last" ]; then
    age=$(( $(date -u +%s) - $(date -u -d "$last" +%s) ))
    [ "$age" -lt 180 ] && { echo "pressed or spoke ${age}s ago"; return 0; }
  fi
  if [ "$(grep -hE '"server.tx.stream_(started|ended)"' "$logs/huxley.log" 2>/dev/null | tail -1 | grep -c started)" = 1 ]; then
    echo "a stream is playing"; return 0
  fi
  return 1
}
if [ "$mode" = "--when-idle" ]; then
  for i in $(seq 1 480); do why=$(busy) || break; sleep 30; done   # up to 4 h
  if why=$(busy); then echo "gave up after 4 h: $why" > "$result"; exit 3; fi
elif [ "$mode" != "--force" ]; then
  if why=$(busy); then echo "busy: $why (use --force or --when-idle)" | tee "$result"; exit 3; fi
fi
cd "$HOME/huxley" && git pull -q --ff-only && "$HOME/.local/bin/uv" sync -q
start=$(date -u +%Y-%m-%dT%H:%M:%SZ); end=$(date -u -d '+3 min' +%Y-%m-%dT%H:%M:%SZ)
echo "{\"start\": \"$start\", \"end\": \"$end\", \"what\": \"link test: deploy $want\"}" >> "$logs/bench.jsonl"
systemctl --user restart huxley.service
for i in $(seq 1 60); do
  got=$(curl -s -m 3 http://127.0.0.1:8765/health | sed -n 's/.*"commit": "\([0-9a-f]*\)".*/\1/p')
  [ "$got" = "$want" ] && { echo "deployed $want at $(date -u +%H:%M:%SZ)" | tee "$result"; exit 0; }
  sleep 2
done
echo "timeout: /health says ${got:-none}, wanted $want" | tee "$result"; exit 4
