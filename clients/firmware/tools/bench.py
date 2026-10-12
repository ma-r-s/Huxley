#!/usr/bin/env python3
"""Bench check list for the walkie: real board, real server, no hand, no sound.

Drives the board through its serial console (components/hux_console) and
asserts on its own log lines. Each step prints PASS or FAIL with the line
that decided it; the exit code is the number of failures.

    . ~/esp/esp-idf/export.sh            # its python has pyserial
    python tools/bench.py --reset        # every step
    python tools/bench.py --steps turn,bargein
    python tools/bench.py --loud         # keep the speaker amp on

Needs: the server running on the LAN (`server_uri` in main/secrets.h), the
board flashed with the clips (`tools/clips.py`), no other client on the
server (the eviction step brings its own and leaves). Steps that need a
hand (K2, the case's lever) aren't here: tools/README.md says how to press
GPIO7 over JTAG.
"""

import argparse
import base64
import glob
import os
import re
import socket
import sys
import threading
import time

import serial

STEPS = ["boot", "quiet", "selftest", "turn", "volume", "bargein", "offline", "evict", "health"]


class Board:
    def __init__(self, port: str, log_path: str) -> None:
        self.log = open(log_path, "w", buffering=1)
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, 115200, 0.05
        # Held high and never changed: on the S3's USB-Serial/JTAG a reset
        # needs RTS high with DTR low, and a reset at the wrong moment can
        # leave the chip in download mode (tools/flash.sh --unstick).
        self.s.dtr = self.s.rts = True
        self.s.open()
        self.lines: list[str] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        buf = b""
        while True:
            try:
                buf += self.s.read(4096)
            except serial.SerialException:
                time.sleep(0.3)  # re-enumerating after a reset
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").rstrip()
                self.log.write(time.strftime("%H:%M:%S ") + text + "\n")
                with self.lock:
                    self.lines.append(text)

    def mark(self) -> int:
        with self.lock:
            return len(self.lines)

    def send(self, cmd: str) -> None:
        self.log.write(time.strftime("%H:%M:%S ") + f"[bench] >> {cmd}\n")
        self.s.write(cmd.encode() + b"\n")

    def reset(self) -> None:
        # Through the firmware (esp_restart), never the USB reset lines.
        self.send("reboot")

    def expect(self, pattern: str, timeout: float, since: int) -> str | None:
        """First line after `since` matching `pattern`, or None at timeout."""
        rx = re.compile(pattern)
        deadline = time.time() + timeout
        i = since
        while time.time() < deadline:
            with self.lock:
                new = self.lines[i:]
            for line in new:
                i += 1
                if rx.search(line):
                    return line
            time.sleep(0.05)
        return None

    def grep(self, pattern: str, since: int) -> list[str]:
        rx = re.compile(pattern)
        with self.lock:
            return [line for line in self.lines[since:] if rx.search(line)]


def ws_evict(uri: str, hold: float) -> str:
    """Connect as a second client (the server evicts the board), then leave."""
    m = re.match(r"ws://([^:/]+):(\d+)(/.*)?", uri)
    if not m:
        return f"bad uri {uri}"
    host, port, path = m.group(1), int(m.group(2)), m.group(3) or "/"
    with socket.create_connection((host, port), timeout=5) as sock:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        status = sock.recv(4096).split(b"\r\n", 1)[0].decode()
        time.sleep(hold)
        mask = os.urandom(4)
        body = (1000).to_bytes(2, "big")
        sock.sendall(
            bytes([0x88, 0x80 | len(body)])
            + mask
            + bytes(b ^ mask[i % 4] for i, b in enumerate(body))
        )
        return status


