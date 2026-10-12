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
  (back, forward, restart_chapter, go_to_chapter, go_back_to_saved_place)
  and `find_books(query?)`: audiobooks (T1.40, `books.py`).

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

Secrets (`<persona data>/secrets/spotify/values.json`): `client_id`,
`client_secret` of a Spotify Web API app, for search.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import timedelta
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
from huxley_skill_spotify.books import (
    PLAYED_AFTER_MS,
    Place,
    Shelf,
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


def _weak_match(spoken: str, book: Match) -> bool:
    """The best book's title is far from what was heard, and its author
    wasn't named either: confirm before spending listening hours on it."""
    heard = fold(spoken)
    if title_score(heard, fold(book.title)) >= WEAK_TITLE:
        return False
    author_words = [w for w in fold(book.artist or "").replace(",", " ").split() if len(w) > 3]
    return not any(w in heard.split() for w in author_words)


def _first_sentence(text: str, limit: int = 200) -> str:
    """The start of a book's description, enough to say what it's about."""
    text = " ".join(text.split())
    end = text.find(". ")
    if 0 < end < limit:
        return text[: end + 1]
    return text[:limit].rsplit(" ", 1)[0] if len(text) > limit else text


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
        self._shelf: Shelf | None = None
        # The last list of books read out, for "el segundo".
        self._last_list: list[Match] = []
        # When a book stream last ended on a press: a resume backs up by a
        # tenth of the pause.
        self._paused_at: float | None = None
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
        self._shelf = Shelf(ctx.storage)
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
                    "properties": {"query": {"type": "string", "description": t["find_query"]}},
                    "required": [],
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
                return await self._find_books(str(args.get("query") or ""))
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
            last = await self._require_shelf().last_music()
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
        before: tuple[_Command, ...] = {
            "next": (("skip_next", {}),),
            "previous": (("skip_prev", {}),),
        }.get(action, ())
        return ToolResult(
            output=json.dumps({"ok": True, "action": action}),
            side_effect=self._stream(before=before),
        )

    async def _whats_playing(self) -> ToolResult:
        state = self._require_client().state
        book = await self._loaded_book()
        index = book.index_of(item_uri(state.item)) if book is not None else None
        if book is not None and index is not None:
            lang = self._lang()
            chapter = book.chapters[index].name
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
        shelf = self._require_shelf()
        log = self._log()
        if not title.strip():
            last = await shelf.last_book()
            if last is None:
                return self._then("nothing_started")
            target = Match(
                uri=str(last["uri"]),
                kind="audiobook",
                title=str(last.get("title") or ""),
                artist=last.get("author"),
            )
        else:
            from_list = self._from_last_list(title)
            if from_list is not None:
                target = from_list
            else:
                if self._search is None:
                    return self._then("search_not_configured")
                try:
                    found = await self._search.candidates(title, "audiobook", limit=5)
                except SearchError as exc:
                    await log.awarning("spotify.search_failed", query=title, error=str(exc))
                    return self._then("search_failed")
                ordered = order_books(found, self._lang())
                if not ordered or is_guide(ordered[0].title):
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
        if from_beginning:
            place = await shelf.place(target.uri)
            where = info.index_of(place.chapter_uri) if info and place else None
            if where is not None and where >= 2 and not confirmed:
                return self._then("ask_whole_book")
            if place is not None:
                await shelf.save_place(target.uri, place)
            await shelf.clear_finished(target.uri)
        elif await shelf.finished(target.uri):
            return self._then("finished_replay")
        await log.ainfo(
            "spotify.book_requested",
            query=title,
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
            saved = await self._require_shelf().saved_place(book.uri)
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

    async def _find_books(self, query: str) -> ToolResult:
        shelf = self._require_shelf()
        lang = self._lang()
        if not query.strip():
            rows = await shelf.played_books(3)
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
            try:
                found = await self._search.candidates(query, "audiobook", limit=5)
            except SearchError as exc:
                await self._log().awarning("spotify.search_failed", query=query, error=str(exc))
                return self._then("search_failed")
            books = [m for m in order_books(found, lang) if not is_guide(m.title)][:3]
            if not books:
                await self._log().ainfo("spotify.book_not_found", query=query)
                return self._then("book_not_found")
            infos = await asyncio.gather(
                *(self._book_info(m.uri) for m in books), return_exceptions=True
            )
            entries = []
            for i, (m, info) in enumerate(zip(books, infos, strict=True)):
                entry: dict[str, Any] = {
                    "n": i + 1,
                    "title": m.title,
                    "by": m.artist,
                    "listened_here": await shelf.played(m.uri),
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
                {"ok": True, "books": entries, "then": lines.listing(lang)}, ensure_ascii=False
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

    def _from_last_list(self, spoken: str) -> Match | None:
        """'El segundo', or a title from the list just read out."""
        if not self._last_list:
            return None
        spoken = spoken.strip()
        if spoken.isdigit() and 1 <= int(spoken) <= len(self._last_list):
            return self._last_list[int(spoken) - 1]
        best = max(self._last_list, key=lambda m: title_score(fold(spoken), fold(m.title)))
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

    async def _refusal_cause(self, uri: str) -> str:
        """A refusal of a book that played here means the hours ran out;
        of any other, that the book isn't included in the plan."""
        shelf = self._require_shelf()
        if await shelf.played(uri):
            return "book_no_hours"
        budget = self._hours_per_month
        if budget is not None and await shelf.seconds_this_month() >= budget * 3600 - 600:
            return "book_no_hours"
        return "book_must_buy"

    async def _book_stopped(
        self, book: BookInfo, state: PlayerState, chapter: str | None, *, left: bool
    ) -> str:
        """Every way a book stops is said (critic B1): its end, or a cut."""
        index = book.index_of(chapter)
        position = position_now(state)
        last = len(book.chapters) - 1
        if index is not None and index == last:
            self._spawn(self._require_shelf().mark_finished(book.uri))
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
        if left:
            return "book_left"
        self._narrate("book_cut")
        return "book_cut"

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
        await client.wait_until(lambda s: (s.position_ms or 0) <= position + 5_000, 2.0)
        await self._log().ainfo("spotify.book_nav", chapter=book.chapters[target].name)
        return True

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

    async def _save_place(self, book: BookInfo) -> None:
        state = self._require_client().state
        chapter = item_uri(state.item)
        if chapter is None or book.index_of(chapter) is None:
            return
        await self._require_shelf().set_place(book.uri, Place(chapter, position_now(state) or 0))

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
        progress = {"played": False}
        context = self._require_client().state.context_uri or ""
        is_book = (start is not None and start.kind == "audiobook") or (
            start is None and _is_book(context)
        )

        async def factory() -> AsyncIterator[bytes]:
            initial = not progress["played"]
            run = self._run(
                start=start if initial else None,
                before=before if initial else (),
                goto=goto if initial else None,
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
        return AudioStream(
            factory=factory,
            content_type=ContentType.NONMIXABLE,
            label=label,
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
        nav: asyncio.Task[bool] | None = None
        book_start = start is not None and start.kind == "audiobook"
        audio_ms = 0
        try:
            try:
                reader, writer = await open_capture(self._pcm_host, self._pcm_port)
            except CaptureError as exc:
                reason = "capture_unreachable"
                await log.awarning("spotify.capture_failed", error=str(exc))
                self._narrate("book_offline" if book_start else "player_offline")
                return

            errors_before = client.state.error_count
            commands = list(before)
            if start is None and not before and goto is None:
                commands.extend(self._resume_rewind())
            for command, fields in commands:
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
                self._narrate("book_failed" if book_start else "start_failed")
                return
            # Volume belongs to the client (SetVolume); keep Soloist at full
            # so a phone can't silently turn this device down.
            client.send("set_volume", volume=100, generation=generation)
            book = await self._loaded_book()
            if start is not None and book is None and not book_start:
                self._spawn(self._require_shelf().set_last_music(start))
            if book is not None and goto is not None:
                nav = asyncio.create_task(self._go_to(client, generation, book, *goto))
            chapter = item_uri(client.state.item)
            await log.ainfo(
                "spotify.play_started",
                latency_ms=int((time.monotonic() - requested) * 1000),
                resumed=start is None,
                context=state.context_uri,
                book=book.title if book else None,
                chapter=book.chapters[i].name
                if book and (i := book.index_of(chapter)) is not None
                else None,
                goto=goto,
            )
            started_at = time.monotonic()
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
                    if start is not None and time.monotonic() - started_at < REFUSED_S:
                        reason = "refused"
                        cause = await self._refusal_cause(start.uri) if book_start else ""
                        await log.awarning(
                            "spotify.start_refused",
                            kind=start.kind,
                            uri=start.uri,
                            chunks=chunks,
                            cause=cause or None,
                        )
                        self._narrate(cause or "start_failed")
                        return
                    if book is not None:
                        reason = await self._book_stopped(book, state, chapter, left=False)
                        return
                    reason = "finished"
                    return
                if nav is not None:
                    if not nav.done():
                        continue  # silent while it moves between chapters
                    if nav.cancelled() or nav.exception() is not None or not nav.result():
                        self._narrate("book_nav_failed")
                    nav = None
                    chapter = item_uri(client.state.item)
                if book is not None:
                    if state.context_uri and state.context_uri != book.uri:
                        # Spotify left the book by itself (autoplay at its
                        # end), or a phone put something else on.
                        reason = await self._book_stopped(book, state, chapter, left=True)
                        return
                    current = item_uri(state.item)
                    if current and current != chapter:
                        chapter = current
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
                        self._spawn(self._save_place(book))
                if state.status == "paused":
                    # Paused from a Spotify app: let the speaker go.
                    paused_since = paused_since or time.monotonic()
                    if time.monotonic() - paused_since > PAUSED_GRACE_S:
                        reason = "paused_elsewhere"
                        return
                else:
                    paused_since = None
                chunks += 1
                audio_ms += CHUNK_MS
                if book is not None and not marked and audio_ms >= PLAYED_AFTER_MS:
                    marked = True
                    shelf = self._require_shelf()
                    self._spawn(shelf.mark_played(book.uri, book.title, book.author))
                    self._spawn(shelf.set_last_book(book.uri, book.title, book.author))
                yield chunk
            # The capture closed under us (the container's sound server is
            # down, or the container restarted). Behind Docker on a Mac the
            # connect succeeds and closes at once, so a stream with no audio
            # at all is the offline case.
            reason = "capture_closed"
            await log.awarning("spotify.capture_closed", chunks=chunks)
            if book is not None:
                self._narrate("book_cut" if chunks else "book_offline")
            else:
                self._narrate("interrupted" if chunks else "player_offline")
        finally:
            if writer is not None:
                writer.close()
            if nav is not None:
                nav.cancel()
            # Never await Soloist here: this runs in the coordinator's
            # cancel path. Queue the pause; a newer stream drops it. Not
            # when the music moved, was paused elsewhere, or ended.
            if reason not in _NO_PAUSE:
                client.send("pause", generation=generation)
            if book is not None:
                # Scheduled, not awaited: this is the cancel path.
                self._spawn(self._save_place(book))
                if audio_ms:
                    self._spawn(self._require_shelf().add_seconds(audio_ms / 1000))
                if reason == "cancelled":
                    self._paused_at = time.monotonic()
            await log.ainfo(
                "spotify.stream_ended",
                reason=reason,
                generation=generation,
                played_ms=int((time.monotonic() - requested) * 1000),
                book=book.title if book else None,
                audio_ms=audio_ms,
            )

    # --- Helpers ---

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

    def _require_shelf(self) -> Shelf:
        assert self._shelf is not None, "setup() not called"
        return self._shelf

    def _require_client(self) -> SoloistClient:
        assert self._client is not None, "setup() not called"
        return self._client

    def _log(self) -> SkillLogger:
        assert self._logger is not None, "setup() not called"
        return self._logger


__all__ = ["SpotifySkill"]
