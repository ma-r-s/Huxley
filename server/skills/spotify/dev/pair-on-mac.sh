#!/bin/sh
# One-time pairing of the Soloist dev container with a Spotify account,
# on a Mac. Soloist pairs through Spotify Connect discovery on the local
# network, which a Docker container on macOS can't take part in. So this
# runs Soloist in --pair mode, forwards a fixed port into its pairing
# endpoint, and announces the device from the Mac itself (dns-sd).
#
# Then: in a Spotify app signed into the account (same Wi-Fi), open the
# device picker and choose the device. Soloist stores the login in
# $SOLOIST_DATA and exits; afterwards use run.sh. On Linux, Soloist
# announces itself and none of this is needed: run.sh with --pair.
set -eu
. "$(dirname "$0")/_common.sh"
port=47100

docker rm -f "$container" >/dev/null 2>&1 || true
docker run -d --name "$container" --env-file "$envfile" -v "$data:/data" \
  -p 127.0.0.1:9090:9090 -p 127.0.0.1:4712:4712 -p "$port:$port" \
  "$image" --pair >/dev/null

zc=""
for _ in $(seq 1 60); do
  # Soloist colors its log lines; drop the escape codes before parsing.
  zc="$(docker logs "$container" 2>&1 | perl -pe 's/\e\[[0-9;]*m//g' \
    | sed -n 's/.*ZeroConf service on port \([0-9]*\) path \([^ ]*\).*/\1 \2/p' | tail -1)"
  [ -n "$zc" ] && break
  sleep 1
done
[ -n "$zc" ] || { echo "Soloist didn't start its pairing service"; docker logs "$container" | tail -5; exit 1; }
zc_port="${zc% *}"
zc_path="${zc#* }"

docker exec -d "$container" socat "TCP-LISTEN:$port,fork,reuseaddr" "TCP:127.0.0.1:$zc_port"
dns-sd -R "$name" _spotify-connect._tcp local "$port" "CPath=$zc_path" VERSION=1.0 Stack=SP >/dev/null &
announce=$!
trap 'kill $announce 2>/dev/null; rm -f "$envfile"' EXIT

echo "Announcing \"$name\". In a Spotify app signed into the account, pick it from the device list."
docker wait "$container" >/dev/null
if docker logs "$container" 2>&1 | grep -qi "error"; then
  docker logs "$container" 2>&1 | grep -i "error" | tail -3
fi
echo "Pairing finished; start the player with run.sh."
