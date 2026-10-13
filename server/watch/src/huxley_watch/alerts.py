"""Alerts: each item pushed once when it appears and once when it clears,
never repeated; held during quiet hours and sent together when they end.

The open items and the messages not yet delivered live in a small JSON
file, so a restart neither forgets an open item nor sends it again. A
message stays queued until delivery succeeds (a failed send is retried
next cycle, which is not a repeat: it never arrived). Something that
appeared and cleared before its alert went out (at night, say) becomes
one "came and went" line instead of two.

Delivery is a push to the phone through ntfy (a POST to the topic) when a
topic is configured, else Telegram's Bot API (`sendMessage`) when a token
and chat id are, otherwise the alert is only logged (`channel=log`).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from huxley_watch.text import when

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from huxley_watch.rules import Item
    from huxley_watch.wlog import WatchLog

STATE_VERSION = 1
TELEGRAM_MAX = 4096  # characters in one sendMessage
NTFY_MAX = 3500  # ntfy takes 4096 bytes; Spanish accents take two


@dataclass
class OpenItem:
    opened_at: str
    sentence: str
    cleared: str


@dataclass
class Message:
    kind: str  # "opened" | "cleared" | "came_and_went"
    id: str
    text: str
    at: str


@dataclass
class AlertState:
    open: dict[str, OpenItem] = field(default_factory=dict)
    pending: list[Message] = field(default_factory=list)
    failures: int = 0  # deliveries failed in a row (watch.alerts_failing)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "open": {k: asdict(v) for k, v in self.open.items()},
            "pending": [asdict(m) for m in self.pending],
            "failures": self.failures,
        }


def load_state(path: Path, log: WatchLog) -> AlertState:
    """The saved state, or an empty one (first run, or a damaged file,
    which is logged: the open items will be announced again)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AlertState()
    except (OSError, ValueError) as exc:
        log.warning("watch.state_unreadable", path=str(path), error=type(exc).__name__)
        return AlertState()
    try:
        return AlertState(
            open={k: OpenItem(**v) for k, v in dict(raw.get("open", {})).items()},
            pending=[Message(**m) for m in raw.get("pending", [])],
            failures=int(raw.get("failures", 0)),
        )
    except (TypeError, AttributeError, ValueError) as exc:
        log.warning("watch.state_unreadable", path=str(path), error=type(exc).__name__)
        return AlertState()


