#!/usr/bin/env python3
"""Bench check list for the walkie: real board, real server, no hand, no sound.

Drives the board through its console (components/hux_console) and
asserts on its own log lines. Each step prints PASS or FAIL with the line
that decided it; the exit code is the number of failures.

    . ~/esp/esp-idf/export.sh            # its python has pyserial
    python tools/bench.py --reset        # every step, over USB serial
    python tools/bench.py --steps turn,bargein
    python tools/bench.py --loud         # keep the speaker amp on
    python tools/bench.py --wifi         # the same over Wi-Fi, case closed
    python tools/bench.py --wifi --steps stall,slowdown   # network faults

--wifi sends the commands through the developer door (tools/dev.py) and
reads the board's lines from the server log, where it streams them; the
server's own events (connects, `server.tx.audio_stats`) come in as
`[server] ...` lines. `stall` and `slowdown` route the board through
Toxiproxy (`brew install toxiproxy`; started if not running) and break the
link on purpose: the uplink stops mid-press, the downlink drops below real
time mid-stream (F-0022). Afterwards the board goes back to its server and
the amp back on.

Needs: the server running on the LAN (`server_uri` in main/secrets.h), the
board flashed with the clips (`tools/clips.py`), no other client on the
server (the eviction step brings its own and leaves). Steps that need a
hand (K2, the case's lever) aren't here: tools/README.md says how to press
GPIO7 over JTAG.
"""

import argparse
import base64
import contextlib
import glob
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

STEPS = ["boot", "quiet", "selftest", "turn", "volume", "bargein", "offline", "evict", "health"]
# Network faults, --wifi only, not in the default list (they need Toxiproxy).
NET_STEPS = ["stall", "slowdown"]
# Server events the Wi-Fi board sees as `[server] <event> k=v ...` lines.
SERVER_EVENTS = {
    "client_connected",
    "client_disconnected",
    "client_evicted",
    "server.rx.ptt_start",
    "server.rx.ptt_stop",
    "server.tx.audio_stats",
    "transcript",
}
TOXIPROXY = "http://127.0.0.1:8474"
PROXY_PORT = 8775


class Board:
    def __init__(self, port: str, log_path: str) -> None:
        import serial  # only the USB board needs pyserial

        self.serial_error = serial.SerialException
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
            except self.serial_error:
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


class WifiBoard(Board):
    """The same board over Wi-Fi: commands through the developer door,
    the board's lines from the server log (serial format, so every
    pattern works on both), plus `[server] ...` lines."""

    def __init__(self, host: str | None, log_path: str) -> None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import dev  # tools/dev.py, next to this file

        self.dev = dev
        self.host = host or dev.board_ip()
        self.log = open(log_path, "w", buffering=1)
        self.lines = []
        self.lock = threading.Lock()
        self.offset = dev.log_tail_from()
        threading.Thread(target=self._read, daemon=True).start()

    @staticmethod
    def format(e: dict) -> str | None:
        ev = e.get("event")
        if ev == "client.huxley.firmware_log":
            return f"{e.get('level', '?')} ({e.get('ts', 0)}) {e.get('tag')}: {e.get('line')}"
        if ev in SERVER_EVENTS:
            skip = ("event", "timestamp", "level", "logger")
            return f"[server] {ev} " + " ".join(f"{k}={v}" for k, v in e.items() if k not in skip)
        return None

    def _read(self) -> None:
        with open(self.dev.SERVER_LOG) as f:
            f.seek(self.offset)
            while True:
                raw = f.readline()
                if not raw:
                    time.sleep(0.1)
                    continue
                try:
                    text = self.format(json.loads(raw))
                except ValueError:
                    continue
                if text is None:
                    continue
                self.log.write(time.strftime("%H:%M:%S ") + text + "\n")
                with self.lock:
                    self.lines.append(text)

    def send(self, cmd: str) -> None:
        self.log.write(time.strftime("%H:%M:%S ") + f"[bench] >> {cmd}\n")
        self.dev.request(self.host, "/cmd", cmd.encode(), timeout=30)


