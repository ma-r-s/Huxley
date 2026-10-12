#!/usr/bin/env python3
"""The walkie over Wi-Fi: update it, run console commands, read its health
and crash dumps. No USB cable, no opening the case.

    python3 tools/dev.py info                 # health (GET /info)
    python3 tools/dev.py flash [--force]      # build/huxley-firmware.bin -> the board
    python3 tools/dev.py cmd selftest         # any console command (hux_console.h)
    python3 tools/dev.py log                  # follow the board's log lines
    python3 tools/dev.py coredump             # fetch + decode the last crash

Finds the board's IP in the Huxley server's log (its last `client.device`
from the firmware), or --host IP. The token is HUX_DEV_TOKEN in
main/secrets.h. Results of commands are the board's own log lines, which
it streams to the server; `cmd` and `flash` print them.

`flash` is ESP-IDF's on-demand update: this tool serves the image over
HTTP, the board downloads it with esp_https_ota, checks it, and boots it.
A new image confirms itself after 30 s; if it crashes first, the
bootloader goes back to the previous one (ESP-IDF app rollback).
"""

import argparse
import http.server
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(FW))
SERVER_LOG = os.path.join(REPO, "server", "runtime", "logs", "huxley.log")
IMAGE = os.path.join(FW, "build", "huxley-firmware.bin")
ELF = os.path.join(FW, "build", "huxley-firmware.elf")


def token() -> str:
    m = re.search(
        r'#define\s+HUX_DEV_TOKEN\s+"([^"]+)"', open(os.path.join(FW, "main", "secrets.h")).read()
    )
    if not m:
        sys.exit("no HUX_DEV_TOKEN in main/secrets.h (see secrets.example.h)")
    return m.group(1)


def server_events(since: float = 0.0):
    """The server log as dicts, newest last (it's JSON lines)."""
    try:
        with open(SERVER_LOG) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def board_ip() -> str:
    """The remote IP of the connection whose client.device says shell=firmware."""
    remote_by_conn, ip = {}, None
    for e in server_events():
        if e.get("event") == "client_connected":
            m = re.match(r"\('([\d.]+)'", str(e.get("remote", "")))
            if m:
                remote_by_conn[e.get("conn_id")] = m.group(1)
        elif e.get("event") == "client.device" and e.get("shell") == "firmware":
            ip = remote_by_conn.get(e.get("conn_id"), ip)
    if not ip:
        sys.exit("no firmware connection in the server log: is the board on? (--host IP)")
    return ip


def request(host: str, path: str, body: bytes | None = None, timeout: float = 10) -> bytes:
    req = urllib.request.Request(
        f"http://{host}{path}", data=body, headers={"X-Huxley-Token": token()}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def log_tail_from() -> int:
    try:
        return os.path.getsize(SERVER_LOG)
    except OSError:
        return 0


def board_lines(offset: int, until: float, stop=None) -> int:
    """Print the board's log lines (from the server log) until `until` or
    until a line matches `stop`; returns the new offset."""
    with open(SERVER_LOG) as f:
        f.seek(offset)
        while time.time() < until:
            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "client.huxley.firmware_log":
                text = f"{e.get('level', '?')[:1].upper()} {e.get('tag')}: {e.get('line')}"
                print("  " + text, flush=True)
                if stop and re.search(stop, text):
                    return f.tell()
            elif e.get("event") == "client.device" and e.get("shell") == "firmware":
                print(
                    f"  [server] board connected: version {e.get('version')} reset {e.get('reset')}",
                    flush=True,
                )
                if stop and re.search(stop, f"device {e.get('version')}"):
                    return f.tell()
        return f.tell()


def image_version(path: str) -> str:
    """esp_app_desc_t.version, at 0x30 in an ESP32 app image."""
    with open(path, "rb") as f:
        f.seek(0x30)
        return f.read(32).split(b"\0")[0].decode()


def local_ip_towards(host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((host, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def cmd_flash(host: str, force: bool) -> int:
    if not os.path.exists(IMAGE):
        sys.exit(f"no {IMAGE}: run idf.py build")
    version = image_version(IMAGE)
    serve_dir = os.path.dirname(IMAGE)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=serve_dir, **kw)

        def log_message(self, *a):
            pass

    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", 0), Quiet)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{local_ip_towards(host)}:{port}/{os.path.basename(IMAGE)}"
    print(f"flashing {version} ({os.path.getsize(IMAGE) // 1024} KB) to {host} from {url}")
    offset, t0 = log_tail_from(), time.time()
    request(host, "/cmd", f"ota {url}{' force' if force else ''}".encode())
    offset = board_lines(
        offset,
        t0 + 120,
        stop=rf"ota\.failed|ota\.refused|ota\.not_started|device {re.escape(version)}",
    )
    httpd.shutdown()
    info = json.loads(request(host, "/info"))
    if info.get("version") == version:
        print(
            f"done in {time.time() - t0:.0f} s: running {version} in {info.get('slot')} "
            f"({info.get('slot_state')}; it confirms itself after 30 s)"
        )
        return 0
    print(f"not updated: the board runs {info.get('version')}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("what", choices=["info", "flash", "cmd", "log", "coredump"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--host", help="the board's IP (default: from the server log)")
    ap.add_argument("--force", action="store_true", help="flash even if it runs this version")
    a = ap.parse_args()
    host = a.host or board_ip()

    if a.what == "info":
        print(json.dumps(json.loads(request(host, "/info")), indent=2))
    elif a.what == "cmd":
        line = " ".join(a.args)
        offset = log_tail_from()
        request(host, "/cmd", line.encode(), timeout=30)
        board_lines(offset, time.time() + (12 if line.startswith("say") else 4))
    elif a.what == "flash":
        return cmd_flash(host, a.force)
    elif a.what == "log":
        try:
            board_lines(log_tail_from(), float("inf"))
        except KeyboardInterrupt:
            pass
    elif a.what == "coredump":
        out = a.args[0] if a.args else "/tmp/huxley-debug/coredump.bin"
        os.makedirs(os.path.dirname(out), exist_ok=True)
        data = request(host, "/coredump", timeout=30)
        open(out, "wb").write(data)
        print(f"{len(data)} bytes -> {out}")
        if data[:4] == b"\xff\xff\xff\xff":
            print("empty: no crash recorded")
            return 0
        return subprocess.call(
            [sys.executable, "-m", "esp_coredump", "info_corefile", "-c", out, "-t", "raw", ELF]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
