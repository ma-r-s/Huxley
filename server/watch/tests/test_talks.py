"""The Talks tab's requests and struggles (T1.46): what he asked, what he
said, and each struggle rule with its exclusions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from huxley_watch import talks
from huxley_watch.talks import ToolUse

from .logs import (
    NOW,
    ago,
    audio_start,
    bench,
    commit,
    events,
    line,
    press,
    ptt_stop,
    session_connected,
    starting,
    transcript,
)

W = "w1"  # the walkie's connection in these tests
WALKIE = frozenset({W})


def tool(ts: datetime, name: str, **args: Any) -> str:
    return line("session.rx.tool_call", ts, name=name, call_id="call_x", args=args)


def silent_stop(ts: datetime, conn: str = W) -> str:
    return line(
        "coord.ptt_stop",
        ts,
        turn="t",
        frames=30,
        audio_ms=1320,
        committed=False,
        reason="silent",
        conn_id=conn,
    )


def tap_stop(ts: datetime, conn: str = W) -> str:
    return line(
        "coord.ptt_stop",
        ts,
        turn="t",
        frames=3,
        audio_ms=60,
        committed=False,
        reason="too_short",
        conn_id=conn,
    )


def sent(at: datetime, conn: str = W) -> list[str]:
    """A press, its release 2 s later and its audio sent: as the server logs it."""
    stop = at + timedelta(seconds=2)
    return [press(at, conn), ptt_stop(stop, conn), commit(stop + timedelta(milliseconds=1), conn)]


def ask(
    at: datetime,
    said: str | None = "¿Qué hora es?",
    answer_after: float | None = 2.0,
    tools: list[tuple[str, dict[str, Any]]] | None = None,
    conn: str = W,
) -> list[str]:
    """A press, its release 2 s later, his words 1.5 s after that, and the
    answer (model audio, or the tools) `answer_after` s after the release."""
    stop = at + timedelta(seconds=2)
    out = sent(at, conn)
    if said is not None:
        out.append(transcript(stop + timedelta(seconds=1.5), "user", said))
    if answer_after is not None:
        when = stop + timedelta(seconds=answer_after)
        out.append(audio_start(when))
        for name, args in tools or []:
            out.append(tool(when, name, **args))
    return out


def judged(lines: list[str], now: datetime = NOW) -> list[talks.Verdict]:
    found = talks.his(talks.requests(events(lines)), WALKIE)
    return talks.judge(found, now)


def reasons(lines: list[str], now: datetime = NOW) -> list[tuple[str, ...]]:
    return [v.reasons for v in judged(lines, now)]


def said(lines: list[str]) -> list[str | None]:
    return [r.said for r in talks.requests(events(lines))]


# --- requests ----------------------------------------------------------------


def test_a_committed_press_is_a_request_a_tap_is_not_a_silent_press_is() -> None:
    lines = [
        *ask(ago(minutes=30)),
        press(ago(minutes=20)),
        tap_stop(ago(minutes=20) + timedelta(milliseconds=80)),
        press(ago(minutes=10)),
        silent_stop(ago(minutes=10) + timedelta(seconds=1)),
    ]
    found = talks.requests(events(lines))
    assert [(r.committed, r.heard) for r in found] == [(True, True), (False, False)]


def test_his_words_pair_in_order_even_when_they_come_late() -> None:
    """Real, 2026-10-12: transcripts arrive 1-19 s after the release, and a
    second press can come before the first one's words."""
    t = ago(minutes=5)
    lines = [
        *sent(t),
        *sent(t + timedelta(seconds=4)),
        transcript(t + timedelta(seconds=7), "user", "pon la radio"),
        transcript(t + timedelta(seconds=21), "user", "la de Caracol"),
        transcript(t + timedelta(seconds=8), "assistant", "Listo"),
    ]
    assert said(lines) == ["pon la radio", "la de Caracol"]


def test_words_more_than_a_minute_late_are_no_ones() -> None:
    t = ago(minutes=5)
    assert said([*sent(t), transcript(t + timedelta(seconds=70), "user", "hola")]) == [None]


