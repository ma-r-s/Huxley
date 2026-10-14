"""Spotify skill: play music from a household Spotify Premium account.

Engine: Spotify Soloist, Spotify's official headless Spotify Connect
client, running beside the server (systemd unit on Linux; `dev/` has a
container for a Mac). The skill controls it over its local WebSocket
(`soloist.py`) and streams its audio, captured from a null sink over
TCP (`capture.py`), as an `AudioStream`, the same path radio uses.
Huxley stays the one audio pipe: Soloist never plays to a speaker.

Tools:
- `play_music(query?, kind?)`: search and play a song, artist, album or
  playlist (the runners-up go back as `alternatives`). No query: continue
  the last music played.
- `music_control(action)`: pause, resume, next, previous, for whatever is
  loaded: in a book, next and previous are chapters.
- `whats_playing()`: what is on (song, or book and chapter with what's
  left). An info tool: the model speaks the answer, and what was playing
  comes back after it like after any turn.
- `play_book(title?, from_beginning?, confirmed?)`, `book_control(action)`
  (back, forward, restart_chapter, go_to_chapter, go_back_to_saved_place),
  `find_books(query?, like?, more?, listened?)` and
  `book_opinion(opinion, title?)`: audiobooks (T1.40, `books.py`). Offers
  come only from the persona's curated shelf (T1.45, `shelf.py`); open
  search plays a book or an author named, never a theme.

A press pauses the music (the framework holds the stream: the factory's
`finally` queues a Soloist pause without awaiting it) and resumes it
when the turn ends, by calling the factory again, which sends "play".
A turn that runs `music_control(pause)` (CancelMedia) or starts another
stream ends it instead; next/previous act and then return a resume
stream.

Config (persona's `skills.spotify` block), all optional:
- `soloist_ws` (default `ws://127.0.0.1:9090`), `pcm` (default
  `127.0.0.1:4712`): where Soloist and its audio are.
- `market`: ISO country for search, so results are playable here.
- `start_sound` / `sounds_path`: earcon before the music (like radio).
- `patience_minutes` (default 30): how long music paused by a reminder
  or call waits to come back.
- `book_shelf` (default `../books/shelf.json`, relative to the persona
  data dir like `sounds_path`): the built shelf; missing turns offers back
  into T1.40's open search.

Secrets (`<persona data>/secrets/spotify/values.json`): `client_id`,
`client_secret` of a Spotify Web API app, for search.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from huxley_sdk import (
    AudioStream,
    CancelMedia,
    ContentType,
    SkillContext,
    ToolDefinition,
    ToolResult,
)
from huxley_sdk.audio import load_pcm_palette
from huxley_skill_spotify import lines
from huxley_skill_spotify import shelf as shelves
from huxley_skill_spotify.books import (
    OPINIONS,
    PLAYED_AFTER_MS,
    BookMemory,
    Label,
    Listener,
    Place,
    capped_position,
    find_chapter,
    fold,
    is_guide,
    order_books,
    remaining_ms,
    rewind_ms,
)
from huxley_skill_spotify.capture import CHUNK_BYTES, CaptureError, open_capture, read_chunks
from huxley_skill_spotify.search import (
    KINDS,
    BookInfo,
    Match,
    SearchError,
    SpotifySearch,
    title_score,
)
from huxley_skill_spotify.shelf import Shelf, ShelfBook, ShelfFile
from huxley_skill_spotify.soloist import (
    PlayerState,
    SoloistClient,
    describe_item,
    item_uri,
    position_now,
)
from huxley_skill_spotify.tooltext import TEXT

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Coroutine

    from huxley_sdk import SkillLogger


DEFAULT_WS = "ws://127.0.0.1:9090"
DEFAULT_PCM = "127.0.0.1:4712"
# The persona's shelf, relative to its data dir (T1.45).
DEFAULT_SHELF = "../books/shelf.json"
# Offers per answer, and categories named per answer.
OFFER_COUNT = 3
CATEGORY_COUNT = 4
# How long to wait for Soloist to report "playing" after a play command.
START_TIMEOUT_S = 10.0
# PCM16 mono 24 kHz: how much sound one captured chunk carries.
CHUNK_MS = CHUNK_BYTES * 1000 // (2 * 24_000)
# Moving between chapters: each skip takes a moment to land.
SKIP_GAP_S = 0.35
NAV_SETTLE_S = 3.0
# A playing book's place is written down this often (and when it stops).
PLACE_EVERY_S = 15.0
# Falling asleep: after this long with no press, the book pauses at the
# next chapter boundary (persona `book_sleep_minutes`; 0 turns it off).
DEFAULT_SLEEP_MINUTES = 60.0
# A weak title match is confirmed before spending listening hours on it.
WEAK_TITLE = 0.45
# A title word counts as heard when a heard word is this close to it.
UNHEARD_RATIO = 0.75
# The last chapter stopping this close to its end is the end of the book.
END_MARGIN_MS = 15_000
# Checking a book before recommending it: Spotify stops a book it won't
# play within 0.3-1.2 s (measured), so one still playing after this is in.
PROBE_LOAD_S = 4.0
# After a seek, how long to stay silent at most waiting for it to land.
SEEK_SETTLE_S = 2.0
# Soloist's skip_prev, like Spotify's own button, restarts the song once
# it's past its first ~3 s and only goes back before that.
PREVIOUS_GOES_BACK_MS = 2_000
# "ok" only if still playing at the end: Spotify says "playing" the moment
# a play lands, before it decides (Siddhartha passed a 0.35 s check, then
# was refused, and the refusal was read as the hours, 2026-10-12).
PROBE_OK_S = 1.6
MAX_PROBES = 4
# A book played fine this recently: the hours aren't out, so a refusal
# means the book isn't available, with no control check.
HOURS_OK_FOR_S = 30 * 60
# Paused from a Spotify app for this long: end the stream (free the speaker).
PAUSED_GRACE_S = 3.0
# A fresh start that goes idle this soon was refused, not finished: an
# audiobook on an account with no listening time reports "playing", then
# "idle" 0.3 s later, with no error (seen 2026-10-12).
REFUSED_S = 3.0

# What the model should tell the user, per failure. Instructions, not
# literal text: the persona phrases them.
_SAY: dict[str, dict[str, str]] = {
    "es": {
        "player_offline": (
            "Dile al usuario que la música de Spotify no está disponible en este "
            "momento y ofrécele poner la radio."
        ),
        "player_not_paired": (
            "Dile al usuario que Spotify todavía no está conectado en este aparato "
            "y ofrécele poner la radio."
        ),
        "search_not_configured": (
            "Dile al usuario que todavía no puedes buscar en Spotify y ofrécele poner la radio."
        ),
        "search_failed": (
            "Dile al usuario que no pudiste buscar en Spotify ahora mismo y ofrécele "
            "poner la radio."
        ),
        "not_found": (
            "Dile al usuario que no encontraste eso en Spotify y pregúntale si quiere "
            "otra canción o artista."
        ),
        "nothing_playing": "Dile al usuario que no está sonando nada en Spotify.",
        "nothing_to_resume": (
            "Pregúntale al usuario, en una frase corta, qué artista o canción quiere oír."
        ),
        "start_failed": (
            "Dile al usuario, en una frase, que Spotify no pudo poner la música y "
            "ofrécele poner la radio."
        ),
        "takeover": (
            "Dile al usuario, en una frase, que la música de Spotify pasó a sonar en otro aparato."
        ),
        "interrupted": (
            "Dile al usuario, en una frase, que la música de Spotify se cortó y "
            "pregúntale si quiere que la vuelvas a poner."
        ),
        "patience_expired": (
            "Dile al usuario, en una frase, que la música quedó en pausa y que "
            "puede pedirte que siga cuando quiera."
        ),
    },
    "en": {
        "player_offline": "Tell the user Spotify music isn't available right now; offer the radio.",
        "player_not_paired": (
            "Tell the user Spotify isn't connected on this device yet; offer the radio."
        ),
        "search_not_configured": "Tell the user you can't search Spotify yet; offer the radio.",
        "search_failed": "Tell the user you couldn't search Spotify just now; offer the radio.",
        "not_found": (
            "Tell the user you didn't find that on Spotify and ask if they'd like "
            "another song or artist."
        ),
        "nothing_playing": "Tell the user nothing is playing on Spotify.",
        "nothing_to_resume": "Ask the user, in one short sentence, which artist or song to play.",
        "start_failed": (
            "Tell the user, in one sentence, that Spotify couldn't start the music; "
            "offer the radio."
        ),
        "takeover": (
            "Tell the user, in one sentence, that the Spotify music moved to another device."
        ),
        "interrupted": (
            "Tell the user, in one sentence, that the Spotify music cut out and ask "
            "if they want it back on."
        ),
        "patience_expired": (
            "Tell the user, in one sentence, that the music stayed paused and they "
            "can ask you to continue it."
        ),
    },
    "fr": {
        "player_offline": (
            "Dis à l'utilisateur que la musique Spotify n'est pas disponible pour "
            "l'instant ; propose la radio."
        ),
        "player_not_paired": (
            "Dis à l'utilisateur que Spotify n'est pas encore connecté sur cet "
            "appareil ; propose la radio."
        ),
        "search_not_configured": (
            "Dis à l'utilisateur que tu ne peux pas encore chercher sur Spotify ; "
            "propose la radio."
        ),
        "search_failed": (
            "Dis à l'utilisateur que tu n'as pas pu chercher sur Spotify ; propose la radio."
        ),
        "not_found": (
            "Dis à l'utilisateur que tu n'as pas trouvé cela sur Spotify et demande "
            "s'il veut une autre chanson ou un autre artiste."
        ),
        "nothing_playing": "Dis à l'utilisateur que rien ne joue sur Spotify.",
        "nothing_to_resume": (
            "Demande à l'utilisateur, en une phrase courte, quel artiste ou quelle "
            "chanson il veut écouter."
        ),
        "start_failed": (
            "Dis à l'utilisateur, en une phrase, que Spotify n'a pas pu lancer la "
            "musique ; propose la radio."
        ),
        "takeover": (
            "Dis à l'utilisateur, en une phrase, que la musique Spotify est passée "
            "sur un autre appareil."
        ),
        "interrupted": (
            "Dis à l'utilisateur, en une phrase, que la musique Spotify s'est coupée "
            "et demande s'il veut la remettre."
        ),
        "patience_expired": (
            "Dis à l'utilisateur, en une phrase, que la musique est restée en pause "
            "et qu'il peut te demander de la reprendre."
        ),
    },
}

# Failures that contradict the tool's own `ok: true, playing: ...`: the
# play_music result already told the model it was playing. Asked to "tell
# the user Spotify wouldn't play the book", the model said "the audiobook
# is playing now" instead (live, 2026-10-12). So these state the fact and
# give the exact line, the format whats_playing proved reliable.
_NOTICE = {
    "es": "[Aviso del sistema, no lo dijo el usuario: {fact}] Di en voz alta exactamente "
    "{line} No llames a ninguna herramienta.",
    "en": "[System notice, not said by the user: {fact}] Say out loud exactly {line} "
    "Do not call any tool.",
    "fr": "[Avis du système, pas dit par l'utilisateur : {fact}] Dis à voix haute "
    "exactement {line} N'appelle aucun outil.",
}
_CONTRADICTED: dict[str, dict[str, tuple[str, str]]] = {
    "es": {
        "start_failed": (
            "la música NO está sonando, Spotify no la pudo poner.",
            "«No pude poner la música en Spotify. ¿Pongo la radio?»",
        ),
    },
    "en": {
        "start_failed": (
            "the music is NOT playing, Spotify couldn't start it.",
            '"I couldn\'t start the music on Spotify. Shall I put the radio on?"',
        ),
    },
    "fr": {
        "start_failed": (
            "la musique NE joue PAS, Spotify n'a pas pu la lancer.",
            "« Je n'ai pas pu lancer la musique sur Spotify. Je mets la radio ? »",
        ),
    },
}

_ACTIONS = ("pause", "resume", "next", "previous")
_BOOK_ACTIONS = (
    "back",
    "forward",
    "restart_chapter",
    "go_to_chapter",
    "go_back_to_saved_place",
)
# A Soloist command and its fields, sent before a stream's play.
_Command = tuple[str, dict[str, Any]]
# Stream endings that must not queue a pause: something else holds the
# player (takeover, paused elsewhere), or it already stopped by itself.
_NO_PAUSE = frozenset(
    {
        "takeover",
        "finished",
        "refused",
        "paused_elsewhere",
        "capture_unreachable",
        "book_finished",
        "book_cut",
        "book_left",
    }
)


def _is_book(uri: str) -> bool:
    """A Spotify audiobook is a show URI; its chapters are episodes."""
    return uri.startswith("spotify:show:")


# Words around a book request that are neither title nor author.
_FILLER = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "lo",
        "un",
        "una",
        "de",
        "del",
        "libro",
        "audiolibro",
        "pon",
        "ponme",
        "quiero",
        "escuchar",
        "the",
        "a",
        "an",
        "of",
        "by",
        "book",
        "audiobook",
        "play",
        "put",
        "le",
        "les",
        "un",
        "une",
        "livre",
    ]
)


def _weak_match(spoken: str, book: Match) -> bool:
    """What was heard, minus the author's words, is far from the book's
    title, or leaves one of its main words unheard: confirm before
    spending listening hours on it. Only the author ("el libro de García
    Márquez") is not weak: any of theirs will do."""
    author = set(fold(book.artist or "").replace(",", " ").split())
    rest = [w for w in fold(spoken).split() if w not in author and w not in _FILLER]
    if not rest:
        return False
    if title_score(" ".join(rest), fold(book.title)) < WEAK_TITLE:
        return True
    return bool(_unheard_words(rest, book.title))


def _unheard_words(heard: list[str], title: str) -> list[str]:
    """The main title's words (before a subtitle, 4 letters or more) that
    nothing heard comes close to, before the last one that was heard: a
    title named by its opening words ("Cien años") is fine, one whose
    opening was never said is not. "Los doce apóstoles", heard as "Los dos
    apos", matched "Hechos de los Apóstoles" at 0.6 and played without a
    question (2026-10-14, his first morning): "hechos" was never said."""
    main = re.split(r"[:(/|.\-]", title, maxsplit=1)[0]
    words = [w for w in fold(main).split() if len(w) >= 4 and w not in _FILLER]

    def close(word: str) -> bool:
        return any(
            SequenceMatcher(None, word, h).ratio() >= UNHEARD_RATIO
            or (len(h) >= 4 and (word.startswith(h) or h.startswith(word)))
            for h in heard
        )

    matched = [close(w) for w in words]
    if not any(matched):
        return words
    last = max(i for i, m in enumerate(matched) if m)
    return [w for w, m in zip(words[:last], matched[:last], strict=True) if not m]


# The shelf's offers are read "Uno, ... Dos, ... Tres" (lines.py), so the
# numbers pick too ("el dos"; T1.45 review). Not "un": it's an article.
_ORDINALS = {
    "uno": 0,
    "una": 0,
    "one": 0,
    "une": 0,
    "dos": 1,
    "two": 1,
    "deux": 1,
    "tres": 2,
    "three": 2,
    "trois": 2,
    "primero": 0,
    "primer": 0,
    "primera": 0,
    "first": 0,
    "premier": 0,
    "premiere": 0,
    "segundo": 1,
    "segunda": 1,
    "second": 1,
    "deuxieme": 1,
    "tercero": 2,
    "tercer": 2,
    "tercera": 2,
    "third": 2,
    "troisieme": 2,
}
_LAST = frozenset({"ultimo", "ultima", "last", "dernier", "derniere"})
_THAT = frozenset({"ese", "esa", "eso", "este", "esta", "that", "this", "celui-la", "celle-la"})


# Words around an ordinal that don't name anything: "el segundo libro",
# "pon el dos", "número tres".
_ORDINAL_FILLER = frozenset(
    {"el", "la", "los", "las", "lo", "numero", "libro", "pon", "ponme", "quiero", "de"}
    | {"the", "number", "book", "play", "le", "numero", "livre", "mets"}
)


def _ordinal(spoken: str, count: int) -> int | None:
    """'el segundo' -> 1, 'el último' -> count - 1, 'ese' with one item -> 0.
    Only when the ordinal is all that was said (fillers aside): "Los tres
    mosqueteros" is a title, not the third book (2026-10-14, the hearing
    bench: "el segundo" -> play_book("Los tres mosqueteros") played the
    third offer, Moby Dick)."""
    words = [w for w in fold(spoken).replace("-", " ").split() if w not in _ORDINAL_FILLER]
    if len(words) != 1:
        return None
    word = words[0]
    if word in _ORDINALS and _ORDINALS[word] < count:
        return _ORDINALS[word]
    if word in _LAST:
        return count - 1
    if count == 1 and word in _THAT:
        return 0
    return None
    for word in words:
        if word in _ORDINALS and _ORDINALS[word] < count:
            return _ORDINALS[word]
        if word in _LAST:
            return count - 1
    if count == 1 and any(w in _THAT for w in words):
        return 0
    return None


def _offered(output: str) -> bool:
    """A find_books answer that offers something to choose: books or kinds
    of book."""
    try:
        data = json.loads(output)
    except ValueError:
        return False
    return bool(
        isinstance(data, dict) and data.get("ok") and (data.get("books") or data.get("categories"))
    )


def _first_sentence(text: str, limit: int = 200) -> str:
    """The start of a book's description, enough to say what it's about."""
    text = " ".join(text.split())
    end = text.find(". ")
    if 0 < end < limit:
        return text[: end + 1]
    return text[:limit].rsplit(" ", 1)[0] if len(text) > limit else text


_CREDIT_LINE = re.compile(
    r"^\s*(author|narrator|autor|narrador|auteur)[^:\n]{0,8}:.*$", re.I | re.M
)


def _blurb(about: str) -> str:
    """Spotify's synopsis without its "Author(s): ... Narrator(s): ..."
    lines."""
    return _CREDIT_LINE.sub("", about).strip()


# whats_playing's instruction: one thing only, say this exact line. Asking
# for "say it, then call music_control(resume)" in the same reply got the
# resume without the line 1 time in 3 (live, 2026-10-10). The framework
# resumes the music after the turn now (2026-10-12), so no call is needed.
_SAY_SONG = {
    "es": "«Es {title}, de {by}.»",
    "en": '"It\'s {title}, by {by}."',
    "fr": "« C'est {title}, de {by}. »",
}
_THEN = {
    "es": "Di en voz alta exactamente {line} No llames a ninguna herramienta.",
    "en": "Say out loud exactly {line} Do not call any tool.",
    "fr": "Dis à voix haute exactement {line} N'appelle aucun outil.",
}


class SpotifySkill:
    """Plays Spotify through Soloist, streamed like radio."""

    # Flat optional keys; secrets live in ctx.secrets, not here.
    config_schema: ClassVar[dict[str, Any] | None] = None
    data_schema_version: ClassVar[int] = 1

    def __init__(
        self,
        *,
        client: SoloistClient | None = None,
        search: SpotifySearch | None = None,
    ) -> None:
        # Keyword-only injection points for tests.
        self._client = client
        self._search = search
        # The book Soloist has loaded, with its chapters (T1.40).
        self._book: BookInfo | None = None
        self._memory: BookMemory | None = None
        # The last list of books read out, for "el segundo".
        self._last_list: list[Match] = []
        # The shelf file (T1.45) and the last browse, for "otros".
        self._shelf_file: ShelfFile | None = None
        self._browse: dict[str, Any] | None = None
        # When a book stream last ended on a press: a resume backs up by a
        # tenth of the pause.
        self._paused_at: float | None = None
        # Whether the last stream was a book: "sigue" with Soloist idle and
        # nothing loaded restarts the last book, not the last music.
        self._last_was_book = False
        # Streams playing right now: a book check never takes the player
        # from one.
        self._streams = 0
        # When a book last played fine (a stream or a check).
        self._hours_ok_at: float | None = None
        self._sleep_s = DEFAULT_SLEEP_MINUTES * 60
        self._hours_per_month: float | None = None
        self._language = "en"
        self._ctx: SkillContext | None = None
        self._logger: SkillLogger | None = None
        self._pcm_host = "127.0.0.1"
        self._pcm_port = 4712
        self._patience = timedelta(minutes=30)
        self._earcon: bytes | None = None
        # Narration tasks: inject_turn is scheduled, never awaited from a
        # stream (it runs in the coordinator's media path). Held so they
        # aren't garbage collected mid-flight.
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def name(self) -> str:
        return "spotify"

    # --- Lifecycle ---

    async def setup(self, ctx: SkillContext) -> None:
        cfg = ctx.config
        self._ctx = ctx
        self._logger = ctx.logger
        self._language = str(ctx.language or cfg.get("language_code") or "en").lower()

        pcm = str(cfg.get("pcm", DEFAULT_PCM))
        host, _, port = pcm.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"spotify skill: `pcm` must be host:port, got {pcm!r}")
        self._pcm_host, self._pcm_port = host, int(port)
        self._patience = timedelta(minutes=float(cfg.get("patience_minutes", 30)))
        self._memory = BookMemory(ctx.storage)
        raw_shelf = Path(str(cfg.get("book_shelf", DEFAULT_SHELF)))
        self._shelf_file = ShelfFile(
            raw_shelf
            if raw_shelf.is_absolute()
            # normpath: "data/../books" must not need data/ to exist.
            else Path(os.path.normpath(ctx.persona_data_dir / raw_shelf))
        )
        await self._current_shelf()
        await self._backfill_summaries()
        self._sleep_s = float(cfg.get("book_sleep_minutes", DEFAULT_SLEEP_MINUTES)) * 60
        hours = cfg.get("book_hours_per_month")
        self._hours_per_month = float(hours) if hours else None

        if self._client is None:
            self._client = SoloistClient(str(cfg.get("soloist_ws", DEFAULT_WS)), ctx.logger)
        client = self._client
        ctx.background_task("spotify.soloist", client.run)

        if self._search is None:
            client_id = await ctx.secrets.get("client_id")
            client_secret = await ctx.secrets.get("client_secret")
            if client_id and client_secret:
                market = cfg.get("market")
                self._search = SpotifySearch(
                    client_id, client_secret, market=str(market) if market else None
                )
            else:
                await ctx.logger.awarning(
                    "spotify.search_unconfigured",
                    missing=[
                        k
                        for k, v in (("client_id", client_id), ("client_secret", client_secret))
                        if not v
                    ],
                )

        role = cfg.get("start_sound")
        if role:
            raw = str(cfg.get("sounds_path", "sounds"))
            sounds_dir = Path(raw) if Path(raw).is_absolute() else ctx.persona_data_dir / raw
            palette = load_pcm_palette(sounds_dir, [str(role)])
            self._earcon = palette.get(str(role))
            if self._earcon is None:
                await ctx.logger.awarning(
                    "spotify.start_sound_missing", role=role, path=str(sounds_dir)
                )

        await ctx.logger.ainfo(
            "spotify.setup_complete",
            soloist_ws=str(cfg.get("soloist_ws", DEFAULT_WS)),
            pcm=pcm,
            search=self._search is not None,
            earcon=self._earcon is not None,
        )

    async def reconfigure(self, ctx: SkillContext) -> None:
        self._ctx = ctx
        self._language = (ctx.language or self._language).lower()

    async def teardown(self) -> None:
        if self._search is not None:
            await self._search.aclose()
        for task in list(self._tasks):
            task.cancel()

    # --- Tools ---

    @property
    def tools(self) -> list[ToolDefinition]:
        t = TEXT[self._lang()]
        # Books have their own tool: a song named like a book stays a song.
        kinds = [k for k in KINDS if k != "audiobook"]
        return [
            ToolDefinition(
                name="play_music",
                description=t["play_music"],
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": t["play_query"]},
                        "kind": {"type": "string", "enum": kinds, "description": t["play_kind"]},
                    },
                    # No query means "some music": the last music continues.
                    "required": [],
                },
            ),
            ToolDefinition(
                name="music_control",
                description=t["music_control"],
                parameters={
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": list(_ACTIONS)}},
                    "required": ["action"],
                },
            ),
            ToolDefinition(
                name="whats_playing",
                description=t["whats_playing"],
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="play_book",
                description=t["play_book"],
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": t["book_title"]},
                        "from_beginning": {
                            "type": "boolean",
                            "description": t["book_from_beginning"],
                        },
                        "confirmed": {"type": "boolean", "description": t["book_confirmed"]},
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="book_control",
                description=t["book_control"],
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(_BOOK_ACTIONS),
                            "description": t["control_action"],
                        },
                        "seconds": {"type": "number", "description": t["control_seconds"]},
                        "chapter": {"type": "string", "description": t["control_chapter"]},
                    },
                    "required": ["action"],
                },
            ),
            ToolDefinition(
                name="find_books",
                description=t["find_books"],
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": t["find_query"]},
                        "like": {"type": "string", "description": t["find_like"]},
                        "more": {"type": "boolean", "description": t["find_more"]},
                        "listened": {"type": "boolean", "description": t["find_listened"]},
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="book_opinion",
                description=t["book_opinion"],
                parameters={
                    "type": "object",
                    "properties": {
                        "opinion": {
                            "type": "string",
                            "enum": list(OPINIONS),
                            "description": t["opinion_value"],
                        },
                        "title": {"type": "string", "description": t["opinion_title"]},
                    },
                    "required": ["opinion"],
                },
            ),
        ]

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "play_music":
                return await self._play_music(
                    str(args.get("query") or ""), str(args.get("kind") or "any")
                )
            case "music_control":
                return await self._music_control(str(args.get("action") or ""))
            case "whats_playing":
                return await self._whats_playing()
            case "play_book":
                return await self._play_book(
                    str(args.get("title") or ""),
                    from_beginning=bool(args.get("from_beginning")),
                    confirmed=bool(args.get("confirmed")),
                )
            case "book_control":
                return await self._book_control(
                    str(args.get("action") or ""),
                    seconds=float(args.get("seconds") or 30),
                    chapter=str(args.get("chapter") or ""),
                )
            case "find_books":
                found = await self._find_books(
                    str(args.get("query") or ""),
                    like=str(args.get("like") or ""),
                    more=bool(args.get("more")),
                    listened=bool(args.get("listened")),
                )
                # Choosing from an offer: what was playing stays paused at its
                # place instead of coming back over it (2026-10-14: after each
                # list the last book resumed, "¿Cuál pongo?" included). When
                # nothing was offered, it comes back as after any question.
                if found.side_effect is None and _offered(found.output):
                    return ToolResult(output=found.output, side_effect=CancelMedia())
                return found
            case "book_opinion":
                return await self._book_opinion(
                    str(args.get("opinion") or ""), str(args.get("title") or "")
                )
            case _:
                return ToolResult(output=json.dumps({"error": f"unknown_tool:{tool_name}"}))

    async def _play_music(self, query: str, kind: str) -> ToolResult:
        if kind == "audiobook":
            # A session from before books had their own tool (T1.40).
            return await self._play_book(query, from_beginning=False, confirmed=False)
        blocked = self._unavailable()
        if blocked:
            return await self._fail(blocked, query=query)
        if not query.strip():
            return await self._play_last()
        if self._search is None:
            return await self._fail("search_not_configured", query=query)
        try:
            found = await self._search.candidates(query, kind)
        except SearchError as exc:
            await self._log().awarning("spotify.search_failed", query=query, error=str(exc))
            return await self._fail("search_failed", query=query)
        if not found:
            return await self._fail("not_found", query=query)
        match, others = found[0], found[1:]
        await self._log().ainfo(
            "spotify.play_requested",
            query=query,
            asked_kind=kind,
            kind=match.kind,
            title=match.title,
            artist=match.artist,
            uri=match.uri,
            alternatives=[f"{m.title} / {m.artist}" for m in others],
        )
        payload: dict[str, Any] = {
            "ok": True,
            "playing": match.title,
            "by": match.artist,
            "kind": match.kind,
        }
        if others:
            # Not spoken now (a stream tool gets no follow-up round); it
            # stays in the conversation for "no, the other one".
            payload["alternatives"] = [
                {"title": m.title, "by": m.artist, "kind": m.kind} for m in others
            ]
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            side_effect=self._stream(start=match),
        )

    async def _play_last(self) -> ToolResult:
        """Music asked for with nothing named: continue the last music.
        Persona-neutral: no default genre or artist is ever guessed. With
        nothing to continue, ask instead of failing after the 10 s start
        timeout. Soloist keeps its last context across restarts; when that
        is a book, the music comes from the skill's own record, since "pon
        música" after a book must not continue the book (T1.40)."""
        state = self._require_client().state
        context = state.context_uri
        if context and _is_book(context):
            last = await self._require_memory().last_music()
            if last is None:
                return await self._fail("nothing_to_resume")
            again = Match(
                uri=str(last["uri"]),
                kind=str(last.get("kind") or "any"),
                title=str(last.get("title") or "Spotify"),
                artist=last.get("by"),
            )
            await self._log().ainfo(
                "spotify.play_last", context=again.uri, restart=True, after_book=True
            )
            return ToolResult(
                output=json.dumps({"ok": True, "action": "restart"}),
                side_effect=self._stream(start=again),
            )
        if not context:
            return await self._fail("nothing_to_resume")
        # Paused: a bare play resumes it. Idle: it ran out (a single song
        # ends that way), so start that context again by its URI rather
        # than send a bare play Soloist may have nothing to resume for.
        finished = state.status == "idle"
        await self._log().ainfo("spotify.play_last", context=context, restart=finished)
        if not finished:
            return ToolResult(
                output=json.dumps({"ok": True, "action": "resume"}),
                side_effect=self._stream(),
            )
        parts = context.split(":")
        kind = parts[1] if len(parts) == 3 and parts[1] in KINDS else "any"
        title, by = describe_item(state.item)
        again = Match(uri=context, kind=kind, title=title or "Spotify", artist=by)
        return ToolResult(
            output=json.dumps({"ok": True, "action": "restart"}),
            side_effect=self._stream(start=again),
        )

    async def _music_control(self, action: str) -> ToolResult:
        if action not in _ACTIONS:
            return ToolResult(
                output=json.dumps({"ok": False, "error": f"unknown_action:{action}"})
            )
        await self._log().ainfo("spotify.control", action=action)
        if action == "pause":
            # The press already paused the stream (its finally queued a
            # pause); CancelMedia ends it so the framework doesn't resume it
            # after the turn, and the command covers a pause asked while
            # nothing streamed.
            self._require_client().send("pause")
            return ToolResult(
                output=json.dumps({"ok": True, "action": "pause"}), side_effect=CancelMedia()
            )
        blocked = self._unavailable()
        if blocked:
            return await self._fail(blocked)
        if action in ("next", "previous"):
            book = await self._loaded_book()
            if book is not None:
                return await self._move_chapter(book, 1 if action == "next" else -1)
        if action == "previous":
            await self._rewind_before_previous()
        if action == "resume" and self._require_client().state.status == "idle":
            # Nothing to resume (a cut, a refusal, the end): a bare play
            # would do nothing for 10 s. Start it again by its URI.
            again = await self._book_to_restart()
            if again is not None:
                return ToolResult(
                    output=json.dumps(
                        {"ok": True, "action": "restart", "playing": again.title},
                        ensure_ascii=False,
                    ),
                    side_effect=self._stream(start=again),
                )
            return await self._play_last()
        before: tuple[_Command, ...] = {
            "next": (("skip_next", {}),),
            "previous": (("skip_prev", {}),),
        }.get(action, ())
        return ToolResult(
            output=json.dumps({"ok": True, "action": action}),
            side_effect=self._stream(before=before),
        )

    async def _rewind_before_previous(self) -> None:
        """ "Canción anterior" 5 s into an autoplayed song restarted it,
        twice, instead of going back (2026-10-13): past its first seconds
        skip_prev only restarts. So seek to the start first and wait for
        it to land; at 0, skip_prev goes back. The seek can't ride in the
        stream's commands: those send seeks after play."""
        client = self._require_client()
        position = position_now(client.state)
        if position is not None and position < PREVIOUS_GOES_BACK_MS:
            await self._log().ainfo("spotify.previous", position_ms=position, rewound=False)
            return
        client.send("seek", position_ms=0)
        landed = await client.wait_until(
            lambda s: s.position_ms is not None and s.position_ms < PREVIOUS_GOES_BACK_MS,
            SEEK_SETTLE_S,
        )
        await self._log().ainfo(
            "spotify.previous", position_ms=position, rewound=True, landed=landed
        )

    async def _whats_playing(self) -> ToolResult:
        state = self._require_client().state
        book = await self._loaded_book()
        index = book.index_of(item_uri(state.item)) if book is not None else None
        if book is not None and index is not None:
            lang = self._lang()
            chapter = lines.chapter_name(lang, book.chapters[index].name)
            chapter_left, book_left = remaining_ms(book, index, position_now(state) or 0)
            return ToolResult(
                output=json.dumps(
                    {
                        "ok": True,
                        "book": book.title,
                        "by": book.author,
                        "chapter": chapter,
                        "then": lines.then(
                            lang,
                            "status",
                            title=book.title,
                            author=book.author or "?",
                            chapter=chapter,
                            chapter_left=lines.duration(lang, chapter_left),
                            book_left=lines.duration(lang, book_left),
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        title, artists = describe_item(state.item)
        if not title:
            return ToolResult(
                output=json.dumps(
                    {
                        "ok": False,
                        "reason": "nothing_playing",
                        "say": self._say("nothing_playing"),
                    },
                    ensure_ascii=False,
                )
            )
        payload: dict[str, Any] = {"ok": True, "title": title, "by": artists}
        # An info tool: the model speaks the answer in a follow-up round
        # (a tool with an AudioStream gets none). The press that asked
        # paused the music; the framework resumes it after the answer.
        lang = self._lang()
        line = _SAY_SONG[lang].format(title=title, by=artists or "?")
        payload["then"] = _THEN[lang].format(line=line)
        return ToolResult(output=json.dumps(payload, ensure_ascii=False))

    # --- Books (T1.40) ---

    async def _play_book(self, title: str, *, from_beginning: bool, confirmed: bool) -> ToolResult:
        blocked = self._unavailable()
        if blocked:
            return self._then(blocked)
        memory = self._require_memory()
        log = self._log()
        if not title.strip():
            last = await memory.last_book()
            if last is None:
                return self._then("nothing_started")
            target = Match(
                uri=str(last["uri"]),
                kind="audiobook",
                title=str(last.get("title") or ""),
                artist=last.get("author"),
            )
            source = "last"
        else:
            from_list = self._from_last_list(title)
            shelf = await self._current_shelf() if from_list is None else None
            on_shelf = shelves.match_title(shelf, title) if shelf is not None else None
            if from_list is not None:
                target, source = from_list, "list"
            elif on_shelf is not None:
                # The curated edition, no "es ...?" question: or his own edition of
                # it if he started one, so it continues (T1.45 critic).
                target = await self._edition_heard(on_shelf) or Match(
                    uri=on_shelf.uri,
                    kind="audiobook",
                    title=on_shelf.title,
                    artist=on_shelf.author,
                    about=on_shelf.pitch,
                )
                source = "shelf"
            elif shelf is not None and shelves.is_theme(shelf, title):
                # "Pon algo de misterio": a kind of book, not a title.
                # Search would answer with a guide or an obscure book.
                await log.ainfo("spotify.book_redirected", query=title)
                return await self._browse_query(shelf, title, more=False)
            else:
                source = "search"
                if self._search is None:
                    return self._then("search_not_configured")
                try:
                    found = await self._search.candidates(title, "audiobook", limit=5)
                except SearchError as exc:
                    await log.awarning("spotify.search_failed", query=title, error=str(exc))
                    return self._then("search_failed")
                ordered = order_books(found, self._lang())
                if not ordered or is_guide(ordered[0].title, edition=ordered[0].edition):
                    await log.ainfo("spotify.book_not_found", query=title)
                    return self._then("book_not_found")
                target = ordered[0]
                if _weak_match(title, target):
                    # A wrong book costs listening hours: ask first. "Sí"
                    # comes back as this title, found in the list.
                    self._last_list = [target]
                    await log.ainfo("spotify.book_confirm_asked", query=title, title=target.title)
                    return self._then("ask_which", title=target.title, author=target.artist or "?")
        info = await self._book_info(target.uri)
        if from_beginning and info is None:
            # No chapter list (Spotify's /audiobooks blocked): the stream
            # can't find the first chapter and would resume where he was.
            # He asked twice and was told "desde cero" both times
            # (2026-10-14): say so instead; the paused book comes back.
            await log.ainfo("spotify.restart_unavailable", title=target.title)
            return self._then("cant_restart")
        if from_beginning:
            finished = await memory.finished(target.uri)
            place = await memory.place(target.uri)
            where = info.index_of(place.chapter_uri) if info and place else None
            if where is not None and where >= 2 and not (confirmed or finished):
                return self._then("ask_whole_book")
            # Keep the place it leaves, unless that is the start already: a
            # second "desde el principio" must not overwrite the real place.
            if place is not None and not finished and (where or place.position_ms > 60_000):
                await memory.save_place(target.uri, place)
            await memory.clear_finished(
                target.uri, self._label(target.uri, target.title, target.artist)
            )
        elif await memory.finished(target.uri):
            return self._then("finished_replay")
        await log.ainfo(
            "spotify.book_requested",
            query=title,
            source=source,
            title=target.title,
            author=target.artist,
            uri=target.uri,
            from_beginning=from_beginning,
            chapters=len(info.chapters) if info else None,
        )
        return ToolResult(
            output=json.dumps(
                {"ok": True, "playing": target.title, "by": target.artist, "kind": "audiobook"},
                ensure_ascii=False,
            ),
            side_effect=self._stream(start=target, goto=(0, 0) if from_beginning else None),
        )

    async def _book_control(self, action: str, *, seconds: float, chapter: str) -> ToolResult:
        if action not in _BOOK_ACTIONS:
            return ToolResult(
                output=json.dumps({"ok": False, "error": f"unknown_action:{action}"})
            )
        blocked = self._unavailable()
        if blocked:
            return self._then(blocked)
        book = await self._loaded_book()
        if book is None:
            return self._then("no_book")
        state = self._require_client().state
        index = book.index_of(item_uri(state.item))
        await self._log().ainfo(
            "spotify.book_control", action=action, chapter_index=index, asked_chapter=chapter
        )
        if action == "go_to_chapter":
            target = find_chapter(book, chapter)
            if target is None:
                return self._then("no_such_chapter", chapter=chapter)
            return self._moving(book, target, 0)
        if action == "go_back_to_saved_place":
            saved = await self._require_memory().saved_place(book.uri)
            target = book.index_of(saved.chapter_uri) if saved else None
            if saved is None or target is None:
                return self._then("no_saved_place")
            return self._moving(book, target, saved.position_ms)
        if action == "restart_chapter":
            to = 0
        else:
            # back / forward, kept inside the chapter.
            position = position_now(state) or 0
            step = int(max(1.0, seconds) * 1000)
            to = max(0, position - step if action == "back" else position + step)
            if index is not None:
                to = min(to, max(0, book.chapters[index].duration_ms - 1000))
        return ToolResult(
            output=json.dumps({"ok": True, "action": action}),
            side_effect=self._stream(before=(("seek", {"position_ms": to}),)),
        )

    async def _find_books(
        self, query: str, *, like: str = "", more: bool = False, listened: bool = False
    ) -> ToolResult:
        shelf = await self._current_shelf()
        if listened or shelf is None:
            return await self._find_books_open("" if listened else query or like)
        last = self._browse
        if more and last is not None:
            changed = (query.strip() and fold(query) != fold(last["query"])) or (
                like.strip() and fold(like) != fold(last["like"])
            )
            if changed:
                # A new subject with "more": a fresh offer, not the old one.
                await self._log().ainfo(
                    "spotify.shelf_more_new_subject",
                    query=query,
                    like=like,
                    last_query=last["query"],
                    last_like=last["like"],
                )
                more = False
            elif last["mode"] in ("title", "search") and self._last_list:
                # "otros" after one book was offered: books like it.
                return await self._browse_like(shelf, self._last_list[0].title, more=False)
            else:
                query, like = last["query"], last["like"]
        else:
            more = False
        if like.strip():
            return await self._browse_like(shelf, like, more=more)
        if query.strip():
            return await self._browse_query(shelf, query, more=more)
        return await self._browse_categories(shelf, more=more)

    # --- The shelf (T1.45) ---

    async def _current_shelf(self) -> Shelf | None:
        """The shelf, read again if its file changed (logged)."""
        if self._shelf_file is None:
            return None
        change = self._shelf_file.refresh()
        if change is not None:
            log = self._log()
            current = self._shelf_file.shelf
            path = str(self._shelf_file.path)
            if change.event == "loaded" and current is not None:
                await log.ainfo(
                    "spotify.shelf_loaded",
                    path=path,
                    books=len(current.books),
                    categories=len(current.categories),
                    skipped=current.skipped,
                )
            elif change.event == "invalid":
                await log.awarning(
                    "spotify.shelf_invalid",
                    path=path,
                    error=change.error,
                    kept=len(current.books) if current else 0,
                )
            else:
                await log.ainfo("spotify.shelf_missing", path=path)
        return self._shelf_file.shelf

    def _label(self, uri: str, title: str, author: str | None) -> Label:
        """A book's name for its summary: the shelf's, when it's there."""
        shelf = self._shelf_file.shelf if self._shelf_file else None
        book = shelf.by_uri(uri) if shelf else None
        if book is not None:
            return Label(book.title, book.author, "shelf")
        return Label(title, author, "search")

    async def _backfill_summaries(self) -> None:
        """Books heard before summaries existed get one now (place only;
        the chapter fills in on the next play)."""
        memory = self._require_memory()
        for row in await memory.played_books(limit=1_000):
            uri = str(row["uri"])
            if await memory.summary(uri) is not None:
                continue
            place = await memory.place(uri)
            await memory.update_summary(
                uri,
                self._label(uri, str(row.get("title") or ""), row.get("author")),
                position_ms=place.position_ms if place else None,
            )

    def _available_categories(self, shelf: Shelf, listener: Listener) -> list[str]:
        judge = shelves.Judge(shelf, listener)
        return [
            c
            for c in shelf.categories
            if any(b.category == c and judge.excluded(b) is None for b in shelf.books)
        ]

    async def _browse_categories(self, shelf: Shelf, *, more: bool) -> ToolResult:
        """No subject: the kinds of book that still have something for
        him, and his book in progress."""
        memory = self._require_memory()
        lang = self._lang()
        listener = await memory.listener()
        available = self._available_categories(shelf, listener)
        offset = 0
        if more and self._browse is not None and self._browse["mode"] == "categories":
            offset = self._browse["offset"] + CATEGORY_COUNT
            if offset >= len(available):
                offset = 0
        else:
            more = False
        named = available[offset : offset + CATEGORY_COUNT]
        if not named:
            await self._log().ainfo("spotify.shelf_nothing", reason="nothing_left", query="")
            return self._then("books_none")
        listing = shelves.spoken_list(named, lang)
        last = None if more else await memory.last_book()
        if last is not None and not await memory.finished(str(last["uri"])):
            title = str(last.get("title") or "")
            line = lines.shelf_line(lang, "categories_started", categories=listing, title=title)
            self._last_list = [
                Match(
                    uri=str(last["uri"]), kind="audiobook", title=title, artist=last.get("author")
                )
            ]
        else:
            key = "categories_more" if more else "categories"
            line = lines.shelf_line(lang, key, categories=listing)
        self._browse = {"mode": "categories", "query": "", "like": "", "offset": offset}
        await self._log().ainfo(
            "spotify.shelf_browse",
            mode="categories",
            query="",
            like="",
            like_source=None,
            excluded=None,
            offered=named,
            offset=offset,
        )
        return ToolResult(
            output=json.dumps(
                {"ok": True, "categories": named, "then": lines.say(lang, line)},
                ensure_ascii=False,
            )
        )

    async def _browse_query(self, shelf: Shelf, query: str, *, more: bool) -> ToolResult:
        """A subject: a title on the shelf, a category, a tag, a shelf
        author; or a book or author named that isn't on it (open search,
        only results that are that book or by that author)."""
        book = shelves.match_title(shelf, query)
        if book is not None and not more:
            return await self._offer(shelf, mode="title", query=query, topic="", books=[book])
        # A kind of book only when every word is one: "Breve historia del
        # tiempo" is a title, not the history category (T1.45 review).
        theme = shelves.only_theme(shelf, query)
        category = shelves.match_category(shelf, query) if theme else None
        if category is not None:
            books = [b for b in shelf.books if b.category == category]
            return await self._offer(
                shelf, mode="category", query=query, topic=category, books=books, more=more
            )
        tag = shelves.match_tag(shelf, query) if theme else None
        if tag is not None:
            books = [b for b in shelf.books if tag in b.tags]
            return await self._offer(
                shelf, mode="tag", query=query, topic=tag, books=books, more=more
            )
        author = shelves.match_author(shelf, query)
        if author is not None:
            books = [b for b in shelf.books if shelves.same_author(b.author, author)]
            return await self._offer(
                shelf, mode="author", query=query, topic=author, books=books, more=more
            )
        return await self._offer_named(shelf, query, more=more)

    async def _browse_like(self, shelf: Shelf, like: str, *, more: bool) -> ToolResult:
        """Books like one he names: shared category, tags and author. A
        book not on the shelf is looked up once for its author and the
        words of its synopsis."""
        book = shelves.match_title(shelf, like)
        source: str
        if book is not None:
            source, topic = "shelf", book.title
            bonus = shelves.similar(
                shelf, author=book.author, category=book.category, tags=book.tags, besides=book.uri
            )
        elif (author := shelves.match_author(shelf, like)) is not None:
            first = next(b for b in shelf.books if shelves.same_author(b.author, author))
            source, topic = "shelf_author", author
            bonus = shelves.similar(shelf, author=author, category=first.category, tags=())
        else:
            found = await self._look_up(like)
            if found is None:
                await self._log().ainfo("spotify.shelf_nothing", reason="like_unknown", query=like)
                return await self._nothing(shelf)
            source, topic = "search", re.split(r"[(:\[]", found.title)[0].strip()
            bonus = {
                uri: points
                for uri, points in shelves.similar(
                    shelf,
                    author=found.artist or "",
                    category=None,
                    tags=(),
                    words=shelves.synopsis_words(found.about),
                ).items()
                if not shelves.same_book(
                    found.title,
                    found.artist or "",
                    *self._title_author(shelf.by_uri(uri)),
                )
            }
        books = [b for b in shelf.books if b.uri in bonus]
        return await self._offer(
            shelf,
            mode="like",
            query="",
            like=like,
            like_source=source,
            topic=topic,
            books=books,
            bonus=bonus,
            more=more,
        )

    @staticmethod
    def _title_author(book: ShelfBook | None) -> tuple[str, str]:
        return (book.title, book.author) if book else ("", "")

    async def _look_up(self, spoken: str) -> Match | None:
        """One search for a book named: the first that isn't a guide."""
        if self._search is None:
            return None
        try:
            found = await self._search.candidates(spoken, "audiobook", limit=3)
        except SearchError as exc:
            await self._log().awarning("spotify.search_failed", query=spoken, error=str(exc))
            return None
        return next((m for m in found if not is_guide(m.title, m.about, m.edition)), None)

    async def _offer(
        self,
        shelf: Shelf,
        *,
        mode: str,
        query: str,
        topic: str,
        books: list[ShelfBook],
        like: str = "",
        like_source: str | None = None,
        bonus: dict[str, int] | None = None,
        more: bool = False,
    ) -> ToolResult:
        """Rank, drop what he's heard or refused, check what plays, offer
        three in the skill's own words."""
        memory = self._require_memory()
        log = self._log()
        listener = await memory.listener()
        if mode == "title":
            ranked, excluded = books, 0  # he named it: offered even if started
        else:
            ranked, excluded = shelves.rank(books, listener, shelf, bonus=bonus)
        already: list[str] = list(self._browse["offered"]) if more and self._browse else []
        ranked = [b for b in ranked if b.uri not in already]
        if not ranked:
            reason = "no_more" if more else "like_nothing" if not books else "all_excluded"
            await log.ainfo(
                "spotify.shelf_nothing", reason=reason, query=query or like, excluded=excluded
            )
            return await self._nothing(shelf, topic=topic if more else "")
        blocked = self._unavailable()
        if blocked:
            await log.ainfo("spotify.shelf_blocked", reason=blocked, query=query or like)
            return self._then(blocked)
        candidates = []
        for b in ranked:
            # A book he named: his own edition if he started one, so "ponlo"
            # continues it (T1.45 review).
            heard = await self._edition_heard(b) if mode == "title" else None
            candidates.append(
                Match(
                    uri=heard.uri if heard else b.uri,
                    kind="audiobook",
                    title=b.title,
                    artist=b.author,
                    languages=(b.language,),
                    about=b.pitch,
                )
            )
        offered, hours_out = await self._playable_only(candidates, OFFER_COUNT)
        if hours_out:
            return self._then("no_hours")
        if not offered:
            await log.ainfo("spotify.shelf_nothing", reason="none_playable", query=query or like)
            return self._then("none_available")
        return await self._said_offer(
            mode=mode,
            query=query,
            like=like,
            like_source=like_source,
            topic=topic,
            offered=offered,
            excluded=excluded,
            offset=len(already),
            already=already,
        )

    async def _said_offer(
        self,
        *,
        mode: str,
        query: str,
        like: str,
        like_source: str | None,
        topic: str,
        offered: list[Match],
        excluded: int,
        offset: int,
        already: list[str],
    ) -> ToolResult:
        lang = self._lang()
        more = bool(already)
        if mode == "title" or (mode == "search" and not topic):
            head = ""
        elif mode == "search":
            head = "head_found"
        elif mode == "like":
            head = "head_more_like" if more else "head_like"
        else:
            head = "head_more" if more else "head"
        line = lines.offer(
            lang, head, topic, [(m.title, m.artist or "?", m.about) for m in offered]
        )
        await self._require_memory().mark_offered([m.uri for m in offered])
        self._last_list = offered
        self._browse = {
            "mode": mode,
            "query": query,
            "like": like,
            "offset": offset,
            "offered": [*already, *(m.uri for m in offered)],
        }
        await self._log().ainfo(
            "spotify.shelf_browse",
            mode=mode,
            query=query,
            like=like,
            like_source=like_source,
            excluded=excluded,
            offered=[m.title for m in offered],
            offset=offset,
        )
        return ToolResult(
            output=json.dumps(
                {
                    "ok": True,
                    "books": [
                        {"n": i + 1, "title": m.title, "by": m.artist, "about": m.about}
                        for i, m in enumerate(offered)
                    ],
                    "then": lines.say(lang, line),
                },
                ensure_ascii=False,
            )
        )

    async def _offer_named(self, shelf: Shelf, query: str, *, more: bool) -> ToolResult:
        """Not on the shelf: open search, keeping only the book named or
        books by the author named. A theme never gets here (it would have
        matched the shelf) and gets nothing from search."""
        log = self._log()
        if self._search is None or more:
            await log.ainfo("spotify.shelf_nothing", reason="no_match", query=query)
            return await self._nothing(shelf)
        blocked = self._unavailable()
        if blocked:
            await log.ainfo("spotify.shelf_blocked", reason=blocked, query=query)
            return self._then(blocked)
        try:
            found = await self._search.candidates(query, "audiobook", limit=8)
        except SearchError as exc:
            await log.awarning("spotify.search_failed", query=query, error=str(exc))
            return self._then("search_failed")
        books = [
            m
            for m in order_books(found, self._lang())
            if not is_guide(m.title, m.about, m.edition)
        ]
        titled = [m for m in books if shelves.title_matches(query, m.title, m.artist or "")]
        # An author's books follow the shelf's rules: never one he finished,
        # didn't like, left or Spotify refused (T1.45 review). A book he
        # names is offered whatever its state, as on the shelf.
        judge = shelves.Judge(shelf, await self._require_memory().listener())
        by_author = [
            m
            for m in books
            if shelves.author_named(query, m.artist or "")
            and judge.excluded_ids(judge.ids_for(m.title, m.artist or "", m.uri)) is None
        ]
        named = titled or by_author
        if not named:
            await log.ainfo("spotify.shelf_nothing", reason="no_match", query=query)
            return await self._nothing(shelf)
        # Every edition of a title named gets its chance; one is offered.
        offered, hours_out = await self._playable_only(named, 1 if titled else OFFER_COUNT)
        if hours_out:
            return self._then("no_hours")
        if not offered:
            await log.ainfo("spotify.shelf_nothing", reason="none_playable", query=query)
            return self._then("none_available")
        offered = [
            Match(
                uri=m.uri,
                kind=m.kind,
                title=m.title,
                artist=m.artist,
                languages=m.languages,
                about=_first_sentence(_blurb(m.about)),
            )
            for m in offered
        ]
        topic = "" if titled else (offered[0].artist or query).split(",")[0].strip()
        return await self._said_offer(
            mode="search",
            query=query,
            like="",
            like_source=None,
            topic=topic,
            offered=offered,
            excluded=0,
            offset=0,
            already=[],
        )

    async def _nothing(self, shelf: Shelf, *, topic: str = "") -> ToolResult:
        """Nothing fits: say what kinds of book there are."""
        lang = self._lang()
        listener = await self._require_memory().listener()
        named = self._available_categories(shelf, listener)[:CATEGORY_COUNT]
        listing = shelves.spoken_list(named or list(shelf.categories[:CATEGORY_COUNT]), lang)
        if topic:
            line = lines.shelf_line(lang, "no_more", topic=topic, categories=listing)
        else:
            line = lines.shelf_line(lang, "nothing", categories=listing)
        self._browse = None
        return ToolResult(
            output=json.dumps(
                {"ok": False, "reason": "shelf_nothing", "then": lines.say(lang, line)},
                ensure_ascii=False,
            )
        )

    async def _edition_heard(self, book: ShelfBook) -> Match | None:
        """His own edition of a shelf book, if he already started one: he
        continues it instead of starting the shelf's from zero."""
        for row in await self._require_memory().played_books(limit=1_000):
            if row["uri"] == book.uri or shelves.same_book(
                book.title, book.author, str(row.get("title") or ""), str(row.get("author") or "")
            ):
                return Match(
                    uri=str(row["uri"]),
                    kind="audiobook",
                    title=str(row.get("title") or book.title),
                    artist=row.get("author") or book.author,
                )
        return None

    async def _book_opinion(self, opinion: str, title: str) -> ToolResult:
        """'Me gustó', 'no me gustó': kept per book, it moves the offers."""
        if opinion not in OPINIONS:
            return ToolResult(output=json.dumps({"ok": False, "error": f"unknown:{opinion}"}))
        lang = self._lang()
        target: tuple[str, str, str | None] | None = None
        how = ""
        if title.strip():
            picked = self._from_last_list(title)
            shelf = await self._current_shelf()
            book = shelves.match_title(shelf, title) if shelf and picked is None else None
            if picked is not None:
                target, how = (picked.uri, picked.title, picked.artist), "list"
            elif book is not None:
                heard = await self._edition_heard(book)
                uri = heard.uri if heard else book.uri
                target, how = (uri, book.title, book.author), "shelf"
            else:
                for row in await self._require_memory().played_books(limit=1_000):
                    row_title = str(row.get("title") or "")
                    if title_score(fold(title), fold(row_title)) >= 0.6:
                        target, how = (str(row["uri"]), row_title, row.get("author")), "played"
                        break
        else:
            loaded = await self._loaded_book()
            if loaded is not None:
                target, how = (loaded.uri, loaded.title, loaded.author), "current"
            elif (last := await self._require_memory().last_book()) is not None:
                target = (str(last["uri"]), str(last.get("title") or ""), last.get("author"))
                how = "last"
        if target is None:
            await self._log().ainfo(
                "spotify.book_opinion", title=title, opinion=opinion, source=None
            )
            return ToolResult(
                output=json.dumps(
                    {
                        "ok": False,
                        "reason": "which_book",
                        "then": lines.say(lang, lines.shelf_line(lang, "opinion_which")),
                    },
                    ensure_ascii=False,
                )
            )
        uri, name, author = target
        label = self._label(uri, name, author)
        await self._require_memory().set_opinion(uri, opinion, label)
        await self._log().ainfo(
            "spotify.book_opinion", title=label.title, opinion=opinion, source=how, uri=uri
        )
        return ToolResult(
            output=json.dumps(
                {
                    "ok": True,
                    "book": label.title,
                    "opinion": opinion,
                    "then": lines.say(lang, lines.shelf_line(lang, opinion, title=label.title)),
                },
                ensure_ascii=False,
            )
        )

    async def _find_books_open(self, query: str) -> ToolResult:
        """T1.40's recommendations, for a persona without a shelf: the
        books started here, or an open search, each book checked."""
        memory = self._require_memory()
        lang = self._lang()
        if not query.strip():
            rows = await memory.played_books(3)
            if not rows:
                return self._then("books_none")
            books = [
                Match(
                    uri=str(r["uri"]),
                    kind="audiobook",
                    title=str(r.get("title") or ""),
                    artist=r.get("author"),
                )
                for r in rows
            ]
            self._last_list = books
            entries: list[dict[str, Any]] = [
                {"n": i + 1, "title": b.title, "by": b.artist, "listened_here": True}
                for i, b in enumerate(books)
            ]
        else:
            if self._search is None:
                return self._then("search_not_configured")
            blocked = self._unavailable()
            if blocked:
                return self._then(blocked)
            try:
                found = await self._search.candidates(query, "audiobook", limit=8)
            except SearchError as exc:
                await self._log().awarning("spotify.search_failed", query=query, error=str(exc))
                return self._then("search_failed")
            candidates = [
                m for m in order_books(found, lang) if not is_guide(m.title, edition=m.edition)
            ]
            if not candidates:
                await self._log().ainfo("spotify.book_not_found", query=query)
                return self._then("book_not_found")
            books, hours_out = await self._playable_only(candidates, 3)
            if hours_out:
                return self._then("no_hours")
            if not books:
                return self._then("none_available")
            infos = await asyncio.gather(
                *(self._book_info(m.uri) for m in books), return_exceptions=True
            )
            entries = []
            for i, (m, info) in enumerate(zip(books, infos, strict=True)):
                entry: dict[str, Any] = {
                    "n": i + 1,
                    "title": m.title,
                    "by": m.artist,
                    "listened_here": await memory.played(m.uri),
                }
                if isinstance(info, BookInfo) and info.chapters:
                    total = sum(c.duration_ms for c in info.chapters)
                    entry["length"] = lines.duration(lang, total)
                about = _first_sentence(m.about)
                if about:
                    entry["about"] = about
                entries.append(entry)
            self._last_list = books
        await self._log().ainfo(
            "spotify.books_listed", query=query, titles=[e["title"] for e in entries]
        )
        return ToolResult(
            output=json.dumps(
                {
                    "ok": True,
                    "books": entries,
                    "then": lines.listing(lang, started=not query.strip()),
                },
                ensure_ascii=False,
            )
        )

    async def _move_chapter(self, book: BookInfo, step: int) -> ToolResult:
        index = book.index_of(item_uri(self._require_client().state.item))
        if index is None:
            # Not on a chapter we know: let Soloist move by itself.
            command = "skip_next" if step > 0 else "skip_prev"
            return ToolResult(
                output=json.dumps({"ok": True, "action": command}),
                side_effect=self._stream(before=((command, {}),)),
            )
        target = index + step
        if target < 0:
            return self._then("first_chapter")
        if target >= len(book.chapters):
            return self._then("last_chapter")
        return self._moving(book, target, 0)

    def _moving(self, book: BookInfo, target: int, position_ms: int) -> ToolResult:
        return ToolResult(
            output=json.dumps(
                {"ok": True, "chapter": book.chapters[target].name}, ensure_ascii=False
            ),
            side_effect=self._stream(goto=(target, position_ms)),
        )

    async def _book_to_restart(self) -> Match | None:
        """The book "sigue" means when Soloist has nothing playing."""
        context = self._require_client().state.context_uri or ""
        if _is_book(context):
            title = self._book.title if self._book and self._book.uri == context else ""
            return Match(uri=context, kind="audiobook", title=title, artist=None)
        if not self._last_was_book:
            return None
        last = await self._require_memory().last_book()
        if last is None:
            return None
        return Match(
            uri=str(last["uri"]),
            kind="audiobook",
            title=str(last.get("title") or ""),
            artist=last.get("author"),
        )

    def _from_last_list(self, spoken: str) -> Match | None:
        """'El segundo', 'ese', or a title from the list just read out."""
        if not self._last_list:
            return None
        spoken = spoken.strip()
        # A title on the list first: it may hold a number word ("Los tres
        # mosqueteros"), which must not read as "the third".
        best = max(self._last_list, key=lambda m: title_score(fold(spoken), fold(m.title)))
        if title_score(fold(spoken), fold(best.title)) >= 0.8:
            return best
        pick = _ordinal(spoken, len(self._last_list))
        if pick is not None:
            return self._last_list[pick]
        digits = "".join(w for w in fold(spoken).split() if w.isdigit())
        if digits.isdigit() and 1 <= int(digits) <= len(self._last_list):
            return self._last_list[int(digits) - 1]  # "el 2", as transcribed
        return best if title_score(fold(spoken), fold(best.title)) >= 0.6 else None

    async def _book_info(self, uri: str) -> BookInfo | None:
        if self._search is None:
            return None
        try:
            info = await self._search.book(uri)
        except SearchError as exc:
            await self._log().awarning("spotify.book_info_failed", uri=uri, error=str(exc))
            return None
        return info

    async def _loaded_book(self) -> BookInfo | None:
        """The book Soloist has loaded, with its chapters; None for music."""
        context = self._require_client().state.context_uri
        if not context or not _is_book(context):
            return None
        if self._book is None or self._book.uri != context:
            self._book = await self._book_info(context)
        return self._book

    async def _refusal_cause(self, uri: str) -> str | None:
        """Why Spotify refused a book, when it's known without playing
        another: the month's budget spent is the hours; a book that played
        lately means the hours are there, so it's this book (paid apart or
        not offered: the same to the listener, Mario 2026-10-12). None: a
        book known to play here was refused, and only trying another one
        tells the book from the hours (`_explain_refusal`)."""
        memory = self._require_memory()
        budget = self._hours_per_month
        if budget is not None and await memory.seconds_this_month() >= budget * 3600 - 600:
            return "book_no_hours"
        if not self._hours_recently_ok() and await memory.playable(uri):
            return None
        await memory.mark_unavailable(uri)
        return "book_unavailable"

    async def _explain_refusal(self, uri: str) -> None:
        """A book known to play here was refused: after its stream has
        ended (a check needs the player, and with no stream reading it,
        nothing reaches a speaker), try a book known to play. It plays:
        the hours are there and this book isn't available. Refused too: the
        hours ran out. No other book to try: a book that really played here
        means the hours, a check's "ok" alone means this book."""
        for _ in range(60):  # the stream's finally, up to 3 s
            if self._streams == 0:
                break
            await asyncio.sleep(0.05)
        memory = self._require_memory()
        client = self._require_client()
        control = await memory.any_playable(besides=uri)
        result = None
        if control is not None and self._streams == 0 and self._unavailable() is None:
            result = await self._probe(control)
            if self._streams == 0:
                client.send("pause")
        if result == "ok":
            self._hours_ok_at = time.monotonic()
            cause = "book_unavailable"
        elif result == "no":
            cause = "book_no_hours"
        else:
            cause = "book_no_hours" if await memory.played(uri) else "book_unavailable"
        if cause == "book_unavailable":
            await memory.mark_unavailable(uri)
        await self._log().ainfo(
            "spotify.refusal_explained",
            uri=uri,
            control=control,
            control_result=result,
            cause=cause,
        )
        self._narrate(cause)

    async def _playable_only(self, candidates: list[Match], want: int) -> tuple[list[Match], bool]:
        """The first `want` candidates Spotify will play here: known ones
        from memory, the rest tried for a moment with nothing reaching a
        speaker (Soloist's sink has no reader between streams), then
        whatever was loaded is put back. (books, hours_ran_out). A refusal
        while a book known to play is refused too means no hours, not that
        every candidate is unavailable."""
        memory = self._require_memory()
        client = self._require_client()
        log = self._log()
        # Never while a stream plays: the check would take the player. In a
        # user's turn the press has already paused it.
        can_check = self._streams == 0 and self._unavailable() is None
        before = (client.state.context_uri, client.state.status)
        kept: list[Match] = []
        refused: list[Match] = []
        tried = 0
        try:
            for match in candidates:
                if len(kept) == want:
                    break
                if await memory.unavailable(match.uri):
                    continue  # first: a book refused lately, even one that played once
                if await memory.playable(match.uri):
                    kept.append(match)
                    continue
                if not can_check or tried >= MAX_PROBES:
                    continue
                tried += 1
                result = await self._probe(match.uri)
                if result == "ok":
                    self._hours_ok_at = time.monotonic()
                    await memory.mark_ok(match.uri, match.title, match.artist)
                    kept.append(match)
                elif result == "no":
                    refused.append(match)
            if refused and not self._hours_recently_ok():
                # Nothing played this round or lately: is it the books, or
                # the month's hours? Try one book known to play.
                control = await memory.any_playable(besides=refused[0].uri)
                if control is not None:
                    tried += 1
                    if await self._probe(control) == "no":
                        await log.awarning("spotify.book_hours_out", control=control)
                        return [], True
                    self._hours_ok_at = time.monotonic()
            for match in refused:
                await memory.mark_unavailable(match.uri)
        finally:
            if tried:
                await self._put_back(*before)
        await log.ainfo(
            "spotify.books_checked",
            candidates=len(candidates),
            tried=tried,
            kept=[m.title for m in kept],
            checked=can_check,
        )
        return kept, False

    def _hours_recently_ok(self) -> bool:
        return (
            self._hours_ok_at is not None and time.monotonic() - self._hours_ok_at < HOURS_OK_FOR_S
        )

    async def _probe(self, uri: str) -> str:
        """Try a book for a moment: "ok" if it keeps playing, "no" if
        Spotify stops it (it does within 0.3-1.2 s), "unknown" if it never
        loaded."""
        client = self._require_client()
        errors = client.state.error_count
        client.send("play", uri=uri)
        loaded = await client.wait_until(
            lambda s: s.context_uri == uri or s.error_count > errors or not s.connected,
            PROBE_LOAD_S,
        )
        if not loaded or client.state.context_uri != uri:
            result = "unknown"
        else:
            # Refused: "playing", the first chapter, "buffering", then idle
            # within about a second. Accepted: still playing the book at the
            # end of the window. A refusal decides early; an "ok" never does
            # (the first "playing" comes before Spotify's verdict).
            await client.wait_until(
                lambda s: s.status == "idle" or s.context_uri != uri, PROBE_OK_S
            )
            state = client.state
            playing = state.status == "playing" and state.context_uri == uri
            result = "no" if state.status == "idle" else "ok" if playing else "unknown"
        await self._log().ainfo("spotify.book_probe", uri=uri, result=result)
        return result

    async def _put_back(self, context: str | None, status: str) -> None:
        """Load again what was there before a check, paused: a book resumes
        at its own place; music restarts its album or list."""
        client = self._require_client()
        if context:
            client.send("play", uri=context)
            await client.wait_until(
                lambda s: s.context_uri == context and s.status in ("playing", "idle"),
                PROBE_LOAD_S,
            )
        client.send("pause")
        await client.wait_until(lambda s: s.status != "playing", 2.0)
        await self._log().ainfo("spotify.put_back", context=context, was=status)

    async def _book_stopped(
        self, book: BookInfo, state: PlayerState, chapter: str | None, *, left: bool
    ) -> str:
        """Every way a book stops is said (critic B1): its end, or a cut."""
        index = book.index_of(chapter)
        position = position_now(state)
        last = len(book.chapters) - 1
        near_end = (
            position is None
            or position < 1_000  # Soloist rewinds a chapter that ran out
            or position >= book.chapters[last].duration_ms - END_MARGIN_MS
        )
        if index is not None and index == last and (left or near_end):
            self._spawn(
                self._require_memory().mark_finished(
                    book.uri, self._label(book.uri, book.title, book.author)
                )
            )
            await self._log().ainfo("spotify.book_finished", title=book.title, left=left)
            if left:
                # Spotify went on to something else by itself.
                self._require_client().send("pause")
            self._narrate("book_finished", title=book.title)
            return "book_finished"
        await self._log().awarning(
            "spotify.book_stopped",
            title=book.title,
            chapter_index=index,
            position_ms=position,
            context=state.context_uri,
            left=left,
        )
        self._narrate("book_cut")  # never a silent stop
        return "book_left" if left else "book_cut"

    async def _go_to(
        self, client: SoloistClient, generation: int, book: BookInfo, target: int, position: int
    ) -> bool:
        """Reach a chapter inside the book: playing a chapter's own URI
        drops the book (measured 2026-10-12), so step with skips. At a
        chapter's start `skip_prev` goes back one; mid-chapter it restarts."""
        want = book.chapters[target].uri
        for _ in range(3):
            await client.wait_until(
                lambda s: book.index_of(item_uri(s.item)) is not None, NAV_SETTLE_S
            )
            current = book.index_of(item_uri(client.state.item))
            if current is None:
                return False
            if current == target:
                break
            steps = target - current
            if steps < 0:
                client.send("seek", generation=generation, position_ms=0)
                await asyncio.sleep(SKIP_GAP_S)
            for _ in range(abs(steps)):
                client.send("skip_next" if steps > 0 else "skip_prev", generation=generation)
                await asyncio.sleep(SKIP_GAP_S)
            await client.wait_until(
                lambda s: item_uri(s.item) == want, NAV_SETTLE_S + SKIP_GAP_S * abs(steps)
            )
        if item_uri(client.state.item) != want:
            await self._log().awarning(
                "spotify.book_nav_failed", target=target, at=item_uri(client.state.item)
            )
            return False
        client.send("seek", generation=generation, position_ms=position)
        await client.wait_until(
            lambda s: s.position_ms is not None and abs(s.position_ms - position) < 3_000, 2.0
        )
        await self._log().ainfo(
            "spotify.book_nav",
            book=book.title,
            chapter=book.chapters[target].name,
            chapter_index=target,
            chapters=len(book.chapters),
        )
        return True

    async def _write_place(self, book: BookInfo, place: Place) -> None:
        """The book's place, and its summary for the page."""
        await self._require_memory().set_place(
            book.uri,
            place,
            self._label(book.uri, book.title, book.author),
            chapter=book.index_of(place.chapter_uri),
            chapters=len(book.chapters),
        )

    def _resume_rewind(self) -> list[_Command]:
        """A resumed book backs up 2 s plus a tenth of the pause, at most
        20 s (critic M6), so it doesn't start mid-word."""
        state = self._require_client().state
        if not (state.context_uri and _is_book(state.context_uri)) or self._paused_at is None:
            return []
        position = position_now(state)
        if position is None:
            return []
        back = rewind_ms(time.monotonic() - self._paused_at)
        return [("seek", {"position_ms": max(0, position - back)})]

    def _place_now(self, book: BookInfo, sent: tuple[str, int] | None) -> tuple[Place, int] | None:
        """Where the book is now, and how far Soloist ran ahead of the audio
        sent (0 if it didn't). `sent` is the chapter and position the audio
        sent so far reaches: the place never goes much past it."""
        state = self._require_client().state
        chapter = item_uri(state.item)
        if chapter is None or book.index_of(chapter) is None:
            return None
        position = position_now(state) or 0
        if sent is not None and sent[0] == chapter:
            capped = capped_position(position, sent[1])
            if capped is not None:
                return Place(chapter, capped), position - sent[1]
        return Place(chapter, position), 0

    # --- Streaming ---

    def _stream(
        self,
        *,
        start: Match | None = None,
        before: tuple[_Command, ...] = (),
        goto: tuple[int, int] | None = None,
    ) -> AudioStream:
        """An AudioStream whose first run starts `start` (or resumes), sends
        `before` first and moves to `goto` (chapter, position) in a book;
        later runs, after a reminder or call paused it, resume."""
        # Flips once the first run has actually sent its play: a run
        # cancelled before that (say, during the capture connect) must
        # leave the next run to start `start`, not resume something else.
        progress = {"played": False, "moved": goto is None}
        context = self._require_client().state.context_uri or ""
        is_book = (start is not None and start.kind == "audiobook") or (
            start is None and _is_book(context)
        )

        async def factory() -> AsyncIterator[bytes]:
            initial = not progress["played"]
            run = self._run(
                start=start if initial else None,
                before=before if initial else (),
                # A press mid-move: the resume finishes the move (critic M1).
                goto=goto if initial or not progress["moved"] else None,
                earcon=initial and start is not None,
                progress=progress,
            )
            # aclosing: on cancel, run's finally (pause, close the capture)
            # happens now, not whenever the generator is collected.
            async with contextlib.aclosing(run):
                async for chunk in run:
                    yield chunk

        async def on_patience_expired() -> None:
            # A call outlasted the patience window; it won't come back on
            # its own. Called from the focus manager: schedule only.
            self._narrate("book_patience" if is_book else "patience_expired")

        if start is not None:
            label = start.title
        elif is_book and self._book is not None:
            label = self._book.title
        else:
            label = "Spotify"
        # Loudness (T1.47): a book keeps its narrator's level from one
        # session to the next; music masters vary by track, one key.
        book_uri = start.uri if start is not None else context
        loudness_key = f"spotify:book:{book_uri}" if is_book and book_uri else "spotify:music"
        return AudioStream(
            factory=factory,
            content_type=ContentType.NONMIXABLE,
            label=label,
            loudness_key=loudness_key,
            preroll_ms=len(self._earcon) * 1000 // 48_000 if self._earcon and start else 0,
            patience=self._patience,
            on_patience_expired=on_patience_expired,
        )

    async def _run(
        self,
        *,
        start: Match | None,
        before: tuple[_Command, ...],
        goto: tuple[int, int] | None,
        earcon: bool,
        progress: dict[str, bool],
    ) -> AsyncGenerator[bytes]:
        client = self._require_client()
        log = self._log()
        generation = client.new_generation()
        requested = time.monotonic()
        reason = "cancelled"
        writer: asyncio.StreamWriter | None = None
        book: BookInfo | None = None
        ident: tuple[str, str, str | None] | None = None  # (uri, title, author) of a book
        nav: asyncio.Task[bool] | None = None
        book_start = start is not None and start.kind == "audiobook"
        counted = False
        # A resume or a chapter move of the book that's loaded is a book too.
        book_stream = book_start or (start is None and _is_book(client.state.context_uri or ""))
        audio_ms = 0
        # Where the audio sent from this chapter started: (chapter, Soloist's
        # position then, audio_ms then).
        anchor: tuple[str, int, int] | None = None

        def sent_until() -> tuple[str, int] | None:
            """The chapter and position the audio sent so far reaches."""
            if anchor is None:
                return None
            return anchor[0], anchor[1] + audio_ms - anchor[2]

        try:
            try:
                reader, writer = await open_capture(self._pcm_host, self._pcm_port)
            except CaptureError as exc:
                reason = "capture_unreachable"
                await log.awarning("spotify.capture_failed", error=str(exc))
                self._narrate("book_offline" if book_stream else "player_offline")
                return

            errors_before = client.state.error_count
            commands = list(before)
            if start is None and not before and goto is None:
                commands.extend(self._resume_rewind())
            # A seek sent before "play" lands after Soloist has already
            # played about a second from its buffer: Mario heard the old
            # spot, a cut, then the new one (2026-10-12). So seeks go after
            # the start, and the walkie stays silent until it's there.
            seek_to = next((int(f["position_ms"]) for c, f in commands if c == "seek"), None)
            for command, fields in commands:
                if command != "seek":
                    client.send(command, generation=generation, **fields)
            if start is not None:
                client.send("play", uri=start.uri, generation=generation)
            else:
                client.send("play", generation=generation)
            progress["played"] = True
            if earcon and self._earcon:
                yield self._earcon

            def started(s: PlayerState) -> bool:
                if s.error_count > errors_before or not s.connected:
                    return True
                # Soloist reports "playing" for the old context for an
                # instant before loading a new one; wait for ours.
                return s.status == "playing" and (start is None or s.context_uri == start.uri)

            await client.wait_until(started, START_TIMEOUT_S)
            state = client.state
            if state.status != "playing" or (start is not None and state.context_uri != start.uri):
                reason = "start_failed"
                await log.awarning(
                    "spotify.start_failed",
                    status=state.status,
                    context=state.context_uri,
                    error=state.last_error,
                    connected=state.connected,
                )
                self._narrate("book_failed" if book_stream else "start_failed")
                return
            # Volume belongs to the client (SetVolume); keep Soloist at full
            # so a phone can't silently turn this device down.
            client.send("set_volume", volume=100, generation=generation)
            self._streams += 1
            counted = True
            book = await self._loaded_book()
            # Which book this is, from its link when Spotify won't give the
            # chapters (a 429 on /audiobooks blocked them for 19 hours on
            # 2026-10-13, and the month's listening stopped being counted).
            ident = await self._book_ident(book, start, client) if book_stream else None
            self._last_was_book = ident is not None
            if start is not None and book is None and not book_start:
                self._spawn(self._require_memory().set_last_music(start))
            if book is not None and goto is not None:
                nav = asyncio.create_task(self._go_to(client, generation, book, *goto))
            seek_sent_at: float | None = None
            if seek_to is not None:
                client.send("seek", generation=generation, position_ms=seek_to)
                seek_sent_at = time.monotonic()
            chapter = item_uri(client.state.item)
            chapter_at = book.index_of(chapter) if book else None
            await log.ainfo(
                "spotify.play_started",
                latency_ms=int((time.monotonic() - requested) * 1000),
                resumed=start is None,
                context=state.context_uri,
                book=book.title if book else None,
                chapter=book.chapters[chapter_at].name
                if book and chapter_at is not None
                else None,
                # The book's progress for huxley-watch's phone app (T1.46).
                chapter_index=chapter_at,
                chapters=len(book.chapters) if book else None,
                goto=goto,
            )
            started_at = time.monotonic()
            # Spotify refuses a book chapter by chapter (no hours left): a
            # stop this soon after any play, chapter change or move is a
            # refusal, not the end or a cut (critic round 2, B1).
            window_at = started_at
            place_at = started_at
            marked = False
            seen_active = False
            paused_since: float | None = None
            chunks = 0
            async for chunk in read_chunks(reader):
                state = client.state
                if not state.connected:
                    reason = "player_offline"
                    self._narrate("book_offline" if book else "player_offline")
                    return
                # Soloist turns active a moment after playback starts; only
                # active-then-not means someone picked another device.
                if state.is_active:
                    seen_active = True
                elif seen_active:
                    reason = "takeover"
                    self._narrate("book_moved" if book else "takeover")
                    return
                if state.status == "idle":
                    moving = nav is not None and not nav.done()
                    soon = time.monotonic() - window_at < REFUSED_S
                    if (start is not None or book is not None) and (soon or moving):
                        reason = "refused"
                        refused = book.uri if book is not None else start.uri if start else ""
                        is_book = book is not None or book_start
                        cause = await self._refusal_cause(refused) if is_book else ""
                        await log.awarning(
                            "spotify.start_refused",
                            uri=refused,
                            resumed=start is None,
                            moving=moving,
                            chunks=chunks,
                            cause=cause if cause is not None else "checking",
                        )
                        if cause is None:
                            # After this stream ends: try a book known to play.
                            self._spawn(self._explain_refusal(refused))
                        else:
                            self._narrate(cause or "start_failed")
                        return
                    if book is not None:
                        reason = await self._book_stopped(book, state, chapter, left=False)
                        return
                    reason = "finished"
                    return
                if seek_sent_at is not None:
                    # Silent until Soloist plays at the new spot (or 2 s).
                    landed = (
                        state.status == "playing"
                        and seek_to is not None
                        and (at := position_now(state)) is not None
                        and abs(at - seek_to) < 2_000
                    )
                    if not landed and time.monotonic() - seek_sent_at < SEEK_SETTLE_S:
                        continue
                    seek_sent_at = None
                if nav is not None:
                    if not nav.done():
                        continue  # silent while it moves between chapters
                    if nav.cancelled() or nav.exception() is not None or not nav.result():
                        self._narrate("book_nav_failed")
                    progress["moved"] = True
                    nav = None
                    chapter = item_uri(client.state.item)
                    window_at = time.monotonic()
                if book is not None:
                    if state.context_uri and state.context_uri != book.uri:
                        # Spotify left the book by itself (autoplay at its
                        # end), or a phone put something else on.
                        reason = await self._book_stopped(book, state, chapter, left=True)
                        return
                    current = item_uri(state.item)
                    if current and current != chapter and book.index_of(current) is not None:
                        chapter = current
                        window_at = time.monotonic()
                        if self._sleep_s and time.monotonic() - requested >= self._sleep_s:
                            # No press for a long while: he may be asleep.
                            # Pause at the chapter boundary, without a word.
                            reason = "asleep"
                            await log.ainfo(
                                "spotify.book_asleep",
                                minutes=round((time.monotonic() - requested) / 60),
                            )
                            return
                    if time.monotonic() - place_at >= PLACE_EVERY_S:
                        place_at = time.monotonic()
                        if (found := self._place_now(book, sent_until())) is not None:
                            self._spawn(self._write_place(book, found[0]))
                if state.status == "paused":
                    # Paused from a Spotify app: let the speaker go.
                    paused_since = paused_since or time.monotonic()
                    if time.monotonic() - paused_since > PAUSED_GRACE_S:
                        reason = "paused_elsewhere"
                        if book is not None:
                            self._narrate("book_patience")  # never a silent stop
                        return
                else:
                    paused_since = None
                if (
                    book is not None
                    and (now := item_uri(state.item)) is not None
                    and (anchor is None or anchor[0] != now)
                    and (at := position_now(state)) is not None
                ):
                    anchor = (now, at, audio_ms)
                chunks += 1
                audio_ms += CHUNK_MS
                if ident is not None and not marked and audio_ms >= PLAYED_AFTER_MS:
                    marked = True
                    self._hours_ok_at = time.monotonic()
                    if ident[1]:  # never remember a book under an empty title
                        memory = self._require_memory()
                        self._spawn(memory.mark_played(*ident))
                        self._spawn(memory.set_last_book(*ident))
                yield chunk
            # The capture closed under us (the container's sound server is
            # down, or the container restarted). Behind Docker on a Mac the
            # connect succeeds and closes at once, so a stream with no audio
            # at all is the offline case.
            reason = "capture_closed"
            await log.awarning("spotify.capture_closed", chunks=chunks)
            if ident is not None:
                self._narrate("book_cut" if chunks else "book_offline")
            else:
                self._narrate("interrupted" if chunks else "player_offline")
        finally:
            if counted:
                self._streams -= 1
            if writer is not None:
                writer.close()
            # Mid-move, the chapter it's on is no place to remember.
            mid_move = nav is not None and not nav.done()
            if nav is not None:
                nav.cancel()
            # Never await Soloist here: this runs in the coordinator's
            # cancel path. Queue the pause; a newer stream drops it. Not
            # when the music moved, was paused elsewhere, or ended.
            if reason not in _NO_PAUSE:
                client.send("pause", generation=generation)
            where: int | None = None  # the book's chapter at the end (T1.46)
            if book is not None:
                # Scheduled, not awaited: this is the cancel path.
                found = None if mid_move else self._place_now(book, sent_until())
                if found is not None:
                    where = book.index_of(found[0].chapter_uri)
                    place, ahead = found
                    if ahead and reason not in _NO_PAUSE:
                        # Soloist played on with nobody listening (the walkie
                        # went and the server noticed late): back to where
                        # the listener was, or a resume picks up from there.
                        client.send("seek", generation=generation, position_ms=place.position_ms)
                    if ahead:
                        await log.awarning(
                            "spotify.place_capped",
                            chapter=place.chapter_uri,
                            ahead_ms=ahead,
                            kept_ms=place.position_ms,
                            audio_ms=audio_ms,
                        )
                    self._spawn(self._write_place(book, place))
            if ident is not None:
                if audio_ms:
                    self._spawn(self._require_memory().add_seconds(audio_ms / 1000))
                if reason == "cancelled":
                    self._paused_at = time.monotonic()
            await log.ainfo(
                "spotify.stream_ended",
                reason=reason,
                generation=generation,
                played_ms=int((time.monotonic() - requested) * 1000),
                book=book.title if book else ident[1] if ident else None,
                book_info=book is not None if ident else None,
                audio_ms=audio_ms,
                chapter=book.chapters[where].name if book and where is not None else None,
                chapter_index=where,
                chapters=len(book.chapters) if book else None,
            )

    # --- Helpers ---

    async def _book_ident(
        self, book: BookInfo | None, start: Match | None, client: SoloistClient
    ) -> tuple[str, str, str | None] | None:
        """(uri, title, author) of the book this stream plays: from its
        chapters when Spotify gave them, else from the request, the last
        book, the shelf or what played before, so its listening is still
        counted and remembered. The title may be "" when none of them
        knows it: then nothing is written under it (T1.45 review)."""
        if book is not None:
            return book.uri, book.title, book.author
        uri = start.uri if start is not None else client.state.context_uri or ""
        if not _is_book(uri):
            return None
        title, author = (start.title, start.artist) if start is not None else ("", None)
        memory = self._require_memory()
        if not title:
            last = await memory.last_book()
            if last is not None and last.get("uri") == uri:
                title, author = str(last.get("title") or ""), last.get("author")
        if not title:
            shelf = self._shelf_file.shelf if self._shelf_file else None
            listed = shelf.by_uri(uri) if shelf else None
            if listed is not None:
                title, author = listed.title, listed.author
        if not title:
            for row in await memory.played_books(limit=1_000):
                if row.get("uri") == uri:
                    title, author = str(row.get("title") or ""), row.get("author")
                    break
        await self._log().awarning("spotify.book_without_chapters", uri=uri, title=title)
        return uri, title, author

    def _unavailable(self) -> str | None:
        state = self._require_client().state
        if not state.connected:
            return "player_offline"
        if not state.logged_in:
            return "player_not_paired"
        return None

    async def _fail(self, reason: str, **extra: Any) -> ToolResult:
        await self._log().ainfo("spotify.unavailable", reason=reason, **extra)
        return ToolResult(
            output=json.dumps(
                {"ok": False, "reason": reason, "say": self._say(reason)}, ensure_ascii=False
            )
        )

    def _narrate(self, reason: str, **fields: str) -> None:
        lang = self._lang()
        contradicted = _CONTRADICTED[lang].get(reason)
        if reason.startswith("book_"):
            prompt = lines.notice(lang, reason, **fields)
        elif contradicted is not None:
            fact, line = contradicted
            prompt = _NOTICE[lang].format(fact=fact, line=line)
        else:
            prompt = self._say(reason)
        self._inject(prompt, f"spotify:{reason}")

    def _inject(self, prompt: str, dedup_key: str) -> None:
        """Schedule an inject_turn; never awaited from a tool or a stream."""
        if self._ctx is None:
            return
        self._spawn(self._ctx.inject_turn(prompt, dedup_key=dedup_key))

    def _spawn(self, work: Coroutine[Any, Any, Any]) -> None:
        """Run `work` in the background, held so it isn't collected
        mid-flight. Streams use it in their cancel path, which must not
        wait on storage or the network."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _then(self, key: str, **fields: str) -> ToolResult:
        """A book answer the model says word for word (T1.40); with no
        stream, whatever the press paused comes back after it."""
        return ToolResult(
            output=json.dumps(
                {"ok": False, "reason": key, "then": lines.then(self._lang(), key, **fields)},
                ensure_ascii=False,
            )
        )

    def _say(self, reason: str) -> str:
        return _SAY[self._lang()].get(reason, _SAY[self._lang()]["start_failed"])

    def _lang(self) -> str:
        for code in ("es", "fr"):
            if self._language.startswith(code):
                return code
        return "en"

    def _require_memory(self) -> BookMemory:
        assert self._memory is not None, "setup() not called"
        return self._memory

    def _require_client(self) -> SoloistClient:
        assert self._client is not None, "setup() not called"
        return self._client

    def _log(self) -> SkillLogger:
        assert self._logger is not None, "setup() not called"
        return self._logger


__all__ = ["SpotifySkill"]
