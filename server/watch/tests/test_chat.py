"""The day as a conversation (chat.py): who said what, with what it did."""

from __future__ import annotations

from typing import Any

from huxley_watch.chat import action, items


def row(at: str, kind: str, who: str = "", text: str = "", **kw: Any) -> dict[str, Any]:
    h, m, s = (int(x) for x in at.split(":"))
    t = h * 60 + m + s / 60
    end = kw.pop("end", None)
    e = kw.pop("e", None)
    return {
        "t": round(t, 2),
        "e": e if e is not None else round(t, 2),
        "at": at,
        "kind": kind,
        "who": who,
        "text": text,
        "detail": kw.pop("detail", ""),
        "audio": kw.pop("audio", None),
        "ms": kw.pop("ms", None),
        "end": end,
        "seconds": kw.pop("seconds", None),
        "bench": False,
        "live": kw.pop("live", False),
    }


def mins(at: str) -> float:
    h, m, s = (int(x) for x in at.split(":"))
    return round(h * 60 + m + s / 60, 2)


# 2026-10-17 13:13-13:20, as the archive gives them: his recording, the
# tool, the radio, then his words a second late; the walkie's words come
# before his.
MORNING = [
    row("13:13:04", "rec", "mic", audio="2026-10-17/a.ogg", ms=3660),
    row("13:13:09", "tool", text="play_station", detail="station: caracol"),
    row(
        "13:13:09", "stream", "radio", "Caracol Radio", end="13:14", e=mins("13:14:20"), seconds=71
    ),
    row("13:13:10", "say", "him", "Pongan noticias Caracol"),
    row("13:14:28", "rec", "mic", audio="2026-10-17/b.ogg", ms=1680),
    row("13:14:30", "tool", text="set_volume", detail="level: 80"),
    row("13:14:31", "rec", "model", audio="2026-10-17/c.ogg", ms=2100),
    row("13:14:31", "say", "huxley", "Listo, lo subo a 80."),
    row("13:14:32", "say", "him", "Súbele el volumen"),
    row(
        "13:14:33", "stream", "radio", "Caracol Radio", end="13:20", e=mins("13:20:00"), live=True
    ),
]


def test_a_press_its_words_and_the_answer_are_one_turn() -> None:
    got = items(MORNING)
    turns = [i for i in got if i["type"] == "turn"]
    assert len(turns) == 2
    first, second = turns
    assert first["him"]["text"] == "Pongan noticias Caracol"
    assert first["him"]["clips"] == [{"audio": "audio/2026-10-17/a.ogg", "ms": 3660}]
    assert first["actions"] == [{"icon": "radio", "label": "Turned on Caracol"}]
    assert first["walkie"] is None and first["heard"] is True
    assert second["him"]["text"] == "Súbele el volumen"  # his words came after the walkie's
    assert second["walkie"] == {
        "text": "Listo, lo subo a 80.",
        "clips": [{"audio": "audio/2026-10-17/c.ogg", "ms": 2100}],
    }
    assert second["actions"] == [{"icon": "volume", "label": "Set the volume to 80"}]


def test_the_radio_pausing_for_a_question_is_one_listening() -> None:
    events = [i for i in items(MORNING) if i["type"] == "event"]
    assert len(events) == 1
    radio = events[0]
    assert (radio["kind"], radio["title"], radio["at"], radio["live"]) == (
        "radio",
        "Caracol Radio",
        "13:13",
        True,
    )


def test_the_order_is_by_time() -> None:
    got = items(MORNING)
    assert [i["type"] for i in got] == ["turn", "event", "turn"]


def test_the_walkie_speaking_on_its_own_is_a_turn_without_a_press() -> None:
    got = items(
        [
            row("09:00:00", "rec", "mic", audio="x.ogg", ms=900),
            row("09:00:02", "say", "huxley", "Son las nueve."),
            row("10:30:00", "say", "huxley", "Es hora de tu pastilla."),
            row("10:30:00", "rec", "model", audio="y.ogg", ms=2000),
        ]
    )
    assert len(got) == 2
    assert got[1]["him"] is None
    assert got[1]["walkie"]["text"] == "Es hora de tu pastilla."


def test_a_press_nothing_heard_or_answered_is_marked() -> None:
    (turn,) = items([row("11:20:00", "rec", "mic", audio="z.ogg", ms=700)])
    assert turn["heard"] is False and turn["him"]["text"] is None


def test_a_fresh_press_is_pending_not_missed() -> None:
    # Critic: every new press read "Didn't catch it" until his words came.
    rows = [row("11:20:00", "rec", "mic", audio="z.ogg", ms=3000)]
    (turn,) = items(rows, now_t=mins("11:20:10"))
    assert (turn["pending"], turn["heard"]) == (True, True)
    (turn,) = items(rows, now_t=mins("11:21:00"))
    assert (turn["pending"], turn["heard"]) == (False, False)


