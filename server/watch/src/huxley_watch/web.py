"""A tiny read-only HTTP server for the page: `/`, `/status.json`, the
archive's days (`/day/YYYY-MM-DD.json`) and its recordings
(`/audio/<path under the recordings dir>`), nothing else. The standard
library's threading server is enough for one reader on the tailnet; the
page and status are the bytes the last cycle built, a day is built from
that day's logs when asked (dayview.py), and a recording is streamed from
disk (audio.py)."""

from __future__ import annotations

import contextlib
import gzip
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, BinaryIO

from huxley_watch import audio

if TYPE_CHECKING:
    from io import BufferedIOBase

    from huxley_watch.cycle import Watcher

_DAY = re.compile(r"^/day/(\d{4}-\d{2}-\d{2})\.json$")


def make_server(watcher: Watcher, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "huxley-watch"
        sys_version = ""

        def _headers(
            self, content_type: str, length: int, status: int = 200, cache: str = "no-store"
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self._headers(content_type, len(body), status)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _not_found(self) -> None:
            self._send(b"Not found", "text/plain; charset=utf-8", 404)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                page, _ = watcher.current()
                self._send(page or b"Starting...", "text/html; charset=utf-8")
            elif path == "/status.json":
                _, status = watcher.current()
                self._send(status, "application/json; charset=utf-8")
            elif m := _DAY.match(path):
                body = watcher.day(m.group(1))
                if body is None:
                    self._not_found()
                elif "gzip" in (self.headers.get("Accept-Encoding") or ""):
                    # A busy day is most of a MB of transcripts and markup;
                    # about a tenth gzipped, over the tailnet to a phone.
                    packed = gzip.compress(body, 6)
                    self._headers("application/json; charset=utf-8", len(packed))
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Vary", "Accept-Encoding")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(packed)
                else:
                    self._send(body, "application/json; charset=utf-8")
            elif path.startswith("/audio/"):
                self._audio(path.removeprefix("/audio/"))
            else:
                self._not_found()

        def _audio(self, rel: str) -> None:
            file = audio.resolve(watcher.cfg.recordings_dir, rel)
            if file is None:
                self._not_found()
                return
            try:
                f = file.open("rb")
            except OSError:
                self._not_found()
                return
            with f:
                size = file.stat().st_size
                wanted = audio.byte_range(self.headers.get("Range"), size)
                if wanted is False:
                    self._headers("text/plain; charset=utf-8", 0, 416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                first, last = wanted if isinstance(wanted, tuple) else (0, size - 1)
                length = max(0, last - first + 1)
                # A recording never changes once written (renamed from .part).
                self._headers(
                    audio.CONTENT_TYPE,
                    length,
                    206 if isinstance(wanted, tuple) else 200,
                    cache="private, max-age=86400",
                )
                self.send_header("Accept-Ranges", "bytes")
                if isinstance(wanted, tuple):
                    self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
                self.end_headers()
                if self.command == "HEAD" or length == 0:
                    return
                f.seek(first)
                # The player may move on mid-file (a seek, a pause, another file).
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    _copy(f, self.wfile, length)

        def do_HEAD(self) -> None:
            self.do_GET()

        def log_message(self, format: str, *args: object) -> None:
            pass  # one reader; requests aren't events worth a line

    server_class = _V6Server if ":" in host else ThreadingHTTPServer
    server = server_class((host, port), Handler)
    server.daemon_threads = True
    return server


def _copy(src: BinaryIO, dst: BinaryIO | BufferedIOBase, length: int) -> None:
    """`length` bytes from src to dst, in chunks."""
    left = length
    while left > 0:
        chunk = src.read(min(65536, left))
        if not chunk:
            break
        dst.write(chunk)
        left -= len(chunk)


class _V6Server(ThreadingHTTPServer):
    """For a Tailscale IPv6 address (fd7a:...)."""

    address_family = socket.AF_INET6


def serve_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="huxley-watch-http", daemon=True)
    thread.start()
    return thread