def test_a_press_whose_audio_was_never_sent_takes_no_words() -> None:
    """The A/B/C shift: B committed but its audio never went (no
    session.tx.commit), so no words come for it. Before, B took C's words
    and C took nothing."""
    t = ago(minutes=5)
    lines = [
        *ask(t, said="pon la radio"),
        press(t + timedelta(seconds=10)),
        ptt_stop(t + timedelta(seconds=12)),  # no commit: the session was down
        *ask(t + timedelta(seconds=20), said="¿qué hora es?"),
    ]
    assert said(lines) == ["pon la radio", None, "¿qué hora es?"]


def test_a_new_session_drops_the_words_still_awaited() -> None:
    """The A/B/C shift across a reconnect: B's item died with the old
    session, so its words never come; C must not get them either."""
    t = ago(minutes=5)
    for fresh in (
        session_connected(t + timedelta(seconds=15)),
        line("app.reconnect_succeeded", t + timedelta(seconds=15), attempt=1),
        starting(t + timedelta(seconds=15)),
    ):
        lines = [
            *ask(t, said="pon la radio"),
            *sent(t + timedelta(seconds=10)),
            fresh,
            *ask(t + timedelta(seconds=20), said="¿qué hora es?"),
        ]
        assert said(lines) == ["pon la radio", None, "¿qué hora es?"], fresh


def test_bench_and_other_clients_presses_take_their_own_words() -> None:
    """Every press counts in the pairing, so a bench press never hands its
    words to his next one; then only his are kept."""
    t = ago(minutes=30)
    w = bench((t - timedelta(minutes=1), t + timedelta(minutes=1), "bench.py run"))
    lines = [
        *ask(t, said="frase del banco"),  # inside the bench window
        *ask(t + timedelta(minutes=5), said="hola", conn="phone"),  # the PWA
        *ask(t + timedelta(minutes=10), said="¿qué hora es?"),
    ]
    found = talks.his(talks.requests(events(lines)), WALKIE, w)
    assert [r.said for r in found] == ["¿qué hora es?"]


# --- the rules -----------------------------------------------------------------


def test_a_request_that_went_fine_is_no_struggle() -> None:
    assert reasons(ask(ago(minutes=10))) == [()]


def test_tool_error_from_the_coordinator_and_from_a_skill() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="pon a Adele", tools=[("play_music", {"query": "Adele"})]),
        line(
            "coord.tool_error",
            t + timedelta(seconds=5),
            level="error",
            tool="play_music",
            args={"query": "Adele"},
            exception_class="RuntimeError",
        ),
        *ask(t + timedelta(minutes=5), said="pon algo de Menudo"),
        line(
            "spotify.unavailable",
            t + timedelta(minutes=5, seconds=6),
            skill="spotify",
            reason="not_found",
            query="Menudo",
        ),
    ]
    got = judged(lines)
    assert [(v.reasons, v.detail) for v in got] == [
        (("tool_error",), "play_music failed"),
        (("tool_error",), "not found"),
    ]


def test_a_failure_after_his_next_press_is_not_this_requests() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t),
        *ask(t + timedelta(seconds=20), said="pon la radio"),
        line("radio.stream_error", t + timedelta(seconds=30), skill="radio", station_id="blu"),
    ]
    assert reasons(lines) == [(), ("tool_error",)]


def test_a_failure_long_after_the_answer_is_not_the_requests() -> None:
    t = ago(minutes=30)
    lines = [*ask(t), line("radio.stream_error", t + timedelta(minutes=5), skill="radio")]
    assert reasons(lines) == [()]


def test_not_heard_silent_press_hallucinated_words_empty_commit() -> None:
    t = ago(minutes=40)
    lines = [
        press(t),
        silent_stop(t + timedelta(seconds=1)),
        *ask(t + timedelta(minutes=10), said="Subtítulos por la comunidad de Amara.org"),
        *ask(t + timedelta(minutes=15), said="¡Gracias por ver el vídeo!"),
        *sent(t + timedelta(minutes=20)),
        line(
            "session.rx.error",
            t + timedelta(minutes=20, seconds=2.2),
            code="input_audio_buffer_commit_empty",
            message="buffer too small",
        ),
    ]
    got = judged(lines)
    assert [v.reasons for v in got] == [("not_heard",)] * 4
    assert [v.detail for v in got] == [
        "silent",
        'transcribed as "Subtítulos por la comunidad de Amara.org"',
        'transcribed as "¡Gracias por ver el vídeo!"',
        "nothing heard",
    ]


