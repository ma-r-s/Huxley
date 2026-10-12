"""A tiny read-only HTTP server for the page: `/` and `/status.json`,
nothing else. The standard library's threading server is enough for one
reader on the tailnet; it serves the bytes the last cycle built and
never touches the logs itself."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huxley_watch.cycle import Watcher


def make_server(watcher: Watcher, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "huxley-watch"
        sys_version = ""

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            page, status = watcher.current()
            if path in ("/", "/index.html"):
                self._send(page or b"Starting...", "text/html; charset=utf-8")
            elif path == "/status.json":
                self._send(status, "application/json; charset=utf-8")
            else:
                self._send(b"Not found", "text/plain; charset=utf-8", 404)

        def do_HEAD(self) -> None:
            self.do_GET()

        def log_message(self, format: str, *args: object) -> None:
            pass  # one reader; requests aren't events worth a line

    server_class = _V6Server if ":" in host else ThreadingHTTPServer
    server = server_class((host, port), Handler)
    server.daemon_threads = True
    return server


class _V6Server(ThreadingHTTPServer):
    """For a Tailscale IPv6 address (fd7a:...)."""

    address_family = socket.AF_INET6


def serve_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="huxley-watch-http", daemon=True)
    thread.start()
    return thread