def toxi(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(TOXIPROXY + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read()
    return json.loads(raw) if raw.strip() else {}


def proxy_up(server: str) -> str:
    """Toxiproxy between the board and the server: listens on PROXY_PORT,
    forwards to the server's port. Returns the URI for the board."""
    try:
        toxi("GET", "/version")
    except OSError:
        log = open("/tmp/huxley-debug/toxiproxy.log", "a")
        subprocess.Popen(["toxiproxy-server"], stdout=log, stderr=log, start_new_session=True)
        for _ in range(30):
            time.sleep(0.2)
            try:
                toxi("GET", "/version")
                break
            except OSError:
                continue
    m = re.match(r"ws://([^:/]+):(\d+)", server)
    port = m.group(2) if m else "8765"
    with contextlib.suppress(OSError):
        toxi("DELETE", "/proxies/huxley")
    toxi(
        "POST",
        "/proxies",
        {"name": "huxley", "listen": f"0.0.0.0:{PROXY_PORT}", "upstream": f"127.0.0.1:{port}"},
    )
    mac = m.group(1) if m else "127.0.0.1"
    return f"ws://{mac}:{PROXY_PORT}/"


def toxic(name: str, stream: str, kind: str, **attrs: int) -> None:
    """stream: "upstream" = board -> server, "downstream" = server -> board."""
    toxi(
        "POST",
        "/proxies/huxley/toxics",
        {"name": name, "type": kind, "stream": stream, "attributes": attrs},
    )


def untoxic(name: str) -> None:
    with contextlib.suppress(OSError):
        toxi("DELETE", f"/proxies/huxley/toxics/{name}")


def field(name: str, line: str) -> str:
    """The value of `name=` in a log line, "?" if absent."""
    m = re.search(rf"\b{name}=(\S+)", line)
    return m.group(1) if m else "?"


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
    ap.add_argument("--wifi", action="store_true", help="over Wi-Fi (tools/dev.py), no USB")
    ap.add_argument("--host", help="--wifi: the board's IP (default: from the server log)")
    ap.add_argument("--stall-s", type=float, default=5, help="stall: how long the uplink crawls")
    ap.add_argument("--stall-kbps", type=int, default=1, help="stall: its rate, KB/s")
    ap.add_argument("--slow-s", type=float, default=8, help="slowdown: how long")
    ap.add_argument(
        "--slow-kbps", type=int, default=32, help="slowdown: downlink KB/s (~64 is real time)"
    )
    args = ap.parse_args()
    if not args.wifi and not args.port:
        print("FAIL no /dev/cu.usbmodem* port: plug the board in (or --wifi)")
        return 1
    steps = args.steps.split(",")
    if not args.wifi and any(s in NET_STEPS for s in steps):
        print("FAIL stall and slowdown need --wifi (the board talks through a proxy)")
        return 1
    if "boot" in steps and not args.reset:
        steps.remove("boot")
    if "quiet" in steps and args.loud:
        steps.remove("quiet")

    os.makedirs("/tmp/huxley-debug", exist_ok=True)
    log_path = time.strftime("/tmp/huxley-debug/bench-%Y%m%d-%H%M%S.log")
    print(f"board log: {log_path}")
    b: Board = WifiBoard(args.host, log_path) if args.wifi else Board(args.port, log_path)
    fails = 0
    via_proxy = False
    pa_off = False

    def result(name: str, ok: bool, why: str) -> None:
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:9s} {why}", flush=True)

    def ready_now(timeout: float = 4) -> bool:
        m = b.mark()
        b.send("info")
        line = b.expect(r"info ring_used=", timeout, m)
        return line is not None

    def through_proxy() -> bool:
        """Point the board at Toxiproxy (once); True when it's connected."""
        nonlocal via_proxy
        if via_proxy:
            return True
        uri = proxy_up(args.server)
        m = b.mark()
        b.send(f"server {uri}")
        ok = b.expect(r"\[server\] client_connected remote=\('127\.", 15, m)
        ready = ok and b.expect(r"state WAITING_HELLO -> READY", 10, m)
        via_proxy = bool(ready)
        return via_proxy

    def cancel_reply() -> None:
        """An empty press: the server drops whatever it was saying."""
        b.send("talk")
        time.sleep(0.4)
        b.send("release")
        time.sleep(1.5)

    # If the board happens to be booting (just plugged in), let it get to
    # READY before the first step.
    if "boot" not in steps and not args.wifi:
        time.sleep(1.5)
        if b.grep(r"boot version=", 0):
            if not b.expect(r"state WAITING_HELLO -> READY", 30, 0):
                print("FAIL  the board reset on open and never got READY")
                return 1

    def run(step: str) -> None:
        nonlocal pa_off
        m = b.mark()
        if step == "boot":
            b.reset()
            line = b.expect(r"state WAITING_HELLO -> READY", 30, m)
            ours = [x for x in b.grep(r" E \(\d+\) hux_", m)]
            result("boot", line is not None and not ours, line or f"no READY in 30 s {ours[:1]}")
        elif step == "quiet":
            pa_off = True
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
        elif step == "offline" and args.wifi:
            # Over Wi-Fi nothing can reach it while it's off: it comes back
            # by itself (`wifi off 8`), and the lines it logged while
            # offline arrive after it reconnects (F-0022).
            b.send("wifi off 8")
            back = b.expect(r"console\.wifi back on", 30, m)
            ready = b.expect(r"state WAITING_HELLO -> READY", 30, m)
            result(
                "offline",
                bool(back and ready),
                (ready if back and ready else None) or f"back_on={bool(back)} ready={bool(ready)}",
            )
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
        elif step == "stall":
            # The uplink crawls at stall_kbps for stall_s, starting 1 s into
            # a ~5 s question (the mic sends ~66 KB/s). The connection must
            # survive it and the question must still get through.
            if not through_proxy():
                result("stall", False, "the board didn't connect through the proxy")
                return
            m = b.mark()
            b.send("say 3")  # "Cuentame un cuento largo..." (~5 s)
            time.sleep(1.0)
            toxic("stall", "upstream", "bandwidth", rate=args.stall_kbps)
            time.sleep(args.stall_s)
            untoxic("stall")
            heard = b.expect(r"\[server\] transcript role=user", 30, m)
            stats = b.expect(r"app\.ptt\.final_stats", 5, m)
            time.sleep(2)
            drops = b.grep(r"\[server\] client_(connected|disconnected|evicted)", m)
            incomplete = b.grep(r"ws\.send\.incomplete", m)
            cancel_reply()
            ok = bool(heard and not drops)
            result(
                "stall",
                ok,
                f"reconnects={len([d for d in drops if 'client_connected' in d])} "
                f"send_incomplete={len(incomplete)} heard={bool(heard)} "
                f"{(stats or '').split('final_stats ')[-1]}",
            )
        elif step == "slowdown":
            # A stream (the radio) whose downlink drops to slow_kbps for
            # slow_s. Prints, per second, what the server sent next to what
            # the board got, so a choppy stream says whose fault it is.
            if not through_proxy():
                result("slowdown", False, "the board didn't connect through the proxy")
                return
            b.send("say 4")  # "Pon la radio."
            if not b.expect(r"ws\.rx\.stats", 30, m):
                result("slowdown", False, "the radio never started")
                return
            time.sleep(4)
            m2 = b.mark()
            toxic("slow", "downstream", "bandwidth", rate=args.slow_kbps)
            time.sleep(args.slow_s)
            untoxic("slow")
            time.sleep(6)
            m3 = b.mark()
            b.send("say 5")  # "Para la radio."
            b.expect(r"ws\.rx\.model_speaking false", 20, m3)
            sent = [
                f"{field('audio_ms', x)}/{field('window_ms', x)}({field('sock_unsent', x)})"
                for x in b.grep(r"\[server\] server\.tx\.audio_stats", m2)
            ]
            got = [
                f"{field('audio_ms', x)}/{field('window_ms', x)}"
                for x in b.grep(r"ws\.rx\.stats", m2)
            ]
            gaps = b.grep(r"spk\.underrun", m2)
            drops = b.grep(r"\[server\] client_(connected|disconnected|evicted)", m2)
            print("          server sent, audio_ms/window_ms (kernel unsent):", *sent[:20])
            print("          board got,   audio_ms/window_ms:", *got[:20])
            silent = sum(int(field("silent_ms", g)) for g in gaps)
            result(
                "slowdown",
                not drops and bool(sent) and bool(got),
                f"reconnects={len(drops)} gaps={len(gaps)} silent_ms={silent} "
                f"(downlink {args.slow_kbps} KB/s for {args.slow_s:.0f} s)",
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

    # Leave it as we found it: its own server, the amp on.
    if args.wifi:
        try:
            if via_proxy:
                b.send(f"server {args.server}")
                time.sleep(3)
            if pa_off:
                b.send("pa on")
        except OSError as e:
            print(f"could not restore the board ({e}): python3 tools/dev.py cmd reboot")

    print(f"{'all passed' if fails == 0 else f'{fails} failed'}")
    return fails


if __name__ == "__main__":
    sys.exit(main())
