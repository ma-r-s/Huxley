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
import contextlib
import hashlib
import http.server
import json
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
REPO = FW.parent.parent
SERVER_LOG = REPO / "server" / "runtime" / "logs" / "huxley.log"
IMAGE = FW / "build" / "huxley-firmware.bin"
ELF = FW / "build" / "huxley-firmware.elf"
# Every image `flash` sends leaves its ELF here as <version>-<sha9>.elf: a
# crash dump decodes only against the ELF of the build that crashed, and
# after a rollback that is not the one running.
ELF_ARCHIVE = FW / "build" / "elf"


def token() -> str:
    m = re.search(r'#define\s+HUX_DEV_TOKEN\s+"([^"]+)"', (FW / "main" / "secrets.h").read_text())
    if not m:
        sys.exit("no HUX_DEV_TOKEN in main/secrets.h (see secrets.example.h)")
    return m.group(1)


def server_events():
    """The server log as dicts, newest last (it's JSON lines)."""
    try:
        with SERVER_LOG.open() as f:
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
        return SERVER_LOG.stat().st_size
    except OSError:
        return 0


def board_lines(offset: int, until: float, stop=None) -> tuple[int, str]:
    """Print the board's log lines (from the server log) until `until` or
    until a line matches `stop`; returns the new offset and that line."""
    with SERVER_LOG.open() as f:
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
                    return f.tell(), text
            elif e.get("event") == "client.device" and e.get("shell") == "firmware":
                print(
                    f"  [server] board connected: version {e.get('version')} reset {e.get('reset')}",
                    flush=True,
                )
                if stop and re.search(stop, f"device {e.get('version')}"):
                    return f.tell(), f"device {e.get('version')}"
        return f.tell(), ""


def image_version(path: Path) -> str:
    """esp_app_desc_t.version, at 0x30 in an ESP32 app image."""
    with path.open("rb") as f:
        f.seek(0x30)
        return f.read(32).split(b"\0")[0].decode()


def elf_sha9(path: Path) -> str:
    """What a core dump records of its app: the ELF's SHA-256, 9 hex chars
    (CONFIG_APP_RETRIEVE_LEN_ELF_SHA)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:9]


def local_ip_towards(host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((host, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def cmd_flash(host: str, force: bool) -> int:
    if not IMAGE.exists():
        sys.exit(f"no {IMAGE}: run idf.py build")
    version = image_version(IMAGE)
    if ELF.exists():
        ELF_ARCHIVE.mkdir(exist_ok=True)
        shutil.copyfile(ELF, ELF_ARCHIVE / f"{version}-{elf_sha9(ELF)}.elf")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(IMAGE.parent), **kw)

        def log_message(self, *a):
            pass

    class Server(socketserver.ThreadingTCPServer):
        def handle_error(self, request, client_address):
            # The board hangs up mid-download when it refuses an image: not news.
            if not isinstance(sys.exc_info()[1], ConnectionError):
                super().handle_error(request, client_address)

    httpd = Server(("0.0.0.0", 0), Quiet)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{local_ip_towards(host)}:{port}/{IMAGE.name}"
    print(f"flashing {version} ({IMAGE.stat().st_size // 1024} KB) to {host} from {url}")
    offset, t0 = log_tail_from(), time.time()
    request(host, "/cmd", f"ota {url}{' force' if force else ''}".encode())
    offset, why = board_lines(
        offset,
        t0 + 120,
        stop=rf"ota\.failed|ota\.refused|ota\.not_started|device {re.escape(version)}",
    )
    httpd.shutdown()
    if re.search(r"ota\.(failed|refused|not_started)", why):
        print(f"not updated: {why}")
        return 1
    info = json.loads(request(host, "/info"))
    if info.get("version") == version:
        print(
            f"done in {time.time() - t0:.0f} s: running {version} in {info.get('slot')} "
            f"({info.get('slot_state')}; it confirms itself after 30 s)"
        )
        return 0
    print(f"not updated: the board runs {info.get('version')}")
    return 1


def cmd_coredump(host: str, out: Path, elf: Path | None) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    data = request(host, "/coredump", timeout=30)
    out.write_bytes(data)
    print(f"{len(data)} bytes -> {out}")
    if data[:4] == b"\xff\xff\xff\xff":
        print("empty: no crash recorded")
        return 0
    if elf is None:
        # The ELF whose hash the dump carries: the build that crashed.
        candidates = [e for e in [ELF, *sorted(ELF_ARCHIVE.glob("*.elf"))] if e.exists()]
        elf = next((e for e in candidates if elf_sha9(e).encode() in data), None)
        if elf is None:
            sys.exit("no ELF here matches this dump (built elsewhere?): --elf PATH")
    print(f"decoding with {elf}")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "esp_coredump",
            "info_corefile",
            "-c",
            str(out),
            "-t",
            "raw",
            str(elf),
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("what", choices=["info", "flash", "cmd", "log", "coredump"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--host", help="the board's IP (default: from the server log)")
    ap.add_argument("--force", action="store_true", help="flash even if it runs this version")
    ap.add_argument("--elf", type=Path, help="coredump: decode with this ELF")
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
        with contextlib.suppress(KeyboardInterrupt):
            board_lines(log_tail_from(), float("inf"))
    elif a.what == "coredump":
        out = Path(a.args[0] if a.args else "/tmp/huxley-debug/coredump.bin")
        return cmd_coredump(host, out, a.elf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
