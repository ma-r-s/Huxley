#!/usr/bin/env python3
"""The walkie over Wi-Fi: update it, run console commands, read its health
and crash dumps. No USB cable, no opening the case.

    python3 tools/dev.py info                 # health (GET /info)
    python3 tools/dev.py flash [--force]      # build/huxley-firmware.bin -> the board
    python3 tools/dev.py cmd selftest         # any console command (hux_console.h)
    python3 tools/dev.py log                  # follow the board's log lines
    python3 tools/dev.py coredump             # fetch + decode the last crash
    python3 tools/dev.py clips                # build/clips.bin -> the bench clips
    python3 tools/dev.py link [hours]         # audio dropouts per half hour, from the log

Finds the board's IP in the Huxley server's log (its last `client.device`
from the firmware), or --host IP. The token is HUX_DEV_TOKEN in
main/secrets.h. Results of commands are the board's own log lines, which
it streams to the server; `cmd` and `flash` print them.

`flash` is ESP-IDF's on-demand update: this tool serves the image over
HTTP, the board downloads it with esp_https_ota, checks it, and boots it.
The new image stays "pending" until this tool, after 30 s and over
Wi-Fi, tells it `ota confirm`; a restart before that (a crash, a power
cut) boots the previous one (ESP-IDF app rollback). The board refuses an
update while it's playing or listening: --force overrides that and
allows the version already running. `cmd ota rollback` goes back by hand.
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
            # 127.0.0.1: the board through tools/netbench.py's proxy.
            if m and not m.group(1).startswith("127."):
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
                # The board's level is `client_level` (the server's own
                # `level` is always "info"); older logs carry it as `level`.
                level = e.get("client_level") or e.get("level", "?")
                text = f"{level[:1].upper()} {e.get('tag')}: {e.get('line')}"
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


def info(host: str) -> dict:
    return json.loads(request(host, "/info"))


def wait_info(host: str, until: float) -> dict | None:
    """/info as soon as the board answers (it's rebooting), None at `until`."""
    while time.time() < until:
        try:
            return info(host)
        except OSError:
            time.sleep(1)
    return None


def cmd_flash(host: str, force: bool, image: Path = IMAGE) -> int:
    """Send the image, check the board runs exactly it (the ELF hash, not a
    version string: uncommitted builds share one), wait out the self-test
    and confirm it. Anything short of that and the board goes back to the
    previous image on its next restart."""
    if not image.exists():
        sys.exit(f"no {image}: run idf.py build")
    elf = image.with_suffix(".elf")
    version = image_version(image)
    sha = elf_sha9(elf) if elf.exists() else None
    if sha:
        ELF_ARCHIVE.mkdir(exist_ok=True)
        shutil.copyfile(elf, ELF_ARCHIVE / f"{version}-{sha}.elf")
    before = info(host)
    if before.get("slot_state") == "pending":
        # Left pending by a flash that didn't finish: ESP-IDF refuses a new
        # update until it's confirmed (ESP_ERR_OTA_ROLLBACK_INVALID_STATE).
        if before.get("uptime_s", 0) < 31:
            time.sleep(31 - before.get("uptime_s", 0))
        print(
            f"the running image ({before.get('version')}) was still pending: confirming it first"
        )
        request(host, "/cmd", b"ota confirm")
        time.sleep(1)
        before = info(host)

    class OnlyTheImage(http.server.SimpleHTTPRequestHandler):
        # Just the image: build/ also holds the ELF and other files with
        # the Wi-Fi password and the token in them.
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(image.parent), **kw)

        def do_GET(self):
            if self.path != f"/{image.name}":
                self.send_error(404)
                return
            super().do_GET()

        def log_message(self, *a):
            pass

    class Server(socketserver.ThreadingTCPServer):
        def handle_error(self, request, client_address):
            # The board hangs up mid-download when it refuses an image: not news.
            if not isinstance(sys.exc_info()[1], ConnectionError):
                super().handle_error(request, client_address)

    httpd = Server(("0.0.0.0", 0), OnlyTheImage)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{local_ip_towards(host)}:{port}/{image.name}"
    print(f"flashing {version} ({image.stat().st_size // 1024} KB) to {host} from {url}")
    offset, t0 = log_tail_from(), time.time()
    request(host, "/cmd", f"ota {url}{' force' if force else ''}".encode())
    # The old image reconnecting mid-download (a Wi-Fi blip) is not the
    # new one booting: stop on a device line with another version, or any
    # if both builds share a version (a forced uncommitted build).
    old = re.escape(str(before.get("version", "")))
    device = "^device " if before.get("version") == version else rf"^device (?!{old}$)"
    offset, why = board_lines(
        offset, t0 + 120, stop=rf"ota\.failed|ota\.refused|ota\.not_started|{device}"
    )
    httpd.shutdown()
    if re.search(r"ota\.(failed|refused|not_started)", why):
        print(f"not updated: {why}")
        return 1

    now = wait_info(host, time.time() + 60)
    same = (now or {}).get("elf_sha", "") == sha if sha else (now or {}).get("version") == version
    if not now or not same or now.get("slot") == before.get("slot"):
        print(f"not updated: the board runs {(now or before).get('version')}")
        if (now or {}).get("last_invalid"):
            print(f"rolled back from {now['last_invalid']}: python3 tools/dev.py coredump")
        return 1
    if now.get("slot_state") != "pending":
        print(f"running {version} in {now.get('slot')} ({now.get('slot_state')})")
        return 0

    # The self-test: it must keep running (no reset) and stay reachable
    # for HUX_OTA_SELFTEST_S, then it's told to keep itself.
    wait = max(0, 31 - int(now.get("uptime_s", 0)))
    print(f"running {version} in {now.get('slot')}, pending: confirming in {wait} s")
    time.sleep(wait)
    later = wait_info(host, time.time() + 30)
    if not later or later.get("elf_sha", later.get("version")) != now.get("elf_sha", version):
        print("not confirmed: it restarted or went quiet during the self-test")
        return 1
    if later.get("uptime_s", 0) < now.get("uptime_s", 0):
        print("not confirmed: it restarted during the self-test")
        return 1
    request(host, "/cmd", b"ota confirm")
    for _ in range(10):
        time.sleep(0.5)
        done = info(host)
        if done.get("slot_state") == "valid":
            print(f"done in {time.time() - t0:.0f} s: {version} confirmed in {done.get('slot')}")
            return 0
    print(f"not confirmed: slot_state={done.get('slot_state')} (python3 tools/dev.py log)")
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


