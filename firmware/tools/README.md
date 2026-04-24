# firmware/tools/

Reusable helpers for bringing up, flashing, and debugging the firmware
without cluttering the repo with ad-hoc scripts.

**Captures land in `/tmp/huxley-debug/`** — outside the repo, auto-
cleared on reboot. Never commit captures.

## Scripts

| Script              | What it does                                                                                           | Needs          |
| ------------------- | ------------------------------------------------------------------------------------------------------ | -------------- |
| `serial-capture.sh` | Tails the board's USB-CDC to `/tmp/huxley-debug/serial-<ts>.log`. Non-interactive; runs in background. | —              |
| `serial-stop.sh`    | Kills any active `serial-capture.sh` capture.                                                          | —              |
| `tcpdump-ws.sh`     | Packet-captures the server's WS traffic to `/tmp/huxley-debug/ws-<ts>.pcap` for Wireshark analysis.    | `sudo` (macOS) |

Typical debugging session:

```sh
# Terminal 1 — board-side logs
./firmware/tools/serial-capture.sh
tail -f /tmp/huxley-debug/serial-latest.log

# Terminal 2 — wire-level WS traffic (needs sudo)
./firmware/tools/tcpdump-ws.sh

# Now press K2 on the board, reproduce the issue.
# Stop:
./firmware/tools/serial-stop.sh
# tcpdump stops with Ctrl+C in Terminal 2.

# Analyse:
tail -100 /tmp/huxley-debug/serial-latest.log
tshark -r /tmp/huxley-debug/ws-latest.pcap  # or open in Wireshark
```

## Why `/tmp/huxley-debug/` instead of `./build/` or similar?

- **Auto-cleanup**: macOS clears `/tmp` on reboot. No accumulating
  multi-GB captures.
- **Out of git**: nothing in `/tmp` can end up in a commit.
- **Shared across checkouts**: multiple clones or worktrees see the
  same captures without cross-contamination.
