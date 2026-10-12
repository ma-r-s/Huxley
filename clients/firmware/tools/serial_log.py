#!/usr/bin/env python3
"""Log the walkie's USB console without ever resetting it.

    python tools/serial_log.py [OUTFILE] [--cmd FIFO]

Opens the first /dev/cu.usbmodem* with DTR and RTS both high and never
changes them: on the S3's USB-Serial/JTAG a reset needs RTS high with DTR
low, and a reset at the wrong moment can leave the chip in download mode
(tools/flash.sh). Lines get a host timestamp; default OUTFILE is
/tmp/huxley-debug/serial-<time>.log. Lines written to the named pipe FIFO
go to the board's console (`echo info > FIFO`). Follows the board across
unplugs and other USB sockets. Ctrl-C to stop.
"""

import datetime
import glob
import os
import sys
import time

import serial


def stamp() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def open_port() -> serial.Serial:
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise OSError("no port")
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = ports[0], 115200, 0.05
    s.dtr = s.rts = True  # held high, never changed: no reset
    s.open()
    return s


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fifo = sys.argv[sys.argv.index("--cmd") + 1] if "--cmd" in sys.argv else None
    if fifo in args:
        args.remove(fifo)
    os.makedirs("/tmp/huxley-debug", exist_ok=True)
    out = args[0] if args else time.strftime("/tmp/huxley-debug/serial-%Y%m%d-%H%M%S.log")
    if fifo and not os.path.exists(fifo):
        os.mkfifo(fifo)
    fifo_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK) if fifo else None
    f = open(out, "a", buffering=1)
    print(f"logging to {out}", file=sys.stderr)
    s, buf, pending = None, b"", b""
    while True:
        if s is None:
            try:
                s = open_port()
                f.write(f"{stamp()} [serial_log] opened {s.port}\n")
            except (OSError, serial.SerialException):
                time.sleep(0.3)
                continue
        if fifo_fd is not None:
            try:
                chunk = os.read(fifo_fd, 1024)
            except BlockingIOError:
                chunk = None
            if chunk:
                pending += chunk
                while b"\n" in pending:
                    cmd, pending = pending.split(b"\n", 1)
                    s.write(cmd.strip() + b"\n")
                    f.write(f"{stamp()} [serial_log] >> {cmd.decode(errors='replace').strip()}\n")
            elif chunk == b"":  # writers closed: reopen to see the next one
                os.close(fifo_fd)
                fifo_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            buf += s.read(4096)
        except (OSError, serial.SerialException) as e:
            f.write(f"{stamp()} [serial_log] lost the port ({e}), waiting for it\n")
            s = None
            continue
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            f.write(f"{stamp()} {line.decode('utf-8', 'replace').rstrip()}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
