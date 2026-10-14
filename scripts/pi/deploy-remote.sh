#!/bin/sh
# Runs ON the Pi (called by scripts/pi/deploy.sh). Checks the walkie is
# idle BEFORE touching the working tree, then fast-forwards to exactly
# <commit> (a later push to the Pi doesn't change what a waiting deploy
# installs), syncs, marks the restart in bench.jsonl, restarts the user unit
# and waits for /health to report the new commit. Result line in
# /tmp/huxley-deploy.result.
#   deploy-remote.sh <commit> [--force | --when-idle]
# A newer deploy replaces a waiting one (main only fast-forwards, so the
# newer commit carries the older): --when-idle at once, a plain or forced
# deploy only once it goes ahead (busy, it leaves the waiter be). A deploy
# under way is never replaced, and two never overlap (flock).
set -u
want="$1"; mode="${2:-}"
logs="$HOME/huxley/server/runtime/logs"
result=/tmp/huxley-deploy.result
waiting=/tmp/huxley-deploy.waiting   # "<pid> <commit>" while a deploy waits for idle
fail() { echo "update failed, server untouched: $1" | tee "$result"; exit 5; }
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
  # Something already said to be said, waiting for him to hear it (a timer
  # or reminder that fired while the walkie was off): a restart drops it.
  # 2026-10-14 a fired pasta timer was lost to a deploy this way.
  queued=$(curl -s --max-time 3 "http://127.0.0.1:${HUXLEY_SERVER_PORT:-8765}/health" \
    | sed -n 's/.*"injects_waiting": \([0-9]*\).*/\1/p')
  if [ -n "$queued" ] && [ "$queued" -gt 0 ]; then
    echo "$queued announcement(s) waiting for him to hear"; return 0
  fi
  return 1
}
supersede() {
  [ -f "$waiting" ] || return 0
  read -r old oldwant < "$waiting"
  [ "$old" = "$$" ] && return 0
  if kill -0 "$old" 2>/dev/null && grep -q huxley-deploy-remote "/proc/$old/cmdline" 2>/dev/null; then
    kill "$old" && echo "replaced the deploy waiting for $oldwant"
  fi
}
unwait() { [ -f "$waiting" ] && [ "$(cut -d' ' -f1 "$waiting")" = "$$" ] && rm -f "$waiting"; return 0; }
if [ "$mode" = "--when-idle" ]; then
  supersede; echo "$$ $want" > "$waiting"
  for i in $(seq 1 480); do why=$(busy) || break; sleep 30; done   # up to 4 h
  unwait
  if why=$(busy); then echo "gave up after 4 h: $why" > "$result"; exit 3; fi
elif [ "$mode" != "--force" ]; then
  if why=$(busy); then echo "busy: $why (use --force or --when-idle)" | tee "$result"; exit 3; fi
  supersede
else
  supersede
fi
exec 9>/tmp/huxley-deploy.lock && flock 9
cd "$HOME/huxley" || fail "no ~/huxley"
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = main ] || fail "~/huxley is not on main"
git fetch -q || fail "git fetch"
git merge -q --ff-only "$want" || fail "fast-forward to $want (local changes or a diverged main?)"
"$HOME/.local/bin/uv" sync -q || fail "uv sync"
start=$(date -u +%Y-%m-%dT%H:%M:%SZ); end=$(date -u -d '+3 min' +%Y-%m-%dT%H:%M:%SZ)
echo "{\"start\": \"$start\", \"end\": \"$end\", \"what\": \"link test: deploy $want\"}" >> "$logs/bench.jsonl"
systemctl --user restart huxley.service
for i in $(seq 1 60); do
  got=$(curl -s -m 3 http://127.0.0.1:8765/health | sed -n 's/.*"commit": "\([0-9a-f]*\)".*/\1/p')
  [ "$got" = "$want" ] && { echo "deployed $want at $(date -u +%H:%M:%SZ)" | tee "$result"; exit 0; }
  sleep 2
done
echo "timeout: /health says ${got:-none}, wanted $want" | tee "$result"; exit 4