def test_empty_words_with_an_answer_are_no_struggle() -> None:
    """The model hears the audio, not the transcript: if it answered within
    20 s (its voice or a tool), it heard him. Without one, not heard."""
    t = ago(minutes=30)
    lines = [
        *ask(t, said=""),  # answered 2 s after the release
        *ask(t + timedelta(minutes=5), said="", answer_after=None, tools=None),
        *ask(t + timedelta(minutes=10), said="  ", answer_after=30),
    ]
    assert reasons(lines) == [(), ("not_heard", "no_answer"), ("not_heard", "no_answer")]
    assert judged(lines)[1].detail == "empty"


def test_no_answer_within_20_s() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, answer_after=None),  # nothing at all
        *ask(t + timedelta(minutes=5), answer_after=25),  # too late
        *ask(t + timedelta(minutes=10), answer_after=5),  # fine
    ]
    assert reasons(lines) == [("no_answer",), ("no_answer",), ()]


def test_an_injected_turn_speaking_is_not_his_answer() -> None:
    """A reminder firing right after his press speaks with owner=injected."""
    t = ago(minutes=30)
    stop = t + timedelta(seconds=2)
    lines = [
        *ask(t, answer_after=None),
        line("coord.audio_start", stop + timedelta(seconds=3), turn="r1", owner="injected"),
    ]
    assert reasons(lines) == [("no_answer",)]
    lines.append(line("coord.audio_start", stop + timedelta(seconds=4), turn="u1", owner="user"))
    assert reasons(lines) == [()]


def test_no_answer_waits_its_20_s_before_saying_so() -> None:
    t = NOW - timedelta(seconds=12)
    assert reasons(ask(t, answer_after=None)) == [()]
    assert reasons(ask(t, answer_after=None), NOW + timedelta(seconds=15)) == [("no_answer",)]


def test_a_new_press_before_any_answer_means_none_came() -> None:
    t = ago(minutes=10)
    lines = [*ask(t, answer_after=None), *ask(t + timedelta(seconds=6))]
    assert reasons(lines) == [("no_answer",), ()]


def test_a_tool_call_is_an_answer() -> None:
    t = ago(minutes=10)
    lines = [*sent(t), tool(t + timedelta(seconds=3), "get_current_time")]
    assert reasons(lines) == [()]


def test_corrected_within_60_s_of_the_answer() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="pon a Juanes", tools=[("play_music", {"query": "Juanes"})]),
        *ask(t + timedelta(seconds=30), said="No, esa no. La de antes."),
        *ask(t + timedelta(minutes=5), said="pon la radio"),
        *ask(t + timedelta(minutes=7), said="No, apágala"),  # 2 min later: a new ask
    ]
    got = judged(lines)
    assert [v.reasons for v in got] == [("corrected",), (), (), ()]
    assert got[0].detail == "No, esa no. La de antes."


def test_correction_words() -> None:
    yes = [
        "No.",
        "no",
        "No, esa no",
        "¡No! ¡Quiero escuchar al grupo Menudo!",
        "esa no me gusta",
        "pon otro, ese no",
    ]
    no = [
        "¿No tienes algunos más famosos?",
        "No sé",
        "Pon otra",
        "otra vez",
        "otro libro",
        "Pon el segundo",
        "Noticias",
        "anoche",
    ]
    assert [t for t in yes if not talks.correction(t)] == []
    assert [t for t in no if talks.correction(t)] == []


def test_no_correction_around_a_skip_or_a_volume_change() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="la siguiente", tools=[("music_control", {"action": "next"})]),
        *ask(t + timedelta(seconds=20), said="no, la otra", tools=[]),
        *ask(t + timedelta(minutes=5), said="pon a Shakira", tools=[("play_music", {"query": "Shakira"})]),
        *ask(
            t + timedelta(minutes=5, seconds=30),
            said="No, siguiente",
            tools=[("music_control", {"action": "next"})],
        ),
        *ask(t + timedelta(minutes=9), said="pon la radio", tools=[("play_station", {"station": "blu"})]),
        *ask(
            t + timedelta(minutes=9, seconds=20),
            said="no, más bajito",
            tools=[("set_volume", {"level": 30})],
        ),
    ]  # fmt: skip
    assert reasons(lines) == [(), (), (), (), (), ()]


