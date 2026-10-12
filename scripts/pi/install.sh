#!/bin/sh
# Installs the production Huxley unit on the Orange Pi. Run on the Pi:
#   sudo ~/huxley/scripts/pi/install.sh
# Needs root once (system unit, time-sync wait). Does not start the
# server: the cutover from the Mac starts it (docs/remote-operation.md).
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
install -m 0644 "$here/huxley.service" /etc/systemd/system/huxley.service
systemctl enable systemd-time-wait-sync.service
systemctl daemon-reload
systemctl enable huxley.service
echo "Installed and enabled (not started). Start at the cutover: sudo systemctl start huxley"