def server_uri() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "main", "secrets.h")
    try:
        m = re.search(r'HUX_SERVER_URI\s+"([^"]+)"', open(path).read())
        return m.group(1) if m else "ws://127.0.0.1:8765/"
    except OSError:
        return "ws://127.0.0.1:8765/"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    ap.add_argument("--port", default=ports[0] if ports else None)
    ap.add_argument("--reset", action="store_true", help="reset the board first (checks the boot)")
    ap.add_argument("--loud", action="store_true", help="leave the speaker amp on")
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--server", default=server_uri(), help="for the eviction step")
    args = ap.parse_args()
    if not args.port:
        print("FAIL no /dev/cu.usbmodem* port: plug the board in")
        return 1
    steps = args.steps.split(",")
    if "boot" in steps and not args.reset:
        steps.remove("boot")
    if "quiet" in steps and args.loud:
        steps.remove("quiet")

    os.makedirs("/tmp/huxley-debug", exist_ok=True)
    log_path = time.strftime("/tmp/huxley-debug/bench-%Y%m%d-%H%M%S.log")
    print(f"board log: {log_path}")
    b = Board(args.port, log_path)
    fails = 0

    def result(name: str, ok: bool, why: str) -> None:
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:9s} {why}", flush=True)

    def ready_now(timeout: float = 4) -> bool:
        m = b.mark()
        b.send("info")
        line = b.expect(r"info ring_used=", timeout, m)
        return line is not None

    # If the board happens to be booting (just plugged in), let it get to
    # READY before the first step.
    if "boot" not in steps:
        time.sleep(1.5)
        if b.grep(r"boot version=", 0):
            if not b.expect(r"state WAITING_HELLO -> READY", 30, 0):
                print("FAIL  the board reset on open and never got READY")
                return 1

    def run(step: str) -> None:
        m = b.mark()
        if step == "boot":
            b.reset()
            line = b.expect(r"state WAITING_HELLO -> READY", 30, m)
            ours = [x for x in b.grep(r" E \(\d+\) hux_", m)]
            result("boot", line is not None and not ours, line or f"no READY in 30 s {ours[:1]}")
        elif step == "quiet":
            b.send("pa off")
            line = b.expect(r"spk\.pa off ok=1", 3, m)
            result("quiet", line is not None, line or "the amp didn't turn off")
        elif step == "selftest":
            b.send("selftest 1000")
            line = b.expect(r"audio\.selftest result=", 4, m)
            result("selftest", bool(line and "result=pass" in line), line or "no result")
        elif step == "turn":
            b.send("say 0")  # "Que hora es?" (clips.py)
            sent = b.expect(r"app\.ptt\.final_stats", 8, m)
            start = b.expect(r"ws\.rx\.model_speaking true", 15, m)
            end = b.expect(r"ws\.rx\.model_speaking false", 40, m)
            time.sleep(1)
            bad = b.grep(r"tx_fail=[1-9]|spk\.underrun|spk\.ring\.stuck|ws\.disconnected", m)
            ok = bool(sent and "tx_fail=0" in sent and start and end and not bad)
            result(
                "turn",
                ok,
                (bad[0] if bad else None)
                or (end if ok else f"sent={bool(sent)} answered={bool(start)} ended={bool(end)}"),
            )
        elif step == "volume":
            b.send("say 1")  # "Pon el volumen al ochenta."
            line = b.expect(r"spk\.volume level=80", 25, m)
            b.expect(r"ws\.rx\.model_speaking false", 25, m)
            b.send("vol 50")
            m2 = b.mark()
            b.send("info")
            info = b.expect(r"dac_reg=", 4, m2)
            ok = bool(line and info and "dac_reg=0xa8" in info)
            result("volume", ok, line or "no set_volume 80")
        elif step == "bargein":
            b.send("say 3")  # a long story
            if not b.expect(r"ws\.rx\.model_speaking true", 20, m):
                result("bargein", False, "the story never started")
                return
            time.sleep(6)
            m2 = b.mark()
            b.send("say 6")  # "Que dia es hoy?" over it
            hold = b.expect(r"spk\.hold on", 3, m2)
            off = b.expect(r"spk\.hold off by=", 4, m2)
            answer = b.expect(r"ws\.rx\.model_speaking true", 20, m2)
            b.expect(r"ws\.rx\.model_speaking false", 30, m2)
            held = re.search(r"held_ms=(\d+)", off or "")
            ok = bool(
                hold
                and off
                and "by=audio_clear" in off
                and answer
                and held
                and int(held.group(1)) < 2000
            )
            result("bargein", ok, off or "no hold / no audio_clear")
        elif step == "offline":
            b.send("wifi off")
            time.sleep(2)
            m2 = b.mark()
            b.send("talk")
            time.sleep(0.3)
            b.send("release")
            cue = b.expect(r"cue=offline", 3, m2)
            b.send("wifi on")
            back = b.expect(r"state WAITING_HELLO -> READY", 40, m2)
            result(
                "offline",
                bool(cue and back),
                (back if cue and back else None) or f"cue={bool(cue)} back={bool(back)}",
            )
        elif step == "evict":
            status = ws_evict(args.server, 3)
            evicted = b.expect(r"net\.ws\.evicted", 5, m)
            m2 = b.mark()
            b.send("talk")
            time.sleep(0.3)
            b.send("release")
            back = b.expect(r"state WAITING_HELLO -> READY", 10, m2)
            result(
                "evict",
                bool(evicted and back),
                f"test client {status}; " + (back or f"evicted={bool(evicted)} back={bool(back)}"),
            )
        elif step == "health":
            b.send("info")
            heap = b.expect(r"info uptime_s=", 4, m)
            stacks = b.expect(r"info stack_free_min", 4, m)
            low = [kv for kv in re.findall(r"(\w+)=(\d+)", stacks or "") if int(kv[1]) < 512]
            hmin = re.search(r"heap_internal=\d+ min=(\d+)", heap or "")
            ok = bool(stacks and hmin and not low and int(hmin.group(1)) > 32768)
            result(
                "health",
                ok,
                f"stacks {'low: ' + str(low) if low else 'ok'}; internal heap min {hmin.group(1) if hmin else '?'}",
            )
        else:
            result(step, False, "unknown step")

    for step in steps:
        try:
            run(step)
        except Exception as e:  # a step that can't run is a failure, not a crash
            result(step, False, f"{type(e).__name__}: {e}")

    print(f"{'all passed' if fails == 0 else f'{fails} failed'}")
    return fails


if __name__ == "__main__":
    sys.exit(main())
