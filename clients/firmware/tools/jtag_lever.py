#!/usr/bin/env python3
"""Press the HX-1 case's talk lever electrically, over JTAG.

The lever closes GPIO7 to GND. This flips GPIO7's pull-up to a pull-down
in the IO_MUX register for `hold` seconds, so the firmware reads exactly
what the lever would give it (`button.talk press src=lever`). No hand, no
case. Needs OpenOCD started in its no-reset mode (see tools/README.md):

    openocd -c "set ESP_FLASH_SIZE 0" -f board/esp32s3-builtin.cfg &
    python3 tools/jtag_lever.py 0.5
"""

import re
import socket
import sys
import time

IO_MUX_GPIO7 = 0x60009020  # soc/esp32s3 io_mux_reg.h: REG_IO_MUX_BASE + 0x20
FUN_PD, FUN_PU = 1 << 7, 1 << 8


def main() -> None:
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    s = socket.create_connection(("localhost", 4444))  # OpenOCD telnet
    s.settimeout(2)

    def cmd(c: str) -> str:
        s.sendall((c + "\n").encode())
        time.sleep(0.15)
        out = b""
        try:
            while True:
                d = s.recv(4096)
                out += d
                if len(d) < 4096:
                    break
        except OSError:
            pass
        return out.decode(errors="replace")

    cmd("")
    cmd("halt")  # a few ms: memory writes need the cores halted
    m = re.search(rf"0x{IO_MUX_GPIO7:08x}: ([0-9a-f]+)", cmd(f"mdw 0x{IO_MUX_GPIO7:x}"))
    if not m:
        cmd("resume")
        sys.exit("could not read IO_MUX_GPIO7: is OpenOCD attached?")
    v = int(m.group(1), 16)
    print(f"IO_MUX_GPIO7 = 0x{v:08x} (pull-up={v >> 8 & 1} pull-down={v >> 7 & 1})")
    cmd(f"mww 0x{IO_MUX_GPIO7:x} 0x{(v & ~FUN_PU) | FUN_PD:x}")
    cmd("resume")
    t0 = time.time()
    time.sleep(hold)
    cmd("halt")
    cmd(f"mww 0x{IO_MUX_GPIO7:x} 0x{v:x}")
    cmd("resume")
    print(f"lever held {time.time() - t0:.2f} s, register restored")


if __name__ == "__main__":
    main()
