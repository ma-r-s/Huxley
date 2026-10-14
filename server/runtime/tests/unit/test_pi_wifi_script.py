"""scripts/pi/wifi.sh: a Wi-Fi network added from afar reaches the walkie's
console as `wifi addhex <ssidhex> <passhex>` on stdin, and the password never
lands in a command line (T1.52, firmware F-0036).

Runs the real script with an `ssh` shim first on PATH that records each
call's argv and stdin, and stands in for the Pi's log with one walkie line.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "pi" / "wifi.sh"
SSID = "Hogar 5G ñ"
PASSWORD = "s3cret pass'w\"rd"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

SHIM = """#!{python}
import json, sys
argv = sys.argv[1:]
remote = argv[-1]
stdin = "" if "-n" in argv else sys.stdin.read()
with open({calls!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "stdin": stdin}}) + "\\n")
if "tail" in remote:
    print('"line": "wifi.add tried ssid=\\\\"Hogar\\\\" ok=1', flush=True)
elif "dev-cmd" in remote:
    print('{{"id": "abc", "output": "wifi.add queued", "truncated": false}}')
"""


def _run(tmp_path: Path, args: list[str], stdin: str = "") -> tuple[Any, list[dict[str, Any]]]:
    calls = tmp_path / "calls.jsonl"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "ssh"
    shim.write_text(SHIM.format(python=sys.executable, calls=str(calls)), encoding="utf-8")
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "HUXLEY_PI": "pi@test"}
    done = subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    recorded = (
        [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        if calls.exists()
        else []
    )
    return done, recorded


def _hex(s: str) -> str:
    return s.encode().hex()


def test_add_sends_the_line_on_stdin_and_the_password_on_no_argv(tmp_path: Path) -> None:
    # The third line answers the daytime question when the clock asks it.
    done, calls = _run(tmp_path, ["add", SSID], stdin=f"{PASSWORD}\n{PASSWORD}\ny\n")
    assert done.returncode == 0, done.stderr
    admin = [c for c in calls if "dev-cmd" in c["argv"][-1]]
    assert len(admin) == 1
    assert admin[0]["argv"][-1].endswith("admin.sh dev-cmd line@- timeout_s=20")
    assert admin[0]["stdin"] == f"wifi addhex {_hex(SSID)} {_hex(PASSWORD)}"
    for call in calls:
        flat = " ".join(call["argv"])
        assert PASSWORD not in flat
        assert _hex(PASSWORD) not in flat
    bench = [c for c in calls if "bench.jsonl" in c["argv"][-1]]
    assert len(bench) == 1
    mark = json.loads(bench[0]["stdin"])
    assert mark["start"] < mark["end"]
    assert "wifi.sh add" in mark["what"]
    assert "walkie: wifi.add tried" in done.stdout


def test_add_refuses_a_mismatched_password(tmp_path: Path) -> None:
    done, calls = _run(tmp_path, ["add", SSID], stdin=f"{PASSWORD}\nother-one\ny\n")
    assert done.returncode == 2
    assert "didn't match" in done.stderr
    assert calls == []


@pytest.mark.parametrize("password", ["short", "x" * 65])
def test_add_refuses_a_password_wifi_cannot_have(tmp_path: Path, password: str) -> None:
    done, calls = _run(tmp_path, ["add", SSID], stdin=f"{password}\n{password}\ny\n")
    assert done.returncode == 2
    assert calls == []


def test_add_refuses_a_name_over_32_bytes(tmp_path: Path) -> None:
    done, calls = _run(tmp_path, ["add", "ñ" * 17], stdin="")
    assert done.returncode == 2
    assert calls == []


def test_forget_and_list(tmp_path: Path) -> None:
    done, calls = _run(tmp_path, ["forget", SSID])
    assert done.returncode == 0, done.stderr
    assert calls[-1]["argv"][-1].endswith(f"dev-cmd 'line=wifi forget {_hex(SSID)}'")
    done, calls = _run(tmp_path, ["list"])
    assert done.returncode == 0, done.stderr
    assert calls[-1]["argv"][-1].endswith("dev-cmd 'line=wifi list'")


@pytest.mark.parametrize("args", [[], ["add"], ["drop", "x"], ["list", "x"]])
def test_usage(tmp_path: Path, args: list[str]) -> None:
    done, calls = _run(tmp_path, args)
    assert done.returncode == 2
    assert calls == []
