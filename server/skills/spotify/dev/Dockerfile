# Dev host for Spotify Soloist on a Mac (Soloist is Linux-only).
# The Soloist binary is NOT in the image: Spotify forbids redistributing
# it, and builds expire after 90 days. entrypoint.sh downloads the
# current build from Spotify at start and keeps it in /data/bin.
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      pulseaudio pulseaudio-utils ca-certificates curl socat libatomic1 \
      iproute2 procps avahi-utils \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 soloist && mkdir -p /cache && chown soloist /cache
COPY entrypoint.sh /entrypoint.sh
USER soloist
ENV XDG_RUNTIME_DIR=/tmp/runtime
ENTRYPOINT ["/entrypoint.sh"]
