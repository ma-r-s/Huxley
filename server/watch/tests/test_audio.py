"""The archive over HTTP: recordings served only from under the recordings
dir (no traversal, no symlink out, Ogg only), as audio/ogg with byte
ranges; and the day's JSON."""

from __future__ import annotations

import gzip
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from huxley_watch import audio
from huxley_watch.web import make_server, serve_in_background

from .logs import flat, press, ptt_stop, segment, starting, transcript, walkie_session
from .test_alerts import FakeSender
from .test_cycle import setup, watcher

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

OGG = b"OggS\x00\x02" + bytes(range(256)) * 4  # 1030 bytes


@pytest.fixture
def rec(tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    day = root / "2026" / "10" / "12"
    day.mkdir(parents=True)
    (day / "183153-394f2a0a-001-mic.ogg").write_bytes(OGG)
    (day / "notes.txt").write_text("not audio")
    (day / "183200-394f2a0a-002-model.ogg.part").write_bytes(OGG)
    (tmp_path / "secret.ogg").write_bytes(b"outside")
    (tmp_path / "abuelos.db").write_bytes(b"SQLite format 3")
    return root


def test_a_recording_resolves(rec: Path) -> None:
    got = audio.resolve(rec, "2026/10/12/183153-394f2a0a-001-mic.ogg")
    assert got is not None and got.read_bytes() == OGG


@pytest.mark.parametrize(
    "rel",
    [
        "../secret.ogg",
        "2026/../../secret.ogg",
        "..%2fsecret.ogg",
        "%2e%2e/secret.ogg",
        "2026/10/12/%2e%2e/%2e%2e/%2e%2e/%2e%2e/secret.ogg",
        "/etc/passwd.ogg",
        "%2fetc%2fpasswd.ogg",
        "..\\secret.ogg",
        "2026//10/12/183153-394f2a0a-001-mic.ogg",
        "./2026/10/12/183153-394f2a0a-001-mic.ogg",
        "2026/10/12/183153-394f2a0a-001-mic.ogg%00.txt",
        "../abuelos.db",
        "2026/10/12/notes.txt",
        "2026/10/12/183200-394f2a0a-002-model.ogg.part",
        "2026/10/12/missing.ogg",
        "",
    ],
)
def test_anything_else_is_refused(rec: Path, rel: str) -> None:
    assert audio.resolve(rec, rel) is None


def test_a_symlink_out_of_the_recordings_dir_is_refused(rec: Path, tmp_path: Path) -> None:
    os.symlink(tmp_path / "secret.ogg", rec / "2026" / "10" / "12" / "link.ogg")
    os.symlink(tmp_path, rec / "2026" / "up")
    assert audio.resolve(rec, "2026/10/12/link.ogg") is None
    assert audio.resolve(rec, "2026/up/secret.ogg") is None


def test_a_symlink_that_stays_inside_is_fine(rec: Path) -> None:
    day = rec / "2026" / "10" / "12"
    os.symlink(day / "183153-394f2a0a-001-mic.ogg", day / "same.ogg")
    assert audio.resolve(rec, "2026/10/12/same.ogg") is not None


def test_no_recordings_dir_serves_nothing(tmp_path: Path) -> None:
    assert audio.resolve(None, "2026/10/12/x.ogg") is None
    assert audio.resolve(tmp_path / "nowhere", "2026/10/12/x.ogg") is None


def test_byte_ranges() -> None:
    assert audio.byte_range(None, 1000) is None
    assert audio.byte_range("bytes=0-", 1000) == (0, 999)
    assert audio.byte_range("bytes=100-199", 1000) == (100, 199)
    assert audio.byte_range("bytes=900-5000", 1000) == (900, 999)
    assert audio.byte_range("bytes=-100", 1000) == (900, 999)
    assert audio.byte_range("bytes=1000-", 1000) is False
    assert audio.byte_range("bytes=-0", 1000) is False
    # Ignored (RFC 9110 14.2): the whole file, a 200
    assert audio.byte_range("bytes=5-2", 1000) is None  # not a valid range
    assert audio.byte_range("bytes=0-1,5-9", 1000) is None  # several
    assert audio.byte_range("bytes=-", 1000) is None
    assert audio.byte_range("bytes=-100", 0) is None  # an empty file: never 0--1/0


# --- over HTTP ---------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path) -> Iterator[str]:
    now = datetime.now(UTC)
    t = now.replace(microsecond=0)
    lines = flat(
        starting(t - timedelta(minutes=1)),
        walkie_session(t, t, "w1"),
        press(t, turn="t1"),
        ptt_stop(t, turn="t1"),
        segment("mic", t, t, path="recordings/2026/10/12/183153-394f2a0a-001-mic.ogg"),
        transcript(t, "user", "¿Qué hora es?"),
    )
    w = watcher(setup(tmp_path, lines), FakeSender())
    root = tmp_path / "personas" / "abuelos" / "data" / "recordings" / "2026" / "10" / "12"
    root.mkdir(parents=True)
    (root / "183153-394f2a0a-001-mic.ogg").write_bytes(OGG)
    (tmp_path / "personas" / "abuelos" / "data" / "secret.ogg").write_bytes(b"x")
    w.cycle(now)
    server = make_server(w, "127.0.0.1", 0)
    serve_in_background(server)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_a_recording_is_served_as_ogg_with_ranges(served: str) -> None:
    url = served + "/audio/2026/10/12/183153-394f2a0a-001-mic.ogg"
    status, headers, body = get(url)
    assert (status, headers["Content-Type"], body) == (200, "audio/ogg", OGG)
    assert headers["Accept-Ranges"] == "bytes"
    status, headers, body = get(url, {"Range": "bytes=4-9"})
    assert (status, body, headers["Content-Range"]) == (206, OGG[4:10], f"bytes 4-9/{len(OGG)}")
    status, headers, _ = get(url, {"Range": "bytes=99999-"})
    assert status == 416 and headers["Content-Range"] == f"bytes */{len(OGG)}"


@pytest.mark.parametrize(
    "path",
    [
        "/audio/../data/secret.ogg",
        "/audio/%2e%2e/secret.ogg",
        "/audio/2026/10/12/missing.ogg",
        "/audio/",
        "/audio/%2fetc%2fpasswd",
    ],
)
def test_the_route_refuses_traversal_and_missing_files(served: str, path: str) -> None:
    status, _, body = get(served + path)
    assert status == 404 and body == b"Not found"


def test_the_day_json_is_served_and_bad_days_are_not(served: str) -> None:
    today = datetime.now(UTC).astimezone(ZoneInfo("America/Bogota")).date()
    status, headers, body = get(served + f"/day/{today.isoformat()}.json")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    data = json.loads(body)
    assert data["date"] == today.isoformat() and data["is_today"] is True
    status, headers, packed = get(
        served + f"/day/{today.isoformat()}.json", {"Accept-Encoding": "gzip"}
    )
    assert headers["Content-Encoding"] == "gzip" and json.loads(gzip.decompress(packed)) == data
    for bad in (
        "/day/2026-13-01.json",
        "/day/1999-01-01.json",
        "/day/today.json",
        "/day/../x.json",
    ):
        assert get(served + bad)[0] == 404
