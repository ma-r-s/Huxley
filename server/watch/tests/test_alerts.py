"""Alerts: once on appearing, once on clearing, never repeated, held at
night, kept across restarts, sent in chunks Telegram accepts."""

from __future__ import annotations

import email.message
import http.client
import io
import urllib.error
import urllib.request
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from huxley_watch.alerts import (
    AlertState,
    Message,
    Sender,
    chunks,
    deliver,
    load_state,
    plan,
    save_state,
)
from huxley_watch.rules import Item
from huxley_watch.wlog import WatchLog

from .logs import NOW, TZ

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def item(id_: str, sentence: str, action: str = "Do it.", cleared: str = "Fine.") -> Item:
    return Item(id_, "Label", "1", "Chip", sentence, "Why.", action, cleared)


OFFLINE = item(
    "walkie.offline",
    "The walkie hasn't connected since 14:40.",
    "Check it's charged.",
    "The walkie is back online.",
)
BOOKS = item("books.hours", "About 1 h of audiobook time left.", "Buy more.")


class FakeSender(Sender):
    def __init__(self, fail: bool = False, fail_after: int | None = None) -> None:
        super().__init__("token", "chat")
        self.sent: list[str] = []
        self.kinds: list[list[str]] = []
        self.fail = fail
        self.fail_after = fail_after

    def send(self, text: str, kinds: Sequence[str] = ()) -> str | None:
        self.kinds.append(list(kinds))
        if self.fail or (self.fail_after is not None and len(self.sent) >= self.fail_after):
            return "URLError"
        self.sent.append(text)
        return None


def step(
    state: AlertState,
    items: list[Item],
    sender: FakeSender,
    quiet: bool = False,
    minutes: float = 0,
    log: WatchLog | None = None,
) -> AlertState:
    log = log or WatchLog(None)
    now = NOW + timedelta(minutes=minutes)
    return deliver(plan(state, items, now, TZ, quiet, log), sender, quiet, log)


def test_open_sends_once_and_never_repeats() -> None:
    s = FakeSender()
    state = step(AlertState(), [OFFLINE], s)
    state = step(state, [OFFLINE], s, minutes=5)
    state = step(state, [OFFLINE], s, minutes=10)
    assert s.sent == ["Needs you: The walkie hasn't connected since 14:40. Check it's charged."]
    assert list(state.open) == ["walkie.offline"]


def test_a_changed_sentence_is_not_a_new_alert() -> None:
    s = FakeSender()
    state = step(AlertState(), [OFFLINE], s)
    step(state, [item(OFFLINE.id, "Since yesterday.")], s, minutes=60 * 24)
    assert len(s.sent) == 1


def test_clear_sends_once() -> None:
    s = FakeSender()
    state = step(AlertState(), [OFFLINE], s)
    state = step(state, [], s, minutes=5)
    state = step(state, [], s, minutes=10)
    assert s.sent[1:] == ["Cleared: The walkie is back online."]
    assert state.open == {}


def test_quiet_hours_hold_then_release_together() -> None:
    s = FakeSender()
    log = WatchLog(None)
    state = step(AlertState(), [OFFLINE, BOOKS], s, quiet=True, log=log)
    assert s.sent == []
    assert len(log.events("watch.alert_held_quiet_hours")) == 2
    state = step(state, [OFFLINE, BOOKS], s, quiet=True, minutes=5)
    assert s.sent == []
    state = step(state, [OFFLINE, BOOKS], s, quiet=False, minutes=600)
    assert len(s.sent) == 1  # one message carrying both
    assert "Needs you: The walkie" in s.sent[0]
    assert "Needs you: About 1 h" in s.sent[0]
    assert state.pending == []


def test_came_and_went_at_night_is_one_line() -> None:
    s = FakeSender()
    state = step(AlertState(), [OFFLINE], s, quiet=True)
    state = step(state, [], s, quiet=True, minutes=30)
    step(state, [], s, quiet=False, minutes=600)
    assert s.sent == ["Came and went: The walkie hasn't connected since 14:40. Cleared at 15:30."]


