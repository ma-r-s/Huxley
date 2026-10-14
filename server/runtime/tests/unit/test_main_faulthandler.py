"""A native crash leaves a Python traceback in the journal (2026-10-14: the
Telegram call library segfaulted with nothing logged but exit 139)."""

from __future__ import annotations

import subprocess
import sys


def test_main_enables_the_fault_handler() -> None:
    code = (
        "import faulthandler, sys\n"
        "import huxley.__main__ as m\n"
        "sys.argv = ['huxley']\n"
        "class Stop(Exception): pass\n"
        "def stop(*a, **k): raise Stop\n"
        "m.Settings = stop\n"
        "try:\n"
        "    m.main()\n"
        "except (Stop, SystemExit):\n"
        "    pass\n"
        "print(faulthandler.is_enabled())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.stdout.strip().endswith("True"), out.stderr
