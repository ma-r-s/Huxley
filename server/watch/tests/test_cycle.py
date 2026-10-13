"""Whole cycles over files on disk: config, logs, DB, bench, alerts,
ping, page, and the HTTP server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from huxley_watch.__main__ import main
from huxley_watch.alerts import Sender
from huxley_watch.config import ConfigError, load_config
from huxley_watch.cycle import Watcher
from huxley_watch.web import make_server, serve_in_background
from huxley_watch.wlog import WatchLog

from .logs import (
    NOW,
    ago,
    bench_line,
    disconnected,
    flat,
    press,
    ptt_stop,
    starting,
    walkie_session,
)
from .test_alerts import FakeSender
from .test_db import make_db

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


def setup(
    tmp_path: Path,
    lines: list[str],
    watch: str = "",
    thresholds: str = "",
    tables: str = "",
    huxley: str = "",
    persona: str = "",
) -> Path:
    server = tmp_path / "runtime"
    (server / "logs").mkdir(parents=True, exist_ok=True)
    (server / "logs" / "huxley.log").write_text("".join(x + "\n" for x in lines))
    persona_dir = tmp_path / "personas" / "abuelos"
    (persona_dir / "data").mkdir(parents=True, exist_ok=True)
    (persona_dir / "persona.yaml").write_text(
        persona
        or "name: Abuelo\ntimezone: America/Bogota\nskills:\n  spotify:\n    book_hours_per_month: 15\n"
    )
    db = persona_dir / "data" / "abuelos.db"
    if not db.exists():
        make_db(db, {"spotify:book:seconds:2026-10": "3600"})
    cfg = tmp_path / "watch.toml"
    # The disk rule reads this machine's real disk, and the pid check this
    # machine's processes: keep both out of the way.
    cfg.write_text(
        '[huxley]\nserver_dir = "runtime"\npersona_dir = "personas/abuelos"\ncheck_pid = false\n'
        f"{huxley}"
        f'[watch]\nstate_dir = "state"\nsecrets = "secrets.toml"\n{watch}'
        f"[thresholds]\ndisk_pct = 101.0\n{thresholds}{tables}"
    )
    return cfg


def watcher(
    cfg_path: Path,
    sender: Sender,
    pinger: Callable[[str], str | None] | None = None,
) -> Watcher:
    cfg = load_config(cfg_path)
    log = WatchLog(cfg.log_file)
    return Watcher(cfg_path, cfg, log, sender=sender, pinger=pinger or (lambda url: None))


def ids(status: dict[str, Any]) -> list[str]:
    return [i["id"] for i in status["items"]]


OFFLINE_LOG = flat(
    starting(ago(hours=6)),
    walkie_session(ago(hours=2), ago(minutes=40), "w1"),
    disconnected(ago(minutes=40), "w1"),
)


def test_cycle_alerts_once_across_a_restart_and_clears(tmp_path: Path) -> None:
    cfg = setup(tmp_path, OFFLINE_LOG)
    s = FakeSender()
    assert ids(watcher(cfg, s).cycle(NOW)) == ["walkie.offline"]
    assert len(s.sent) == 1
    # The watcher restarts: same item, nothing sent again.
    assert ids(watcher(cfg, s).cycle(NOW + timedelta(minutes=5))) == ["walkie.offline"]
    assert len(s.sent) == 1
    # The walkie comes back.
    log = tmp_path / "runtime" / "logs" / "huxley.log"
    with log.open("a") as f:
        f.writelines(x + "\n" for x in walkie_session(NOW, NOW + timedelta(minutes=10), "w2"))
    assert ids(watcher(cfg, s).cycle(NOW + timedelta(minutes=10))) == []
    assert s.sent[-1] == "Cleared: The walkie is back online."
    page = (tmp_path / "state" / "index.html").read_text()
    assert "<h1>All good</h1>" in page
    status = json.loads((tmp_path / "state" / "status.json").read_text())
    assert status["headline"] == "All good"
    lines = [
        json.loads(x) for x in (tmp_path / "state" / "huxley-watch.log").read_text().splitlines()
    ]
    names = [x["event"] for x in lines]
    assert names.count("watch.item_opened") == 1
    assert names.count("watch.item_cleared") == 1
    assert names.count("watch.alert_sent") == 2
    assert names.count("watch.cycle") == 3


def test_quiet_hours_hold_until_morning(tmp_path: Path) -> None:
    cfg = setup(tmp_path, OFFLINE_LOG, watch='quiet_hours = "14:00-16:00"\n')
    s = FakeSender()
    w = watcher(cfg, s)
    w.cycle(NOW)  # 15:00 local: quiet
    assert s.sent == []
    w.cycle(NOW + timedelta(hours=1, minutes=5))  # 16:05
    assert len(s.sent) == 1


def test_malformed_lines_and_a_missing_db_dont_stop_a_cycle(tmp_path: Path) -> None:
    cfg = setup(tmp_path, [*OFFLINE_LOG[:3], "{garbage", *OFFLINE_LOG[3:]])
    (tmp_path / "personas" / "abuelos" / "data" / "abuelos.db").unlink()
    w = watcher(cfg, FakeSender())
    assert ids(w.cycle(NOW)) == ["walkie.offline"]
    cycle = w.log.events("watch.cycle")[-1]
    assert cycle["skipped_lines"] == 1
    assert cycle["db_ok"] is False
    assert w.log.events("watch.db_unavailable")


def test_ping_every_cycle_and_failures_logged_without_the_url(tmp_path: Path) -> None:
    cfg = setup(tmp_path, OFFLINE_LOG, watch='healthchecks_url = "https://hc-ping.com/abc"\n')
    pinged: list[str] = []

    def failing(url: str) -> str | None:
        pinged.append(url)
        return "URLError"

    w = watcher(cfg, FakeSender(), failing)
    w.cycle(NOW)
    w.cycle(NOW + timedelta(minutes=5))
    assert pinged == ["https://hc-ping.com/abc"] * 2
    failed = w.log.events("watch.ping_failed")
    assert len(failed) == 2
    assert failed[0]["host"] == "hc-ping.com"
    assert "abc" not in json.dumps(failed)


def test_a_crashing_sender_or_ping_never_skips_saving_or_the_page(tmp_path: Path) -> None:
    """Critic 4: an exception that isn't an OSError (InvalidURL,
    IncompleteRead) escaped deliver and skipped save_state and the page."""

    class Exploding(Sender):
        def __init__(self) -> None:
            super().__init__("t", "c")

        def send(self, text: str, kinds: Sequence[str] = ()) -> str | None:
            raise RuntimeError("boom with SECRET inside")

    def bad_ping(url: str) -> str | None:
        raise RuntimeError("SECRET")

    cfg = setup(tmp_path, OFFLINE_LOG, watch='healthchecks_url = "https://hc-ping.com/abc"\n')
    w = watcher(cfg, Exploding(), bad_ping)
    status = w.cycle(NOW)
    assert ids(status) == ["walkie.offline"]
    assert (tmp_path / "state" / "index.html").exists()
    saved = json.loads((tmp_path / "state" / "alerts.json").read_text())
    assert list(saved["open"]) == ["walkie.offline"]
    assert len(saved["pending"]) == 1  # kept for the next try
    assert "SECRET" not in (tmp_path / "state" / "huxley-watch.log").read_text()


def test_alerts_failing_becomes_an_item(tmp_path: Path) -> None:
    """Critic 6: deliveries failing for good were only in the log."""
    cfg = setup(tmp_path, OFFLINE_LOG)
    w = watcher(cfg, FakeSender(fail=True))
    for n in range(3):
        w.cycle(NOW + timedelta(minutes=5 * n))
    assert "watch.alerts_failing" in ids(w.cycle(NOW + timedelta(minutes=15)))
    w._sender_override = FakeSender()
    w.cycle(NOW + timedelta(minutes=20))  # delivered: the count resets
    assert "watch.alerts_failing" not in ids(w.cycle(NOW + timedelta(minutes=25)))


def test_a_stale_page_is_served_as_stopped_checking(tmp_path: Path) -> None:
    """Critic 5: a dead loop left "All good" up forever."""
    lines = flat(starting(ago(hours=6)), walkie_session(ago(hours=1), NOW, "w1"))
    w = watcher(setup(tmp_path, lines), FakeSender())
    w.cycle(NOW)
    page, body = w.current(NOW + timedelta(minutes=5))
    assert b"<h1>All good</h1>" in page
    assert json.loads(body)["stale"] is False
    page, body = w.current(NOW + timedelta(minutes=30))
    assert b"Stopped checking" in page
    assert b"All good" not in page
    assert json.loads(body)["stale"] is True


def test_bench_file_is_read_from_the_logs_dir(tmp_path: Path) -> None:
    lines = flat(
        starting(ago(hours=6)),
        walkie_session(ago(hours=2), NOW, "w1"),
        press(ago(minutes=30)),
        ptt_stop(ago(minutes=30) + timedelta(seconds=2)),
        press(ago(minutes=10)),
        ptt_stop(ago(minutes=10) + timedelta(seconds=2)),
    )
    lines.sort(key=lambda x: x.split('"timestamp": "')[1][:20])
    cfg = setup(tmp_path, lines)
    (tmp_path / "runtime" / "logs" / "bench.jsonl").write_text(
        bench_line(ago(minutes=12), ago(minutes=8), "bench.py --steps turn") + "\n"
    )
    status = watcher(cfg, FakeSender()).cycle(NOW)
    assert status["week"]["days"][-1]["presses"] == 1
    assert [w["kind"] for w in status["today"]["windows"]] == ["bench"]


def test_missing_logs_is_an_item_not_all_good(tmp_path: Path) -> None:
    cfg = setup(tmp_path, [])
    (tmp_path / "runtime" / "logs" / "huxley.log").unlink()
    (tmp_path / "runtime" / "logs").rmdir()
    assert ids(watcher(cfg, FakeSender()).cycle(NOW)) == ["watch.logs"]


def test_logs_dir_setting(tmp_path: Path) -> None:
    """Critic 10."""
    elsewhere = tmp_path / "prod-logs"
    elsewhere.mkdir()
    (elsewhere / "huxley.log").write_text("".join(x + "\n" for x in OFFLINE_LOG))
    cfg = setup(tmp_path, [], huxley=f'logs_dir = "{elsewhere}"\n')
    assert load_config(cfg).logs_dir == elsewhere
    assert ids(watcher(cfg, FakeSender()).cycle(NOW)) == ["walkie.offline"]


def test_persona_gives_timezone_and_book_budget(tmp_path: Path) -> None:
    cfg = load_config(setup(tmp_path, OFFLINE_LOG))
    assert cfg.timezone.key == "America/Bogota"
    assert cfg.book_hours_per_month == 15.0
    assert cfg.db_path == tmp_path / "personas" / "abuelos" / "data" / "abuelos.db"
    assert cfg.telegram_token is None
    assert ("watch.secrets_missing", str(tmp_path / "secrets.toml")) in cfg.notes


def test_timezone_fallback_is_noted(tmp_path: Path) -> None:
    cfg = load_config(setup(tmp_path, OFFLINE_LOG, persona="name: Abuelo\n"))
    assert cfg.timezone.key == "UTC"
    assert any(event == "watch.timezone_fallback" for event, _ in cfg.notes)


def test_secrets_readable_by_others_are_refused_and_values_stripped(tmp_path: Path) -> None:
    cfg_path = setup(tmp_path, OFFLINE_LOG)
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('telegram_bot_token = " 123:abc\\n"\ntelegram_chat_id = "42 "\n')
    secrets.chmod(0o644)
    cfg = load_config(cfg_path)
    assert cfg.telegram_token is None
    assert cfg.notes[0][0] == "watch.secrets_insecure"
    secrets.chmod(0o600)
    cfg = load_config(cfg_path)
    assert cfg.telegram_token == "123:abc"
    assert cfg.telegram_chat_id == "42"


def test_an_ntfy_topic_in_the_secrets_makes_ntfy_the_channel(tmp_path: Path) -> None:
    cfg_path = setup(tmp_path, OFFLINE_LOG)
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('ntfy_topic = " huxley-abc123 "' + chr(10))
    secrets.chmod(0o600)
    cfg = load_config(cfg_path)
    assert cfg.ntfy_url == "https://ntfy.sh/huxley-abc123"
    assert "huxley-abc123" not in repr(cfg)
    assert cfg.page_url.startswith("http://") and cfg.page_url.endswith(":8780/")


def test_balance_and_thresholds_from_the_config(tmp_path: Path) -> None:
    cfg = load_config(
        setup(
            tmp_path,
            OFFLINE_LOG,
            thresholds='offline_minutes = 30\nday_start = "08:00"\nrestarts_max = 5\n',
            tables='[openai]\nbalance_usd = 25.0\nbalance_at = "2026-10-12 09:00"\n',
        )
    )
    assert cfg.balance_usd == 25.0
    assert cfg.balance_at == datetime(2026, 10, 12, 14, 0, tzinfo=UTC)  # 09:00 Bogota
    assert cfg.thresholds.offline_minutes == 30
    assert cfg.thresholds.restarts_max == 5
    assert cfg.thresholds.day_start.hour == 8


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"thresholds": "not_a_knob = 1\n"}, "unknown threshold"),
        ({"thresholds": 'restarts_max = "many"\n'}, "ValueError used to escape"),
        ({"watch": "interval_minutes = 0\n"}, "a non-positive interval"),
        ({"watch": 'bind = "0.0.0.0:8780"\n'}, "every address"),
        ({"watch": 'bind = "[::]:8780"\n'}, "every address, v6"),
    ],
)
def test_bad_config_is_an_error(tmp_path: Path, kwargs: dict[str, str], why: str) -> None:
    with pytest.raises(ConfigError):
        load_config(setup(tmp_path, [], **kwargs))


def test_any_address_needs_the_flag(tmp_path: Path) -> None:
    cfg = load_config(
        setup(tmp_path, [], watch='bind = "0.0.0.0:8780"\nallow_any_address = true\n')
    )
    assert cfg.bind_host == "0.0.0.0"


def test_missing_and_half_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[openai]\nbalance_usd = 5\n")  # no balance_at
    with pytest.raises(ConfigError):
        load_config(bad)


def test_a_broken_edit_keeps_the_previous_config(tmp_path: Path) -> None:
    cfg_path = setup(tmp_path, OFFLINE_LOG)
    w = watcher(cfg_path, FakeSender())
    cfg_path.write_text('[thresholds]\nrestarts_max = "x"\n')  # a ValueError inside
    w.cycle(NOW)
    assert w.log.events("watch.config_invalid")
    assert w.status is not None
    cfg_path.write_text("[watch\nbroken")
    w.cycle(NOW)
    assert len(w.log.events("watch.config_invalid")) == 2


def test_once_prints_the_cards_and_tiles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC)
    lines = flat(
        starting(now - timedelta(hours=3)),
        walkie_session(now - timedelta(hours=2), now - timedelta(minutes=40), "w1"),
        disconnected(now - timedelta(minutes=40), "w1"),
    )
    # A whole day's window, so 40 minutes away is always an item.
    cfg = setup(tmp_path, lines, thresholds='day_start = "00:00"\nday_end = "23:59"\n')
    assert main(["--once", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "1 needs you"
    assert out[1] == "- Walkie offline: 40 min (Check charger)"
    assert out[2].startswith("walkie: away, seen ")


def test_http_serves_the_page_and_json(tmp_path: Path) -> None:
    w = watcher(setup(tmp_path, OFFLINE_LOG), FakeSender())
    w.cycle(datetime.now(UTC))
    server = make_server(w, "127.0.0.1", 0)
    serve_in_background(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as resp:
            assert resp.headers["Content-Type"].startswith("text/html")
            assert "The walkie" in resp.read().decode()
        with urllib.request.urlopen(base + "/status.json") as resp:
            assert json.loads(resp.read())["schema"] == 3
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/logs/huxley.log")
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


class Recording(Sender):
    def __init__(self) -> None:
        super().__init__("t", "c")
        self.sent: list[str] = []

    def send(self, text: str, kinds: Sequence[str] = ()) -> str | None:
        self.sent.append(text)
        return None


def test_a_check_run_beside_the_service_never_makes_it_repeat(tmp_path: Path) -> None:
    """2026-10-13: a `--once` run saw an item clear, sent "Cleared" and saved
    the state; the service, still holding the item in memory, would have
    sent "Cleared" again at its next cycle."""
    cfg_path = setup(tmp_path, OFFLINE_LOG)
    service = watcher(cfg_path, Recording())
    service.cycle(NOW)
    assert ids(service.status or {}) == ["walkie.offline"]
    back = flat(OFFLINE_LOG, walkie_session(ago(minutes=5), NOW, "w2"))
    (tmp_path / "runtime" / "logs" / "huxley.log").write_text("".join(x + "\n" for x in back))
    check = Recording()
    watcher(cfg_path, check).cycle(NOW)  # the one-off check: it says "Cleared"
    assert any(t.startswith("Cleared") for t in check.sent)
    sender = service.sender
    assert isinstance(sender, Recording)
    sender.sent.clear()
    service.cycle(NOW)
    assert sender.sent == []


def test_an_unreadable_state_file_keeps_the_memory(tmp_path: Path) -> None:
    sender = Recording()
    service = watcher(setup(tmp_path, OFFLINE_LOG), sender)
    service.cycle(NOW)
    assert len(sender.sent) == 1
    (tmp_path / "state" / "alerts.json").write_text("{not json")
    service.cycle(NOW)
    assert len(sender.sent) == 1  # no second "Needs you" from a forgotten item
