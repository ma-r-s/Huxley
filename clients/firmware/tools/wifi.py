#!/usr/bin/env python3
"""Add the walkie's Wi-Fi on site, over USB (F-0027's Wi-Fi list).

    . ~/esp/esp-idf/export.sh            # pyserial
    python3 clients/firmware/tools/wifi.py add      # pick a network, type its password
    python3 clients/firmware/tools/wifi.py scan     # what the walkie hears from where it sits
    python3 clients/firmware/tools/wifi.py list     # what it knows (never passwords) and its MAC
    python3 clients/firmware/tools/wifi.py forget   # drop one added on site

Run it in your own terminal: the password is asked with no echo and goes
only to the walkie (as hex on its USB console; the walkie never logs it).
Nothing is written to disk. The port is opened with DTR and RTS held high
and never changed, so the board is never reset (tools/serial_log.py,
F-0020).

`add` then checks the server from this laptop on the same network, restarts
the walkie and waits for it to come back on its own, the way it will after
a power cut.
"""

from __future__ import annotations

import getpass
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

import serial

SERVER = "https://walkie.ma-r-s.com/"


def open_port(wait_s: float = 0) -> serial.Serial:
    end = time.time() + wait_s
    while True:
        ports = sorted(str(p) for p in pathlib.Path("/dev").glob("cu.usbmodem*"))
        if ports:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = ports[0], 115200, 0.05
            s.dtr = s.rts = True  # held high, never changed: no reset
            s.exclusive = True  # a running serial_log.py can't take the lines
            try:
                s.open()
                return s
            except (OSError, serial.SerialException):
                if time.time() > end:
                    raise
        elif time.time() > end:
            sys.exit("No walkie on USB: plug it into this laptop with a data cable.")
        time.sleep(0.5)


