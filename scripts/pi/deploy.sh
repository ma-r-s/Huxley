#!/bin/sh
# Deploys main to the production server on the Orange Pi (T1.43 W1).
# Run from the Mac checkout:  scripts/pi/deploy.sh [--force]
#
# 1. pushes main to the Pi's private repo and fast-forwards ~/huxley;
# 2. uv sync;
# 3. refuses when the walkie was busy in the last 3 minutes (a press,
#    an answer, a stream) unless --force;
# 4. marks the restart in bench.jsonl (planned: no alert on the page);
# 5. restarts the user unit and waits until /health reports the new commit.
set -eu
pi="${HUXLEY_PI:-mario@100.75.152.70}"
force="${1:-}"
here="$(cd "$(dirname "$0")/../.." && pwd)"
want="$(git -C "$here" rev-parse --short=7 main)"
git -C "$here" push -q "ssh://$pi/home/mario/huxley.git" main:main
ssh -o BatchMode=yes "$pi" FORCE="$force" WANT="$want" 'sh -s' <<'REMOTE'
set -eu
cd ~/huxley && git pull -q --ff-only && ~/.local/bin/uv sync -q
logs=~/huxley/server/runtime/logs
if [ "$FORCE" != "--force" ]; then
  last=$(grep -hE '"(coord.ptt_start|server.tx.model_speaking|server.tx.stream_started)"' "$logs/huxley.log" 2>/dev/null \
    | tail -1 | sed -n 's/.*"timestamp": "\([^"]*\)".*/\1/p')
  if [ -n "$last" ]; then
    age=$(( $(date -u +%s) - $(date -u -d "$last" +%s) ))
    if [ "$age" -lt 180 ]; then echo "walkie busy ${age}s ago: not restarting (use --force)"; exit 3; fi
  fi
  if grep -q '"server.tx.stream_started"' "$logs/huxley.log" && \
     [ "$(grep -hE '"server.tx.stream_(started|ended)"' "$logs/huxley.log" | tail -1 | grep -c started)" = 1 ]; then
    echo "a stream is playing: not restarting (use --force)"; exit 3
  fi
fi
start=$(date -u +%Y-%m-%dT%H:%M:%SZ); end=$(date -u -d '+3 min' +%Y-%m-%dT%H:%M:%SZ)
echo "{\"start\": \"$start\", \"end\": \"$end\", \"what\": \"link test: deploy $WANT\"}" >> "$logs/bench.jsonl"
systemctl --user restart huxley.service
for i in $(seq 1 60); do
  got=$(curl -s -m 3 http://127.0.0.1:8765/health | sed -n 's/.*"commit": "\([0-9a-f]*\)".*/\1/p')
  [ "$got" = "$WANT" ] && { echo "deployed $WANT: /health answers"; exit 0; }
  sleep 2
done
echo "timeout: /health says commit=${got:-none}, wanted $WANT"; exit 4
REMOTE
