#!/bin/sh
# Runs the Soloist dev container: WebSocket control on 127.0.0.1:9090,
# PCM capture on 127.0.0.1:4712 (the skill's defaults). Restarts with
# Docker; fetches a newer Soloist build from Spotify on each start.
# Pair once first (pair-on-mac.sh on a Mac). Extra args go to soloist.
set -eu
. "$(dirname "$0")/_common.sh"
docker rm -f "$container" >/dev/null 2>&1 || true
docker run -d --name "$container" --restart unless-stopped --env-file "$envfile" \
  -v "$data:/data" -p 127.0.0.1:9090:9090 -p 127.0.0.1:4712:4712 \
  "$image" "$@" >/dev/null
echo "Soloist running as \"$name\" (docker logs -f $container)."