class Console:
    def __init__(self) -> None:
        self.port = open_port()
        self.buf = b""

    def send(self, line: str) -> None:
        self.port.write(line.encode() + b"\n")

    def drain(self) -> None:
        """Forget what the board said so far (old lines must not match)."""
        self.port.reset_input_buffer()
        self.buf = b""

    def lines(self, seconds: float):
        """Board lines for `seconds` (ANSI colours stripped). Survives the USB
        port going away and coming back (a restart)."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.buf += self.port.read(4096)
            except (OSError, serial.SerialException):
                self.port.close()
                self.port = open_port(wait_s=max(1.0, end - time.time()))
                continue
            while b"\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n", 1)
                yield re.sub(r"\x1b\[[0-9;]*m", "", raw.decode(errors="replace")).strip()

    def wait(self, pattern: str, seconds: float) -> re.Match[str] | None:
        rx = re.compile(pattern)
        for line in self.lines(seconds):
            m = rx.search(line)
            if m:
                return m
        return None


def scan(c: Console) -> list[tuple[str, int, str]]:
    """(name, best signal, security) for every network the walkie hears."""
    seen: dict[str, tuple[int, str]] = {}
    for _ in range(2):  # a scan can fail while the walkie is mid-connect: once more
        c.drain()
        c.send("wifiscan")
        for line in c.lines(8):
            m = re.search(r'wifi\.scan\.ap .*rssi=(-?\d+) auth=(\w+) known=\d ssid="(.*)"', line)
            if m and m.group(3):
                rssi, auth, name = int(m.group(1)), m.group(2), m.group(3)
                if name not in seen or rssi > seen[name][0]:
                    seen[name] = (rssi, auth)
        if seen:
            break
    return sorted(((n, r, a) for n, (r, a) in seen.items()), key=lambda x: -x[1])


def bars(rssi: int) -> str:
    return "good" if rssi >= -60 else "fair" if rssi >= -72 else "weak"


def show_scan(nets: list[tuple[str, int, str]]) -> None:
    if not nets:
        print("The walkie hears no network here (it uses 2.4 GHz only).")
        return
    print("The walkie hears (2.4 GHz only; a 5 GHz-only network won't show):")
    for i, (name, rssi, auth) in enumerate(nets, 1):
        print(f"  {i:2}. {name:32}  {bars(rssi):4} ({rssi} dBm)  {auth}")


def server_reachable() -> str:
    try:
        urllib.request.urlopen(SERVER, timeout=10)
        return "yes"
    except urllib.error.HTTPError:
        return "yes"  # any HTTP answer: the tunnel and the server are there
    except (urllib.error.URLError, OSError) as e:
        return f"no ({e})"


WHY = {
    "lengths": "the name must be 1-32 characters and the password 8-64",
    "busy": "another network is being added; wait a minute",
    "known": "the walkie already knows that name: `forget` it first to change its password",
    "full": "the walkie already has 4 networks added: `forget` one first",
    "bad_hex": "the tool sent something odd; try again",
    "config": "the walkie's Wi-Fi refused that name or password",
    "nvs": "the walkie couldn't save it (its storage); nothing changed",
    "wrong_password": "the password was refused (three times in a row)",
    "auth_failed": "the network refused the walkie (often a wrong password, or a weak signal)",
    "security_too_weak": "the network is open or uses old WPA (the walkie needs WPA2 or WPA3)",
    "enterprise_login": "the network needs a user login (enterprise), which the walkie can't do",
    "not_heard": "the walkie doesn't hear that network from here",
    "no_ip": "the walkie joined but the network gave it no address",
}


def show_list(c: Console) -> None:
    c.drain()
    c.send("wifi list")
    for line in c.lines(3):
        m = re.search(r"(wifi\.list.*)", line)
        if m:
            print("  " + m.group(1))


def add(c: Console) -> None:
    if not sys.stdin.isatty():
        sys.exit("Run this in your own terminal: it asks for the password with no echo.")
    print("The walkie (its MAC address, if the place's IT asks for it):")
    show_list(c)
    print("Asking the walkie what it hears...")
    nets = scan(c)
    show_scan(nets)
    choice = input("Network number, or type its name (hidden networks too): ").strip()
    name = (
        nets[int(choice) - 1][0] if choice.isdigit() and 0 < int(choice) <= len(nets) else choice
    )
    if not name:
        sys.exit("No network chosen.")
    password = getpass.getpass(f'Password for "{name}" (not shown): ')
    line = f"wifi addhex {name.encode().hex()} {password.encode().hex()}"
    password = ""  # the hex goes to the walkie only
    if len(line) > 255:
        sys.exit("Name and password are too long for the walkie (32 and 64 characters).")
    print(f'Trying "{name}"...')
    c.drain()
    c.send(line)
    line = ""
    m = c.wait(r"wifi\.add (refused|failed|saved)(?: .*why=(\w+))?", 60)
    if m is None:
        sys.exit("No answer from the walkie in 60 s. Run `list` to see what it did.")
    if m.group(1) != "saved":
        why = m.group(2) or ""
        sys.exit(f"Not added: {WHY.get(why, why)}. It stays on its previous network.")
    print("Joined and saved: the network gave the walkie an address.")
    last_error = ""
    reached = False
    for line in c.lines(90):
        if "ws.error" in line:
            last_error = line[line.index("ws.error") :]
        if "wifi.add server_reached" in line:
            reached = True
            break
        if "wifi.add no_server" in line:
            break
    if reached:
        print("The walkie reached the server through it.")
    else:
        print("The walkie did NOT reach the server through it yet (it keeps the network).")
        if last_error:
            print(f"  Its last connection error: {last_error}")
    print(f"From this laptop (on the same network?), is {SERVER} reachable? {server_reachable()}")
    print("Restarting the walkie to check it comes back on its own (up to 4 minutes)...")
    c.drain()
    c.send("reboot")
    if c.wait(r"console\.reboot", 10) is None:
        sys.exit("The walkie didn't restart. Run the tool again.")
    joined = ""
    came_back = False
    for line in c.lines(240):
        j = re.search(r'wifi\.(?:init|join) ssid="([^"]*)"', line)
        if j:
            joined = j.group(1)
        if re.search(r"app\.device_hello sent=1|state WAITING_HELLO -> READY", line):
            came_back = True
            break
    if came_back and joined == name:
        print(
            f'Done: after a restart the walkie is back on the server through "{name}", by itself.'
        )
    elif came_back:
        print(
            f'After the restart it came back through "{joined}", NOT "{name}". '
            f'"{name}" is not carrying it yet: check the lines above before leaving.'
        )
    else:
        print(f'After the restart it is not back on the server (last network tried: "{joined}").')
    print("What the walkie knows now:")
    show_list(c)


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "add"
    c = Console()
    if what == "scan":
        show_scan(scan(c))
    elif what == "list":
        show_list(c)
    elif what == "forget":
        name = input("Name of the network to forget: ").strip()
        c.send(f"wifi forget {name.encode().hex()}")
        m = c.wait(r"wifi\.forget (refused|ssid=.*)", 10)
        print(m.group(0) if m else "No answer from the walkie.")
    elif what == "add":
        add(c)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
