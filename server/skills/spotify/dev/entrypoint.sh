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
# Until Soloist runs, a stop ends the script at once (not after Docker's
# 10 s); a trapped signal ends a `wait`, never a foreground `sleep`.
trap 'exit 143' TERM INT
nap() { sleep "$1" & wait $! || true; }
tries=0
until curl -fsS -o /dev/null --max-time 5 https://apresolve.spotify.com/; do
  [ $((tries % 12)) -eq 0 ] && echo "entrypoint: waiting for Spotify to be reachable (${tries}x5 s so far)" >&2
  tries=$((tries + 1))
  nap 5
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

# Soloist logs a failed login and then sits there logged out: it never
# tries again. Pass its output through; on "login failed" stop it, then end
# the container after a pause that doubles with each failure in a row (30 s
# up to 30 min, counted in /data across restarts, reset by a login), so
# Docker's restart policy tries again through the wait above without
# hammering Spotify over a key that will never work. Pairing runs as before.
case " $* " in
  *" --pair "*)
    exec /data/bin/soloist -n "$DEVICE_NAME" -k "$SOLOIST_API_KEY" \
      -D /data/state -C /cache -w 0.0.0.0:9090 -i 100 "$@"
    ;;
esac
fails_file=/data/login_failures
out="$XDG_RUNTIME_DIR/soloist.out"
rm -f "$out"
mkfifo "$out"
/data/bin/soloist -n "$DEVICE_NAME" -k "$SOLOIST_API_KEY" \
  -D /data/state -C /cache -w 0.0.0.0:9090 -i 100 "$@" >"$out" 2>&1 &
pid=$!
# Held open on fd 3 to the end: what Soloist says while it stops still has
# a reader (a closed FIFO would kill it with SIGPIPE mid-shutdown).
exec 3<"$out"
trap 'kill -TERM "$pid" 2>/dev/null' TERM INT
failed=0
while IFS= read -r line <&3 || [ -n "$line" ]; do
  printf '%s\n' "$line"
  case "$line" in
    *"logged in"*) rm -f "$fails_file" ;;
    *"login failed"*)
      failed=1
      kill -TERM "$pid" 2>/dev/null || true
      break
      ;;
  esac
  line=
done
cat <&3
status=0
wait "$pid" || status=$?
if [ "$failed" -eq 1 ]; then
  n=$(cat "$fails_file" 2>/dev/null || true)
  case "$n" in '' | *[!0-9]*) n=0 ;; esac
  n=$((n + 1))
  echo "$n" >"$fails_file"
  pause=$((${LOGIN_RETRY_S:-30} << (n < 7 ? n - 1 : 6)))
  [ "$pause" -gt 1800 ] && pause=1800
  echo "entrypoint: Soloist failed to log in ($n in a row); restarting the container in $pause s" >&2
  trap 'exit 1' TERM INT
  nap "$pause"
  exit 1
fi
exit "$status"
