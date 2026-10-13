#!/usr/bin/env python3
"""How much of the far end comes back through the walkie's mic in a call
(F-0031). The recorder keeps both legs of every call: `<id>-<n>-claim-<who>.ogg`
(the far end, sent to the walkie) and `<id>-<n>-mic-<who>.ogg` (the walkie's mic,
sent back). This compares their loudness envelopes (20 ms frames, which
survive Opus and the speaker's distortion) at every delay and prints the
correlation, so two things show as peaks:

- before 0 ms: the mic hearing the far-end person directly, in the same room
  (a test with the phone next to the walkie; gone in real use);
- after 0 ms: the speaker echo, at the walkie's playback delay.

A working echo suppressor leaves no peak after 0 ms.

Usage (the call id is the recording's `<HHMMSS>-<conn>` prefix, e.g. from
the page's archive or `ls` on the Pi; fetched over ssh, nothing changes there):
    uv run --no-project --with numpy python scripts/call_echo.py 2026/10/13/015326-19f1f143
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

PI = "mario@100.75.152.70"
REC = "huxley/server/personas/abuelos/data/recordings"
LOGS = "huxley/server/runtime/logs"
SR, FRAME = 16_000, 320  # 20 ms frames


def ssh(cmd: str) -> str:
    return subprocess.run(["ssh", PI, cmd], check=True, capture_output=True, text=True).stdout


def envelope(path: Path) -> np.ndarray:
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
    n = len(x) // FRAME
    rms = np.sqrt((x[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1))
    return 20 * np.log10(rms + 1e-6)


def start_of(name: str) -> datetime:
    """The leg's start from its recorder.segment line (the file names are
    to the second; the legs start ~130 ms apart)."""
    out = ssh(f"grep -ah '\"recorder.segment\"' {LOGS}/huxley*.log | grep -F '{name}' | tail -1")
    return datetime.fromisoformat(json.loads(out)["start"].replace("Z", "+00:00"))


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    call = sys.argv[1].removesuffix(".ogg")
    names = ssh(f"ls {REC}/{call}-*claim-*.ogg {REC}/{call}-*mic-*.ogg").split()
    claim = next((n for n in names if "-claim-" in n), None)
    mic = next((n for n in names if "-mic-" in n), None)
    if claim is None or mic is None:
        sys.exit(f"no claim/mic pair for {call}: {names}")
    offset_ms = (start_of(Path(mic).name) - start_of(Path(claim).name)).total_seconds() * 1000
    with tempfile.TemporaryDirectory() as tmp:
        local = []
        for name in (claim, mic):
            dest = Path(tmp) / Path(name).name
            subprocess.run(["scp", "-q", f"{PI}:{name}", str(dest)], check=True)
            local.append(dest)
        far, near = envelope(local[0]), envelope(local[1])
    active = float(np.mean(far > -45) * 100)
    print(
        f"{Path(claim).name}: far end active {active:.0f}% of the call, mic starts {offset_ms:.0f} ms later"
    )
    if active < 5:
        print("  the far end barely spoke: nothing to measure")
        return
    n = min(len(far), len(near))
    a, b = far[:n] - far[:n].mean(), near[:n] - near[:n].mean()
    rows = []
    for lag in range(-25, 150):  # -0.5 s .. +3 s, in frames
        x, y = (a[: n - lag], b[lag:]) if lag >= 0 else (a[-lag:], b[: n + lag])
        if len(x) > 50:
            rows.append((lag * 20 + offset_ms, float(np.corrcoef(x, y)[0, 1])))
    for ms, c in rows[::3]:
        print(f"  {ms:6.0f} ms {'#' * int(max(c, 0) * 60)} {c:.2f}")
    echo = max((r for r in rows if r[0] > 100), key=lambda r: r[1])
    print(
        f"echo: strongest after 100 ms is {echo[1]:.2f} at {echo[0]:.0f} ms "
        f"({'an echo' if echo[1] > 0.3 else 'no clear echo'})"
    )


if __name__ == "__main__":
    main()