def test_reopened_before_the_clear_was_sent_says_nothing_new() -> None:
    s = FakeSender()
    state = step(AlertState(), [OFFLINE], s)
    state = step(state, [], s, quiet=True, minutes=5)
    state = step(state, [OFFLINE], s, quiet=True, minutes=10)
    step(state, [OFFLINE], s, quiet=False, minutes=600)
    assert s.sent == ["Needs you: The walkie hasn't connected since 14:40. Check it's charged."]


def test_failed_delivery_is_retried_and_counted() -> None:
    log = WatchLog(None)
    state = step(AlertState(), [OFFLINE], FakeSender(fail=True), log=log)
    assert len(state.pending) == 1
    assert state.failures == 1
    assert log.events("watch.alert_failed")[0]["failures_in_a_row"] == 1
    state = step(state, [OFFLINE], FakeSender(fail=True), minutes=5)
    assert state.failures == 2
    s = FakeSender()
    state = step(state, [OFFLINE], s, minutes=10)
    assert len(s.sent) == 1
    assert state.pending == []
    assert state.failures == 0  # a success resets the count


def test_state_persists_across_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "alerts.json"
    s = FakeSender()
    save_state(path, step(AlertState(), [OFFLINE], s))
    restarted = load_state(path, WatchLog(None))
    assert list(restarted.open) == ["walkie.offline"]
    step(restarted, [OFFLINE], s, minutes=5)
    assert len(s.sent) == 1  # not sent again after the restart
    step(restarted, [], s, minutes=10)
    assert s.sent[-1] == "Cleared: The walkie is back online."


def test_held_messages_and_failures_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "alerts.json"
    save_state(path, step(AlertState(), [OFFLINE], FakeSender(fail=True)))
    loaded = load_state(path, WatchLog(None))
    assert loaded.failures == 1
    assert len(loaded.pending) == 1
    s = FakeSender()
    state = step(loaded, [OFFLINE], s, minutes=600)
    assert len(s.sent) == 1
    assert state.pending == []


def test_damaged_state_file_starts_over_and_says_so(tmp_path: Path) -> None:
    path = tmp_path / "alerts.json"
    path.write_text("{not json")
    log = WatchLog(None)
    assert load_state(path, log) == AlertState()
    assert log.events("watch.state_unreadable")
    path.write_text('{"open": {"x": {"wrong": 1}}}')
    assert load_state(path, WatchLog(None)) == AlertState()


def test_without_telegram_alerts_are_only_logged() -> None:
    log = WatchLog(None)
    sender = Sender(None, None)
    assert sender.channel == "log"
    state = deliver(plan(AlertState(), [OFFLINE], NOW, TZ, False, log), sender, False, log)
    assert state.pending == []
    sent = log.events("watch.alert_sent")
    assert sent[0]["channel"] == "log"
    assert sent[0]["id"] == "walkie.offline"


# --- chunks (critic: a 4096 cut dropped messages) -----------------------------------


def test_chunks_keep_whole_messages_under_the_limit() -> None:
    msgs = [Message("opened", f"i{n}", "x" * 1500, "t") for n in range(5)]
    groups = chunks(msgs, 4096)
    assert [len(g) for g in groups] == [2, 2, 1]
    assert all(len("\n\n".join(m.text for m in g)) <= 4096 for g in groups)


def test_every_message_arrives_across_chunks() -> None:
    s = FakeSender()
    many = [item(f"skill.s{n}", f"Skill {n} " + "x" * 600) for n in range(12)]
    state = step(AlertState(), many, s)
    assert len(s.sent) >= 2
    assert all(len(t) <= 4096 for t in s.sent)
    joined = "\n\n".join(s.sent)
    for n in range(12):
        assert f"Skill {n} " in joined  # nothing cut away
    assert state.pending == []