def test_asked_again_same_tool_similar_query_within_90_s() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="pon a Adele", tools=[("play_music", {"query": "Adele", "kind": "artist"})]),
        *ask(
            t + timedelta(seconds=40),
            said="que pongas a Adele",
            tools=[("play_music", {"query": "adele", "kind": "track"})],
        ),
    ]
    got = judged(lines)
    assert [v.reasons for v in got] == [("asked_again",), ()]
    assert got[0].detail == "play_music"


def test_not_asked_again_other_query_other_tool_or_too_late() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="pon a Adele", tools=[("play_music", {"query": "Adele"})]),
        *ask(t + timedelta(seconds=40), said="pon a Shakira", tools=[("play_music", {"query": "Shakira"})]),
        *ask(t + timedelta(seconds=80), said="pon la radio", tools=[("play_station", {"station": "blu"})]),
        *ask(t + timedelta(minutes=4), said="pon la radio", tools=[("play_station", {"station": "blu"})]),
    ]  # fmt: skip
    assert reasons(lines) == [(), (), (), ()]


def test_two_questions_with_no_query_are_not_asked_again() -> None:
    """Real, 2026-10-12: 9 of 13 "asked again" were empty-query pairs, the
    time asked twice. Nothing to compare: two questions."""
    t = ago(minutes=30)
    lines = [
        *ask(t, said="¿qué hora es?", tools=[("get_current_time", {})]),
        *ask(t + timedelta(seconds=40), said="¿y qué día?", tools=[("get_current_time", {})]),
        *ask(t + timedelta(minutes=5), said="borra el recordatorio", tools=[("cancel_reminder", {"id": 1})]),
        *ask(t + timedelta(minutes=5, seconds=30), said="y el otro", tools=[("cancel_reminder", {"id": 2})]),
    ]  # fmt: skip
    assert reasons(lines) == [(), (), (), ()]


def test_skips_and_volume_repeat_by_design_a_second_pause_does_not() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="siguiente", tools=[("music_control", {"action": "next"})]),
        *ask(t + timedelta(seconds=20), said="siguiente", tools=[("music_control", {"action": "next"})]),
        *ask(t + timedelta(minutes=2), said="al máximo", tools=[("set_volume", {"level": 100, "say": "max"})]),
        *ask(t + timedelta(minutes=2, seconds=20), said="más", tools=[("set_volume", {"level": 100, "say": "max"})]),
        *ask(t + timedelta(minutes=4), said="pausa", tools=[("music_control", {"action": "pause"})]),
        *ask(t + timedelta(minutes=4, seconds=30), said="pausa", tools=[("music_control", {"action": "pause"})]),
    ]  # fmt: skip
    assert reasons(lines) == [(), (), (), (), ("asked_again",), ()]


def test_several_reasons_are_all_named_in_order() -> None:
    t = ago(minutes=30)
    lines = [
        *ask(t, said="Subtítulos realizados por la comunidad de Amara.org", answer_after=None),
        *ask(t + timedelta(seconds=30), said="no, te dije la radio"),
    ]
    assert reasons(lines)[0] == ("not_heard", "no_answer")


def test_similar_and_query_of() -> None:
    a = ToolUse(NOW, "play_music", {"query": "Los Panchos", "kind": "artist"})
    assert talks.similar(a, ToolUse(NOW, "play_music", {"query": "los panchos"}))
    assert talks.similar(a, ToolUse(NOW, "play_music", {"query": "Panchos"}))
    assert not talks.similar(a, ToolUse(NOW, "play_book", {"query": "Los Panchos"}))
    assert not talks.similar(a, ToolUse(NOW, "play_music", {"query": "Shakira"}))
    assert not talks.similar(ToolUse(NOW, "get_battery", {}), ToolUse(NOW, "get_battery", {}))
    assert talks.query_of({"station": "blu", "x": 1}) == "blu"
    assert talks.query_of({"level": 80}) == ""  # a number is not a query
    assert talks.query_of({"id": 1, "kind": "artist"}) == "artist"
    assert talks.query_of({"query": ["a"], "text": "hola"}) == "hola"


def test_garbled() -> None:
    assert talks.garbled("")
    assert talks.garbled("  ...  ")
    assert talks.garbled("Subtítulos realizados por la comunidad de Amara.org")
    assert not talks.garbled("PAUSA")
    assert not talks.garbled("¿Qué hora es?")
