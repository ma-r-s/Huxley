"""scripts/pi/admin.sh: the admin token and a `key@-` value reach the server
but never a command line (T1.52: a Wi-Fi password sent from afar).

Runs the real script against a stub HTTP server, with a `curl` shim first on
PATH that records its own argv and then runs the real curl.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "pi" / "admin.sh"
TOKEN = "tok-admin-123"
SECRET = "wifi addhex 4d6952656421 70 61$ss'w\"rd later"

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


class _Stub(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]]

    def do_GET(self) -> None:
        url = urlparse(self.path)
        type(self).seen.append(
            {
                "path": url.path,
                "query": parse_qs(url.query, keep_blank_values=True),
                "token": self.headers.get("X-Huxley-Admin"),
            }
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def stub() -> Any:
    handler = type("Handler", (_Stub,), {"seen": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, handler.seen
    server.shutdown()


def _shells() -> list[str]:
    found = [s for s in ("/bin/sh", "/bin/dash", "/usr/bin/dash") if Path(s).exists()]
    return sorted(set(found), key=found.index)


def _run(
    tmp_path: Path, port: int, shell: str, args: list[str], stdin: str = ""
) -> tuple[subprocess.CompletedProcess[str], str]:
    env_file = tmp_path / ".env"
    env_file.write_text(f'export HUXLEY_ADMIN_TOKEN="{TOKEN}"\n', encoding="utf-8")
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    argv_log = tmp_path / "curl-argv.txt"
    real_curl = shutil.which("curl")
    shim = shim_dir / "curl"
    shim.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{argv_log}"\nexec "{real_curl}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "HUXLEY_ENV": str(env_file),
        "HUXLEY_PYTHON": sys.executable,
        "HUXLEY_SERVER_PORT": str(port),
    }
    done = subprocess.run(
        [shell, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    argv = argv_log.read_text(encoding="utf-8") if argv_log.exists() else ""
    return done, argv


@pytest.mark.parametrize("shell", _shells())
def test_plain_key_value_and_the_token_off_argv(tmp_path: Path, stub: Any, shell: str) -> None:
    server, seen = stub
    done, argv = _run(tmp_path, server.server_port, shell, ["dev-cmd", "line=info"])
    assert done.returncode == 0, done.stderr
    assert seen == [{"path": "/admin/dev-cmd", "query": {"line": ["info"]}, "token": TOKEN}]
    assert TOKEN not in argv


@pytest.mark.parametrize("shell", _shells())
def test_stdin_value_reaches_the_server_and_never_argv(
    tmp_path: Path, stub: Any, shell: str
) -> None:
    server, seen = stub
    done, argv = _run(
        tmp_path,
        server.server_port,
        shell,
        ["dev-cmd", "line@-", "timeout_s=40"],
        stdin=SECRET + "\n",  # a trailing newline is not part of the value
    )
    assert done.returncode == 0, done.stderr
    assert seen == [
        {
            "path": "/admin/dev-cmd",
            "query": {"line": [SECRET], "timeout_s": ["40"]},
            "token": TOKEN,
        }
    ]
    assert "70 61$ss" not in argv
    assert TOKEN not in argv


@pytest.mark.parametrize(
    "args",
    [
        ["dev-cmd", "line@-", "other@-"],  # two stdin values
        ["dev-cmd", "line@/etc/passwd"],  # a file, never
        ["dev-cmd", "../x@-"],  # not a plain key
        ["dev-cmd", "@-"],  # no key
        ["dev-cmd", "line"],  # not key=value
    ],
)
def test_refused_arguments_send_nothing(tmp_path: Path, stub: Any, args: list[str]) -> None:
    server, seen = stub
    done, _ = _run(tmp_path, server.server_port, "/bin/sh", args, stdin="x\n")
    assert done.returncode == 2
    assert seen == []


def test_stdin_key_with_empty_stdin_sends_nothing(tmp_path: Path, stub: Any) -> None:
    server, seen = stub
    done, _ = _run(tmp_path, server.server_port, "/bin/sh", ["dev-cmd", "line@-"], stdin="")
    assert done.returncode == 2
    assert "nothing on stdin" in done.stderr
    assert seen == []
