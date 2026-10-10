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

pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit -n \
  --load=module-native-protocol-unix \
  --load="module-null-sink sink_name=huxley_spotify rate=48000" \
  --load="module-simple-protocol-tcp port=4712 listen=0.0.0.0 record=true playback=false source=huxley_spotify.monitor format=s16le rate=24000 channels=1"
pactl set-default-sink huxley_spotify

# Current build from Spotify, re-fetched only if newer (If-Modified-Since).
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
  curl -fsSL -o "$archive" "https://soloist-builds.spotifycdn.com/$pkg"
fi
tar -xzf "$archive" -C /data/bin
/data/bin/soloist --version || true

exec /data/bin/soloist -n "$DEVICE_NAME" -k "$SOLOIST_API_KEY" \
  -D /data/state -C /cache -w 0.0.0.0:9090 -i 100 "$@"
