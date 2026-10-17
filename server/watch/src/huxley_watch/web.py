"""A tiny read-only HTTP server for the page: `/`, `/status.json`, the
archive's days (`/day/YYYY-MM-DD.json`), a day as a conversation for the
phone app (`/chat/YYYY-MM-DD.json` or `/chat/today.json`) and its recordings
(`/audio/<path under the recordings dir>`), and the phone app (T1.46):
`/app.json` and `/app/` with its manifest, icons and service worker,
nothing else. The standard library's threading server is enough for one
reader on the tailnet; the page, status and app data are the bytes the
last cycle built, a day is built from that day's logs when asked
(dayview.py), and a recording is streamed from disk (audio.py)."""

from __future__ import annotations

import contextlib
import functools
import gzip
import hashlib
import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import TYPE_CHECKING, BinaryIO

from huxley_watch import audio
from huxley_watch.icons import sprite

if TYPE_CHECKING:
    from io import BufferedIOBase

    from huxley_watch.cycle import Watcher

_DAY = re.compile(r"^/day/(\d{4}-\d{2}-\d{2})\.json$")
_CHAT = re.compile(r"^/chat/(\d{4}-\d{2}-\d{2}|today)\.json$")
# The phone app's files (static/, package data): path -> (file, type, cache).
# Its HTML, manifest and worker are checked on every open (an update reaches
# a home-screen app at once); the icons change only with a new install.
_HTML = "text/html; charset=utf-8"
APP_FILES = {
    "/app/": ("app.html", _HTML, "no-cache"),
    "/app/index.html": ("app.html", _HTML, "no-cache"),
    "/app/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json", "no-cache"),
    "/app/sw.js": ("sw.js", "text/javascript; charset=utf-8", "no-cache"),
    "/app/icon-180.png": ("icon-180.png", "image/png", "max-age=604800"),
    "/app/icon-192.png": ("icon-192.png", "image/png", "max-age=604800"),
    "/app/icon-512.png": ("icon-512.png", "image/png", "max-age=604800"),
}
SPRITE_MARK = b"<!--SPRITE-->"
VERSION_MARK = b"__APP_VERSION__"


@functools.cache
def app_file(name: str) -> bytes:
    """A file of the phone app, read once; the HTML gets the icon sprite
    (the same symbols as the page, icons.py) and its own version."""
    body = (resources.files("huxley_watch") / "static" / name).read_bytes()
    if name == "app.html":
        body = body.replace(SPRITE_MARK, sprite().encode("utf-8"))
        body = body.replace(VERSION_MARK, app_version().encode("ascii"))
    return body


@functools.cache
def app_version() -> str:
    """The app's version: its HTML's hash. A home-screen app on iOS resumes
    the page it had instead of loading the new one (2026-10-17: the old app
    after the redesign); the page asks /app/version.json when it comes
    back and reloads itself when this changed."""
    raw = (resources.files("huxley_watch") / "static" / "app.html").read_bytes()
    return hashlib.sha256(raw).hexdigest()[:12]


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

        def _redirect(self, to: str) -> None:
            self._headers("text/plain; charset=utf-8", 0, 301)
            self.send_header("Location", to)
            self.end_headers()

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/app.json":
                self._send(watcher.current_app(), "application/json; charset=utf-8")
            elif path == "/app":
                self._redirect("/app/")
            elif path == "/app/version.json":
                ver = json.dumps({"version": app_version()}).encode("utf-8")
                self._send(ver, "application/json; charset=utf-8")
            elif path in APP_FILES:
                name, content_type, cache = APP_FILES[path]
                shell = app_file(name)
                self._headers(content_type, len(shell), cache=cache)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(shell)
            elif path in ("/", "/index.html"):
                page, _ = watcher.current()
                self._send(page or b"Starting...", "text/html; charset=utf-8")
            elif path == "/status.json":
                _, status = watcher.current()
                self._send(status, "application/json; charset=utf-8")
            elif m := _CHAT.match(path):
                body = watcher.chat(m.group(1))
                if body is None:
                    self._not_found()
                else:
                    self._send(body, "application/json; charset=utf-8")
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
