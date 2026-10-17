#!/bin/sh
# Starts a private PulseAudio whose only output is a null sink, serves
# that sink's monitor as raw PCM (s16le, 24 kHz, mono: Huxley's wire
# format) over TCP, then runs Soloist into it.
#
# Env: SOLOIST_API_KEY (required), DEVICE_NAME (default Huxley).
# Ports: 9090 WebSocket control, 4712 PCM capture.
set -eu
: "${SOLOIST_API_KEY:?set SOLOIST_API_KEY}"
DEVICE_NAME="${DEVICE_NAME:-Huxley}"
mkdir -p "$XDG_RUNTIME_DIR" /data/bin /cache
# A restart keeps the container's files: after a power cut the killed
# daemon's pid file is still here, its number now belongs to another
# process, and PulseAudio refuses to start ("Daemon startup failed", a
# restart loop: 2026-10-13). The runtime dir is this container's alone.
if [ -e "$XDG_RUNTIME_DIR/pulse" ]; then
  echo "entrypoint: removing stale PulseAudio runtime state ($XDG_RUNTIME_DIR/pulse)" >&2
  rm -rf "$XDG_RUNTIME_DIR/pulse"
fi

# --log-target=stderr: the container has no syslog; this lands in docker logs.
pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit -n --log-target=stderr \
  --load=module-native-protocol-unix \
  --load="module-null-sink sink_name=huxley_spotify rate=48000" \
  --load="module-simple-protocol-tcp port=4712 listen=0.0.0.0 record=true playback=false source=huxley_spotify.monitor format=s16le rate=24000 channels=1"
pactl set-default-sink huxley_spotify

# After a power cut the box boots before its network and clock are up:
# Soloist tried its one login then ("login failed: make sure --api-key is
# valid") and never again, three days without music (2026-10-14 to 10-17).
# Wait until Spotify answers before fetching or starting anything.
tries=0
until curl -fsS -o /dev/null --max-time 5 https://apresolve.spotify.com/; do
  [ $((tries % 12)) -eq 0 ] && echo "entrypoint: waiting for Spotify to be reachable (${tries}x5 s so far)" >&2
  tries=$((tries + 1))
  sleep 5
done
[ "$tries" -gt 0 ] && echo "entrypoint: Spotify reachable after $((tries * 5)) s" >&2

# Current build from Spotify, re-fetched only if newer (If-Modified-Since).
# Always download to .new and move into place: a partial first download
# must not become the file that -z compares against forever.
arch="$(uname -m)"
case "$arch" in
  aarch64) pkg=soloist_release_arm64.tar.gz ;;
  x86_64) pkg=soloist_release_x86_64.tar.gz ;;
  *) echo "unsupported arch $arch" >&2; exit 1 ;;
esac
archive="/data/bin/$pkg"
if [ -f "$archive" ]; then
  curl -fsSL -z "$archive" -o "$archive.new" "https://soloist-builds.spotifycdn.com/$pkg" \
    && [ -s "$archive.new" ] && mv "$archive.new" "$archive" || rm -f "$archive.new"
else
  curl -fsSL -o "$archive.new" "https://soloist-builds.spotifycdn.com/$pkg"
  mv "$archive.new" "$archive"
fi
tar -xzf "$archive" -C /data/bin
/data/bin/soloist --version || true

exec /data/bin/soloist -n "$DEVICE_NAME" -k "$SOLOIST_API_KEY" \
  -D /data/state -C /cache -w 0.0.0.0:9090 -i 100 "$@"