def test_two_quick_presses_keep_their_own_words() -> None:
    got = items(
        [
            row("10:00:00", "rec", "mic", audio="a.ogg", ms=2000),
            row("10:00:06", "rec", "mic", audio="b.ogg", ms=2000),
            row("10:00:08", "say", "him", "primera"),
            row("10:00:14", "say", "him", "segunda"),
        ]
    )
    assert [t["him"]["text"] for t in got] == ["primera", "segunda"]


def test_a_missed_press_stays_missed_when_the_walkie_speaks_later() -> None:
    # Critic: "Te llama Mario." 40 s after a silent press made it "heard".
    got = items(
        [
            row("11:20:00", "rec", "mic", audio="z.ogg", ms=700),
            row("11:20:40", "say", "huxley", "Te llama Mario."),
        ]
    )
    assert len(got) == 2
    assert got[0]["heard"] is False and got[0]["walkie"] is None
    assert got[1]["him"] is None and got[1]["walkie"]["text"] == "Te llama Mario."


def test_ids_are_unique_and_stable() -> None:
    rows = [
        row("12:00:00", "rec", "mic", audio="a.ogg", ms=300),
        row("12:00:00", "rec", "mic", audio="b.ogg", ms=300),
        row("12:00:00", "call", "", "mario", end="12:03", e=mins("12:03:00"), seconds=180),
        row("12:00:00", "stream", "radio", "Caracol Radio", end="12:30", e=mins("12:30:00")),
    ]
    first = [i["id"] for i in items(rows)]
    assert len(set(first)) == 4
    assert first == [i["id"] for i in items(rows)]


def test_a_stream_from_yesterday_keeps_its_day() -> None:
    (ev,) = items(
        [
            row("00:00:00", "stream", "radio", "Caracol Radio", end="00:20", e=20.0)
            | {"at": "Fri 23:50:00", "t": -10.0}
        ]
    )
    assert ev["at"] == "Fri 23:50"


def test_a_call_carries_its_recordings() -> None:
    got = items(
        [
            row("11:34:41", "call", "", "mario", end="11:37", e=mins("11:37:53"), seconds=192),
            row("11:34:52", "rec", "call", "Mario", audio="m.ogg", ms=180000),
            row("11:34:52", "rec", "call", "Him", audio="h.ogg", ms=180000),
        ]
    )
    (call,) = got
    assert (call["kind"], call["title"], call["seconds"]) == ("call", "Mario", 192)
    assert [c["who"] for c in call["clips"]] == ["Mario", "Him"]


def test_actions_read_as_sentences() -> None:
    assert (
        action("play_music", "query: Leo Dan, kind: artist")["label"]
        == "Played Leo Dan on Spotify"
    )
    assert action("get_weather", "")["label"] == "Checked the weather"
    assert action("call_contact", "name: julián ruiz")["label"] == "Called Julián Ruiz"
    assert action("search_the_web", "query: clima Villavicencio")["label"] == (
        "Searched the web: clima Villavicencio"
    )
    assert action("get_current_time", "")["label"] == "Said the time"
    assert (
        action("add_contact", "name: elsa sarmiento")["label"]
        == "Saved Elsa Sarmiento as a contact"
    )
    assert action("something_new", "a: b")["label"] == "Something new: a: b"


def test_the_walkie_speaking_again_later_is_its_own_turn() -> None:
    # 11:34 on 2026-10-17: a call was announced 37 s after his request.
    got = items(
        [
            row("11:34:01", "rec", "mic", audio="p.ogg", ms=3960),
            row("11:34:07", "tool", text="play_station", detail="station: caracol"),
            row("11:34:07", "say", "huxley", "Claro, ya pongo la emisora."),
            row("11:34:07", "say", "him", "Quiero oír Caracol"),
            row("11:34:44", "say", "huxley", "Te llama Mario."),
        ]
    )
    assert [t["walkie"]["text"] for t in got] == ["Claro, ya pongo la emisora.", "Te llama Mario."]
    assert got[1]["him"] is None


def test_a_slow_answer_after_a_filler_stays_in_the_turn() -> None:
    got = items(
        [
            row("11:32:09", "rec", "mic", audio="n.ogg", ms=4320),
            row("11:32:16", "say", "huxley", "Un momento, voy a buscar las noticias."),
            row("11:32:17", "tool", text="get_news"),
            row("11:32:23", "say", "huxley", "Primero, el papa salió del hospital."),
        ]
    )
    (turn,) = got
    assert turn["walkie"]["text"].endswith("el papa salió del hospital.")


def test_made_up_words_are_never_shown_as_his() -> None:
    # 14:03 on 2026-10-17: Whisper's "Amara.org" line under his press.
    (turn,) = items(
        [
            row("14:03:00", "rec", "mic", audio="g.ogg", ms=1000),
            row("14:03:02", "say", "him", "Subtítulos realizados por la comunidad de Amara.org"),
        ]
    )
    assert turn["him"]["text"] is None
    assert turn["him"]["heard_as"].startswith("Subtítulos realizados")