def test_a_failed_second_chunk_keeps_only_the_unsent_messages() -> None:
    s = FakeSender(fail_after=1)
    many = [item(f"skill.s{n}", f"Skill {n} " + "x" * 1500) for n in range(4)]
    state = step(AlertState(), many, s)
    assert len(s.sent) == 1
    sent_ids = {f"skill.s{n}" for n in range(4) if f"Skill {n} " in s.sent[0]}
    assert sent_ids
    assert {m.id for m in state.pending} == {f"skill.s{n}" for n in range(4)} - sent_ids
    assert state.failures == 1


# --- the sender never leaks the token ---------------------------------------------


def _raise(exc: BaseException) -> Any:
    def opener(*_a: object, **_k: object) -> Any:
        raise exc

    return opener


def test_telegram_refusal_logs_its_description_not_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = io.BytesIO(b'{"ok": false, "error_code": 401, "description": "Unauthorized"}')
    err = urllib.error.HTTPError(
        "https://api.telegram.org/botSECRET/sendMessage",
        401,
        "Unauthorized",
        email.message.Message(),
        body,
    )
    monkeypatch.setattr("urllib.request.urlopen", _raise(err))
    error = Sender("SECRET", "42").send("hi")
    assert error == "HTTP 401: Unauthorized"


@pytest.mark.parametrize(
    "exc",
    [
        http.client.InvalidURL("nonnumeric port: 'SECRET'"),
        http.client.IncompleteRead(b"SECRET"),
        http.client.BadStatusLine("SECRET"),
        ValueError("unknown url type: SECRET"),
    ],
)
def test_any_send_failure_is_its_type_only(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _raise(exc))
    error = Sender("SECRET", "42").send("hi")
    assert error == type(exc).__name__
    assert "SECRET" not in (error or "")


# --- ntfy (phone push) --------------------------------------------------


class _Captured:
    def __init__(self) -> None:
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float = 0) -> Any:
        self.requests.append(request)
        return io.BytesIO(b'{"id": "x"}')


NTFY = "https://ntfy.sh/huxley-SECRETTOPIC"


def test_ntfy_wins_over_telegram_and_posts_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _Captured()
    monkeypatch.setattr("urllib.request.urlopen", captured)
    sender = Sender("tok", "42", ntfy_url=NTFY, page_url="http://100.75.152.70:8780/")
    assert sender.channel == "ntfy"
    assert sender.send("Needs you: the walkie is offline.", ["opened"]) is None
    req = captured.requests[0]
    assert (req.full_url, req.get_method()) == (NTFY, "POST")
    assert req.data == b"Needs you: the walkie is offline."
    headers = {k.lower(): v for k, v in req.header_items()}
    assert (headers["title"], headers["priority"]) == ("Walkie: needs you", "high")
    assert headers["click"] == "http://100.75.152.70:8780/"


def test_ntfy_a_clearing_is_a_plain_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _Captured()
    monkeypatch.setattr("urllib.request.urlopen", captured)
    assert Sender(None, None, ntfy_url=NTFY).send("Cleared: back online.", ["cleared"]) is None
    headers = {k.lower(): v for k, v in captured.requests[0].header_items()}
    assert (headers["title"], headers["priority"]) == ("Walkie: fixed", "default")
    assert "click" not in headers


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.HTTPError(NTFY, 429, "Too Many Requests", email.message.Message(), None),
        urllib.error.URLError(f"no route to {NTFY}"),
        http.client.IncompleteRead(b"SECRETTOPIC"),
    ],
)
def test_ntfy_failures_never_carry_the_topic(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _raise(exc))
    error = Sender(None, None, ntfy_url=NTFY).send("hi", ["opened"])
    assert error in ("HTTP 429", "URLError", "IncompleteRead")
    assert "SECRETTOPIC" not in (error or "")


def test_deliver_tells_the_sender_what_kinds_it_carries() -> None:
    sender = FakeSender()
    state = step(AlertState(), [OFFLINE], sender)
    step(state, [], sender, minutes=10)
    assert sender.kinds == [["opened"], ["cleared"]]
