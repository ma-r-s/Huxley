#!/bin/sh
# Deploys main to the production server on the Orange Pi (T1.43 W1).
# Run from the Mac checkout:
#   scripts/pi/deploy.sh              # deploy now if the walkie is idle
#   scripts/pi/deploy.sh --force      # deploy now even if it's busy
#   scripts/pi/deploy.sh --when-idle  # wait ON the Pi (up to 4 h) for idle, then deploy
# The walkie is checked BEFORE the Pi's working tree changes; see
# deploy-remote.sh. With --when-idle this returns at once; the result
# lands in /tmp/huxley-deploy.result on the Pi.
set -eu
pi="${HUXLEY_PI:-mario@100.75.152.70}"
mode="${1:-}"
here="$(cd "$(dirname "$0")/../.." && pwd)"
want="$(git -C "$here" rev-parse --short=7 main)"
git -C "$here" push -q "ssh://$pi/home/mario/huxley.git" main:main
# The remote script comes from the commit being deployed, not the working tree.
ssh -o BatchMode=yes "$pi" "git -C ~/huxley.git show main:scripts/pi/deploy-remote.sh > /tmp/huxley-deploy-remote.sh && chmod +x /tmp/huxley-deploy-remote.sh && rm -f /tmp/huxley-deploy.result"
if [ "$mode" = "--when-idle" ]; then
  ssh -o BatchMode=yes "$pi" "nohup /tmp/huxley-deploy-remote.sh $want --when-idle > /tmp/huxley-deploy.log 2>&1 &"
  echo "waiting on the Pi for the walkie to be idle; result in /tmp/huxley-deploy.result there"
else
  ssh -o BatchMode=yes "$pi" "/tmp/huxley-deploy-remote.sh $want $mode"
fi
