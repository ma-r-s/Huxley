#!/bin/sh
# Starts the Cloudflare Tunnel that puts the production server on the
# internet as https/wss://walkie.ma-r-s.com (tunnel "huxley-walkie",
# remotely managed: its routing lives in Cloudflare, set with the `cf` CLI;
# see docs/remote-operation.md W2). Run on the Pi:  ~/huxley/scripts/pi/tunnel.sh
#
# Only start it while the server runs with HUXLEY_DEVICE_TOKEN set: the
# production role then refuses any tunneled connection without the
# walkie's token, and /admin, /health stay LAN and Tailscale only (the
# tunnel also answers 404 for them).
#
# - --network host: the connector reaches the server at localhost:8765.
# - Pinned version, Watchtower off: an update drops every WebSocket,
#   grandpa's included. Update by hand, at night.
# - The token is in ~/.config/huxley-tunnel/env (mode 600), never here.
set -eu
version=2026.9.1
docker rm -f huxley-tunnel >/dev/null 2>&1 || true
docker run -d --name huxley-tunnel --restart unless-stopped --network host \
  --label com.centurylinklabs.watchtower.enable=false \
  --env-file "$HOME/.config/huxley-tunnel/env" \
  "cloudflare/cloudflared:$version" tunnel --no-autoupdate run >/dev/null
echo "huxley-tunnel running (cloudflared $version): docker logs -f huxley-tunnel"