def save_state(path: Path, state: AlertState) -> None:
    """Atomic: a crash mid-write leaves the previous file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".alerts-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_json(), f, ensure_ascii=False, indent=1)
        Path(tmp).replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise


def plan(
    state: AlertState,
    items: Sequence[Item],
    now: datetime,
    tz: ZoneInfo,
    quiet: bool,
    log: WatchLog,
) -> AlertState:
    """Diff the open items against the state; queue what to say. Logs
    every decision. Returns the new state (the input is not modified)."""
    at = now.isoformat()
    opened = dict(state.open)
    pending = list(state.pending)
    current = {item.id: item for item in items}
    queued: list[Message] = []

    for item_id, item in current.items():
        if item_id in opened:
            continue
        opened[item_id] = OpenItem(opened_at=at, sentence=item.sentence, cleared=item.cleared)
        log.info("watch.item_opened", id=item_id, sentence=item.sentence)
        unsaid = [m for m in pending if m.id == item_id and m.kind == "cleared"]
        if unsaid:
            # It cleared and came back before the clearing was sent: for
            # the reader nothing changed, so neither message goes out.
            pending = [m for m in pending if m not in unsaid]
            log.info("watch.alert_dropped", id=item_id, reason="reopened_before_clear_sent")
            continue
        msg = Message("opened", item_id, f"Needs you: {item.sentence} {item.action}", at)
        pending.append(msg)
        queued.append(msg)

    for item_id in [k for k in opened if k not in current]:
        was = opened.pop(item_id)
        log.info("watch.item_cleared", id=item_id, opened_at=was.opened_at)
        unsent = [m for m in pending if m.id == item_id and m.kind == "opened"]
        if unsent:
            pending = [m for m in pending if m not in unsent]
            msg = Message(
                "came_and_went",
                item_id,
                f"Came and went: {was.sentence} Cleared at {when(now, now, tz)}.",
                at,
            )
        else:
            msg = Message("cleared", item_id, f"Cleared: {was.cleared}", at)
        pending.append(msg)
        queued.append(msg)

    if quiet:
        for msg in queued:
            log.info("watch.alert_held_quiet_hours", id=msg.id, kind=msg.kind)
    return AlertState(open=opened, pending=pending, failures=state.failures)


class Sender:
    """ntfy if a topic is configured, else Telegram, else the log. `send`
    returns None on success or the error to log."""

    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        timeout_s: float = 10.0,
        ntfy_url: str | None = None,
        page_url: str | None = None,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout_s = timeout_s
        self.ntfy_url = ntfy_url
        self.page_url = page_url

    @property
    def channel(self) -> str:
        if self.ntfy_url:
            return "ntfy"
        return "telegram" if self.token and self.chat_id else "log"

    @property
    def limit(self) -> int:
        return NTFY_MAX if self.channel == "ntfy" else TELEGRAM_MAX

    def send(self, text: str, kinds: Sequence[str] = ()) -> str | None:
        """None on success, else what went wrong: never the URL or the
        exception's text, which can carry the token or the topic."""
        if self.channel == "log":
            return None
        if self.channel == "ntfy":
            return self._ntfy(text, kinds)
        data = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"}
        ).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            with urllib.request.urlopen(url, data=data, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            return f"HTTP {exc.code}: {_telegram_description(exc)}"
        except Exception as exc:  # InvalidURL, IncompleteRead, BadStatusLine: not all OSError
            return type(exc).__name__
        if isinstance(body, dict) and body.get("ok"):
            return None
        described = body.get("description") if isinstance(body, dict) else None
        return f"telegram said: {str(described or 'not ok')[:200]}"

    def _ntfy(self, text: str, kinds: Sequence[str]) -> str | None:
        """One push: "Needs you" at high priority when anything opened,
        else a plain "Fixed". Tapping it opens the page (over Tailscale)."""
        assert self.ntfy_url is not None
        needs_you = "opened" in kinds
        headers = {
            "Title": "Walkie: needs you" if needs_you else "Walkie: fixed",
            "Priority": "high" if needs_you else "default",
            "Tags": "warning" if needs_you else "white_check_mark",
        }
        if self.page_url:
            headers["Click"] = self.page_url
        request = urllib.request.Request(
            self.ntfy_url, data=text.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            return f"HTTP {exc.code}"
        except Exception as exc:
            return type(exc).__name__
        return None


def _telegram_description(exc: urllib.error.HTTPError) -> str:
    """Telegram explains a refusal in the body ('Unauthorized', 'Bad
    Request: chat not found'); that text never holds the token."""
    try:
        body = json.loads(exc.read().decode("utf-8") or "{}")
    except Exception:
        return "no description"
    described = body.get("description") if isinstance(body, dict) else None
    return str(described or "no description")[:200]


def chunks(messages: list[Message], limit: int = TELEGRAM_MAX) -> list[list[Message]]:
    """Whole messages per chunk, joined by a blank line, each chunk at most
    `limit` characters. A message longer than that goes alone (cut)."""
    out: list[list[Message]] = []
    size = 0
    for m in messages:
        if out and size + 2 + len(m.text) <= limit:
            out[-1].append(m)
            size += 2 + len(m.text)
        else:
            out.append([m])
            size = len(m.text)
    return out


def deliver(state: AlertState, sender: Sender, quiet: bool, log: WatchLog) -> AlertState:
    """Send what's pending, unless it's quiet hours: in chunks Telegram
    accepts, each message dropped from the queue only once its chunk went
    out. A failure keeps the rest queued for the next cycle and counts
    toward watch.alerts_failing."""
    if quiet or not state.pending:
        return state
    pending = list(state.pending)
    for chunk in chunks(pending, sender.limit):
        text = "\n\n".join(m.text for m in chunk)[: sender.limit]
        try:
            error = sender.send(text, [m.kind for m in chunk])
        except Exception as exc:  # a broken sender must not stop the cycle
            error = type(exc).__name__
        if error is not None:
            failures = state.failures + 1
            log.warning(
                "watch.alert_failed",
                channel=sender.channel,
                error=error,
                ids=[m.id for m in pending],
                failures_in_a_row=failures,
            )
            return AlertState(open=state.open, pending=pending, failures=failures)
        for m in chunk:
            log.info("watch.alert_sent", channel=sender.channel, id=m.id, kind=m.kind, text=m.text)
        pending = pending[len(chunk) :]
    return AlertState(open=state.open, pending=[], failures=0)
