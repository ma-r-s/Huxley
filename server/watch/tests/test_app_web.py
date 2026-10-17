"""The phone app over HTTP (T1.46): /app.json from the cycle, /app/ and
its manifest, icons and worker with their types, all self-contained."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from huxley_watch.web import APP_FILES, app_file, make_server, serve_in_background

from .logs import ago, commit, flat, press, ptt_stop, transcript, walkie_session
from .test_alerts import FakeSender
from .test_cycle import setup, watcher

if TYPE_CHECKING:
    from collections.abc import Iterator
    from http.client import HTTPResponse
    from pathlib import Path

    from huxley_watch.cycle import Watcher


def now_log() -> list[str]:
    """The walkie online the last 20 minutes, one press with his words."""
    real = datetime.now(UTC).replace(microsecond=0)
    shift = real - ago(minutes=0)

    def moved(lines: list[str]) -> list[str]:
        out = []
        for raw in lines:
            obj = json.loads(raw)
            ts = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00")) + shift
            obj["timestamp"] = ts.isoformat().replace("+00:00", "Z")
            out.append(json.dumps(obj))
        return out

    t = ago(minutes=5)
    return moved(
        flat(
            walkie_session(ago(minutes=20), ago(seconds=10), "w1"),
            press(t),
            ptt_stop(t + timedelta(seconds=2)),
            commit(t + timedelta(seconds=2.001)),
            transcript(t + timedelta(seconds=3), "user", "¿Qué hora es?"),
        )
    )


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Watcher]]:
    w = watcher(setup(tmp_path, now_log()), FakeSender())
    w.cycle(datetime.now(UTC))
    server = make_server(w, "127.0.0.1", 0)
    serve_in_background(server)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", w
    finally:
        server.shutdown()
        server.server_close()


def get(url: str) -> tuple[HTTPResponse, bytes]:
    resp: HTTPResponse = urllib.request.urlopen(url)
    with resp:
        return resp, resp.read()


def test_app_json_is_the_cycles(served: tuple[str, Watcher]) -> None:
    base, w = served
    resp, body = get(base + "/app.json")
    assert resp.headers["Content-Type"] == "application/json; charset=utf-8"
    assert resp.headers["Cache-Control"] == "no-store"
    data = json.loads(body)
    assert data["schema"] == 1
    assert data["talks"]["requests_today"] == 1
    assert data["today"]["last_used"]["said"] == "¿Qué hora es?"
    built = w.log.events("watch.app_json_built")
    assert built and built[-1]["level"] == "debug" and built[-1]["requests_today"] == 1


def test_the_app_and_its_files_with_their_types(served: tuple[str, Watcher]) -> None:
    base, _ = served
    want = {
        "/app/": ("text/html; charset=utf-8", "no-cache"),
        "/app/index.html": ("text/html; charset=utf-8", "no-cache"),
        "/app/manifest.webmanifest": ("application/manifest+json", "no-cache"),
        "/app/sw.js": ("text/javascript; charset=utf-8", "no-cache"),
        "/app/icon-180.png": ("image/png", "max-age=604800"),
        "/app/icon-192.png": ("image/png", "max-age=604800"),
        "/app/icon-512.png": ("image/png", "max-age=604800"),
    }
    assert set(want) == set(APP_FILES)
    for path, (content_type, cache) in want.items():
        resp, body = get(base + path)
        assert resp.headers["Content-Type"] == content_type, path
        assert resp.headers["Cache-Control"] == cache, path
        assert int(resp.headers["Content-Length"]) == len(body) > 0, path
        if content_type == "image/png":
            assert body.startswith(b"\x89PNG\r\n\x1a\n")
            size = int(path.split("-")[1].split(".")[0])
            assert (int.from_bytes(body[16:20]), int.from_bytes(body[20:24])) == (size, size)


def test_app_without_the_slash_redirects(served: tuple[str, Watcher]) -> None:
    base, _ = served

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.build_opener(NoFollow).open(base + "/app")
    assert err.value.code == 301
    assert err.value.headers["Location"] == "/app/"


def test_unknown_app_paths_are_404(served: tuple[str, Watcher]) -> None:
    base, _ = served
    for path in ("/app/../logs/huxley.log", "/app/app.html", "/app/static/sw.js", "/app/x.png"):
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + path)
        assert err.value.code == 404, path


def test_the_app_knows_its_version_and_asks_for_the_servers(served: tuple[str, Watcher]) -> None:
    """iOS resumed the old app after the redesign (2026-10-17): the page
    carries its hash and reloads when /app/version.json says otherwise."""
    base, _ = served
    resp, body = get(base + "/app/version.json")
    assert resp.headers["Cache-Control"] == "no-store"
    version = json.loads(body)["version"]
    assert len(version) == 12
    _, page = get(base + "/app/")
    assert f'const VERSION = "{version}";' in page.decode()
    assert "__APP_VERSION__" not in page.decode()


def test_manifest() -> None:
    m = json.loads(app_file("manifest.webmanifest"))
    assert (m["name"], m["short_name"], m["start_url"], m["display"]) == (
        "Walkie",
        "Walkie",
        "/app/",
        "standalone",
    )
    assert m["theme_color"] and m["background_color"]
    assert m["user_preferences"]["color_scheme_dark"]["theme_color"]
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_the_page_is_self_contained() -> None:
    """Nothing from the internet: it runs on the tailnet without one."""
    page = app_file("app.html").decode()
    assert not re.search(r"https?://|(?:src|href)\s*=\s*[\"']//", page)
    assert not re.search(r"<script[^>]+src=|<link[^>]+stylesheet", page)
    assert "<!--SPRITE-->" not in page and 'id="i-walkie"' in page  # icons inlined
    for tag in (
        'name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"',
        'name="apple-mobile-web-app-capable" content="yes"',
        'name="apple-mobile-web-app-title" content="Walkie"',
        'name="apple-mobile-web-app-status-bar-style" content="default"',
        'rel="apple-touch-icon" href="icon-180.png"',
        'rel="manifest" href="manifest.webmanifest"',
        "env(safe-area-inset-bottom)",
        "prefers-color-scheme:dark",
    ):
        assert tag in page, tag


def test_the_page_never_keeps_a_days_words() -> None:
    """Conversations are fetched with no-store, and the worker caches only
    the shell and the last /app.json, never /chat/, /day/ or /audio/."""
    page = app_file("app.html").decode()
    assert re.search(r"fetch\(`\.\./chat/[^`]+`, \{cache: \"no-store\"\}\)", page)
    assert "localStorage" not in page and "indexedDB" not in page
    assert "isSecureContext" in page
    worker = app_file("sw.js").decode()
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", worker, flags=re.S)  # the comments name them
    assert "/day" not in code and "/chat" not in code and "audio" not in code
    assert "respondWith" in code and "APP_JSON" in code
    # The offline copy of /app.json is kept without his words.
    assert "fresh(event.request, DATA, APP_JSON, wordless)" in code
    body = code[code.index("async function wordless") : code.index("async function fresh")]
    for strip in ("data.today.last_used.said = null", "s.said = null", 's.detail = ""'):
        assert strip in body, strip


def test_a_failed_app_build_costs_only_the_app(tmp_path: Path, monkeypatch: Any) -> None:
    from huxley_watch import app

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("x")

    monkeypatch.setattr(app, "build", boom)
    w = watcher(setup(tmp_path, now_log()), FakeSender())
    w.cycle(datetime.now(UTC))
    data = json.loads(w.current_app())
    assert data["error"] == "KeyError" and data["schema"] == 1
    failed = [r for r in w.log.events("watch.step_failed") if r["step"] == "app"]
    assert failed and failed[0]["error"] == "KeyError"
    assert b"The walkie" in w.current()[0]  # the page was built


def test_app_json_goes_stale_with_the_loop(tmp_path: Path) -> None:
    w = watcher(setup(tmp_path, now_log()), FakeSender())
    w.cycle(datetime.now(UTC))
    assert json.loads(w.current_app())["stale"] is False
    later = datetime.now(UTC) + timedelta(minutes=30)
    assert json.loads(w.current_app(later))["stale"] is True
