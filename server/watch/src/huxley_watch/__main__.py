"""huxley-watch: run the caregiver's view.

uv run --package huxley-watch huxley-watch                 # loop + page on 127.0.0.1:8780
uv run --package huxley-watch huxley-watch --once          # one cycle, print the items, exit
uv run --package huxley-watch huxley-watch --config FILE   # see watch.example.toml
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from huxley_watch.config import ConfigError, find_config, load_config
from huxley_watch.cycle import Watcher, health_url
from huxley_watch.facts import server_client_connected
from huxley_watch.web import make_server, serve_in_background
from huxley_watch.wlog import WatchLog

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType


def once_summary(status: dict[str, Any]) -> str:
    """What --once prints: the headline, the cards, the tiles, one line each."""
    lines = [status["headline"]]
    lines += [f"- {i['label']}: {i['big']} ({i['chip']})" for i in status["items"]]
    t = status["tiles"]
    walkie = t["walkie"]
    if walkie["online"]:
        lines.append(f"walkie: online since {walkie['since']}")
    elif walkie["online"] is None:
        lines.append("walkie: never seen")
    else:
        lines.append(f"walkie: away, seen {walkie['last_seen']}")
    rssi = t["wifi"]["rssi"]
    lines.append(f"wifi: {rssi} dBm, {t['wifi']['bars']} bars" if rssi is not None else "wifi: -")
    gap = t["audio"]["gap_pct"]
    window = f"last {t['audio']['window_h']:g} h"
    lines.append(
        f"audio gaps, {window}: {gap} %" if gap is not None else f"audio gaps, {window}: -"
    )
    sv = t["server"]
    state = {True: "up", False: "down", None: "?"}[sv["running"]]
    extra = ", ".join(
        x for x in (sv["up"], f"firmware {sv['firmware']}" if sv["firmware"] else "") if x
    )
    lines.append(f"server: {state}" + (f" ({extra})" if extra else ""))
    lines.append(f"openai today: ${t['openai']['today_usd']:.2f}")
    c = status.get("credit") or {}
    if c.get("mode") == "budget":
        ends = f"hits $0 {c['runs_out']}" if c.get("runs_out") else "lasts the month"
        lines.append(
            f"openai {c['month']}: ${c['left_usd']:.2f} of ${c['limit_usd']:g} left, "
            f"${c['spent_usd']:.2f} used ({c['limit_source']} limit, {c['source']}), {ends}"
        )
    elif c.get("month"):
        lines.append(f"openai {c['month']}: ${c['spent_usd']:.2f} used, no limit set")
    week = ", ".join(
        f"{d['label']} {sum(d['hours'].values()):.1f} h/{d['presses']}p"
        if d["has_logs"]
        else f"{d['label']} no logs"
        for d in status["week"]["days"]
    )
    lines.append(f"week: {week}")
    calls = ", ".join(f"{p['name']} {p['count']}" for p in status["calls"]["people"])
    lines.append(f"calls, {status['calls']['days']} days: {status['calls']['total']} ({calls})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="huxley-watch", description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, help="watch.toml (default: ~/.config/huxley-watch/)")
    ap.add_argument("--once", action="store_true", help="one cycle, print what needs you, exit")
    args = ap.parse_args(argv)

    path = find_config(args.config)
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        print(f"huxley-watch: {exc}", file=sys.stderr)
        return 2
    log = WatchLog(cfg.log_file)
    log.info(
        "watch.starting",
        config=str(path) if path else None,
        logs=str(cfg.logs_dir),
        db=str(cfg.db_path),
        timezone=cfg.timezone.key,
        once=args.once,
    )
    watcher = Watcher(path, cfg, log)

    if args.once:
        status = watcher.cycle()
        print(once_summary(status))
        return 0

    stop = threading.Event()

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("watch.stopping", signal=signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        server = make_server(watcher, cfg.bind_host, cfg.bind_port)
    except OSError as exc:
        log.error("watch.bind_failed", host=cfg.bind_host, port=cfg.bind_port, error=str(exc))
        print(
            f"huxley-watch: can't listen on {cfg.bind_host}:{cfg.bind_port}: {exc}",
            file=sys.stderr,
        )
        return 1
    serve_in_background(server)
    log.info("watch.serving", url=f"http://{cfg.bind_host}:{cfg.bind_port}/")
    while not stop.is_set():
        try:
            watcher.cycle()
        except Exception as exc:  # a bad cycle must not end the watching
            # Type and where, not the message: it could quote a secret.
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            log.error(
                "watch.cycle_failed",
                error=type(exc).__name__,
                at=f"{Path(frame.filename).name}:{frame.lineno}",
            )
        wait_next(stop, watcher)
    server.shutdown()
    log.info("watch.stopped")
    return 0


# While the walkie is marked offline, how often to ask the server whether it
# came back.
QUICK_S = 15.0


def wait_next(
    stop: threading.Event,
    watcher: Watcher,
    connected: Callable[[str], bool | None] = server_client_connected,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """The interval to the next cycle, cut short when the walkie comes back
    while it's marked offline: it connected for four minutes between two
    cycles and the page never showed it (2026-10-17)."""
    end = clock() + watcher.cfg.interval_s
    while not stop.is_set():
        left = end - clock()
        if left <= 0:
            return
        if "walkie.offline" not in watcher.state.open:
            stop.wait(left)
            return
        if stop.wait(min(QUICK_S, left)):
            return
        if connected(health_url(watcher.cfg)) is True:
            waited = watcher.cfg.interval_s - (end - clock())
            watcher.log.info("watch.walkie_back_early", waited_s=round(waited))
            return


if __name__ == "__main__":
    sys.exit(main())