def all_server_events():
    """Every run's log (the server keeps 10 archives, plus logs/keep/),
    oldest first, each event once."""
    logs = SERVER_LOG.parent
    files = [*logs.glob("keep/*.log"), *logs.glob("huxley_*.log"), SERVER_LOG]
    seen = ""
    for path in sorted(files, key=lambda p: p.stat().st_mtime if p.exists() else 0):
        try:
            with path.open() as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    ts = e.get("timestamp", "")
                    if ts and ts <= seen:
                        continue  # a copy already read (keep/ duplicates an archive)
                    seen = ts or seen
                    yield e
        except OSError:
            continue


def cmd_link(hours: float) -> int:
    """What the speaker did, per half hour of real use, from every server
    log: seconds of audio played (the board's spk.alive chunk counter),
    seconds of gaps heard (its spk.underrun silences, rebuffering included)
    and the gaps' share of the playing time; median rssi; the server's
    largest send backlog. Not "how much arrived per second": replies come
    in bursts faster than real time and play from the buffer (F-0022)."""
    import statistics

    since = time.time() - hours * 3600
    buckets: dict[str, dict] = {}
    last_chunks: int | None = None
    for e in all_server_events():
        ts = e.get("timestamp", "")
        try:
            t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except ValueError:
            continue
        line = str(e.get("line", ""))
        chunks = None
        if e.get("event") == "client.huxley.firmware_log" and line.startswith("spk.alive"):
            m = re.search(r"\baudio=(\d+)", line)
            chunks = int(m.group(1)) if m else None
        if t < since:
            last_chunks = chunks if chunks is not None else last_chunks
            continue
        local = time.localtime(t)
        key = time.strftime("%m-%d %H:", local) + ("00" if local.tm_min < 30 else "30")
        b = buckets.setdefault(
            key, {"played": 0.0, "gap": 0.0, "gaps": 0, "rssi": [], "unsent": 0}
        )
        if chunks is not None:
            if last_chunks is not None and chunks >= last_chunks:
                b["played"] += (chunks - last_chunks) * 0.02  # 20 ms chunks
            last_chunks = chunks  # a restart resets the counter: skip that step
        elif e.get("event") == "client.huxley.firmware_log" and line.startswith("spk.underrun"):
            m = re.search(r"silent_ms=(\d+)", line)
            b["gap"] += int(m.group(1)) / 1000 if m else 0
            b["gaps"] += 1
        elif e.get("event") == "client.huxley.firmware_log" and line.startswith("ws.rx.stats"):
            m = re.search(r"rssi=(-?\d+)", line)
            if m:
                b["rssi"].append(int(m.group(1)))
        elif e.get("event") == "server.tx.audio_stats":
            b["unsent"] = max(b["unsent"], int(e.get("sock_unsent") or 0))
    print("half hour    played s  gaps  gap s  gap share  rssi  max server backlog")
    for key in sorted(buckets):
        b = buckets[key]
        if b["played"] < 30:
            continue  # under 30 s played: nothing to say
        share = 100 * b["gap"] / (b["played"] + b["gap"])
        rssi = statistics.median(b["rssi"]) if b["rssi"] else "?"
        print(
            f"{key}  {b['played']:8.0f}  {b['gaps']:4d}  {b['gap']:5.0f}  {share:7.1f} %"
            f"  {rssi:>4}  {b['unsent'] // 1024} KB"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("what", choices=["info", "flash", "cmd", "log", "coredump", "clips", "link"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--host", help="the board's IP (default: from the server log)")
    ap.add_argument("--force", action="store_true", help="flash even if it runs this version")
    ap.add_argument("--elf", type=Path, help="coredump: decode with this ELF")
    ap.add_argument(
        "--image", type=Path, default=IMAGE, help="flash: this .bin (its .elf next to it)"
    )
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
        return cmd_flash(host, a.force, a.image)
    elif a.what == "log":
        with contextlib.suppress(KeyboardInterrupt):
            board_lines(log_tail_from(), float("inf"))
    elif a.what == "link":
        return cmd_link(float(a.args[0]) if a.args else 24)
    elif a.what == "clips":
        # tools/clips.py's output, into the storage partition (`say <n>`).
        clips = FW / "build" / "clips.bin"
        if not clips.exists():
            sys.exit("no build/clips.bin: python3 tools/clips.py")
        data = clips.read_bytes()
        offset = log_tail_from()
        print(request(host, "/clips", data, timeout=120).decode().strip(), len(data), "bytes")
        board_lines(offset, time.time() + 3)
    elif a.what == "coredump":
        out = Path(a.args[0] if a.args else "/tmp/huxley-debug/coredump.bin")
        return cmd_coredump(host, out, a.elf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
