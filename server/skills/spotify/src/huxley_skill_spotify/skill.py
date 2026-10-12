"""Spotify skill: play music from a household Spotify Premium account.

Engine: Spotify Soloist, Spotify's official headless Spotify Connect
client, running beside the server (systemd unit on Linux; `dev/` has a
container for a Mac). The skill controls it over its local WebSocket
(`soloist.py`) and streams its audio, captured from a null sink over
TCP (`capture.py`), as an `AudioStream`, the same path radio uses.
Huxley stays the one audio pipe: Soloist never plays to a speaker.

Tools:
- `play_music(query?, kind?)`: search and play a song, artist, album,
  playlist or audiobook (the runners-up go back as `alternatives`). No
  query: continue what played last. A book continues where the account
  left it (Spotify keeps the place).
- `music_control(action)`: pause, resume, next, previous.
- `whats_playing()`: what is on. An info tool: the model speaks the
  answer, and the music comes back after it like after any turn.

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
from huxley_skill_spotify.capture import CaptureError, open_capture, read_chunks
from huxley_skill_spotify.search import KINDS, Match, SearchError, SpotifySearch
from huxley_skill_spotify.soloist import PlayerState, SoloistClient, describe_item

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from huxley_sdk import SkillLogger


DEFAULT_WS = "ws://127.0.0.1:9090"
DEFAULT_PCM = "127.0.0.1:4712"
# How long to wait for Soloist to report "playing" after a play command.
START_TIMEOUT_S = 10.0
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
        "book_not_found": (
            "Dile al usuario que no encontraste ese libro en Spotify y pregúntale si quiere otro."
        ),
        "audiobook_refused": (
            "Dile al usuario, en una frase, que Spotify no dejó poner el libro; "
            "puede que se hayan acabado las horas de audiolibros de este mes."
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
        "book_not_found": (
            "Tell the user you didn't find that book on Spotify and ask if they'd like another."
        ),
        "audiobook_refused": (
            "Tell the user, in one sentence, that Spotify wouldn't play the book; "
            "this month's audiobook hours may have run out."
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
        "book_not_found": (
            "Dis à l'utilisateur que tu n'as pas trouvé ce livre sur Spotify et "
            "demande s'il en veut un autre."
        ),
        "audiobook_refused": (
            "Dis à l'utilisateur, en une phrase, que Spotify n'a pas voulu lancer le "
            "livre ; les heures de livres audio du mois sont peut-être épuisées."
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

_ACTIONS = ("pause", "resume", "next", "previous")

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
        # The last audiobook started: whats_playing names it, since a
        # chapter's own title is only "Capítulo 3".
        self._book: Match | None = None
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
        self._tasks: set[asyncio.Task[None]] = set()

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
        lang = self._lang()
        kinds = list(KINDS)
        # No example artists or genres in these texts: the model used them
        # as its guess when it misheard (2026-10-12), so they stay abstract.
        if lang == "es":
            play = (
                "Pone música de Spotify: una canción, un artista, un álbum o una lista; "
                "también audiolibros (`kind: 'audiobook'`), que siguen donde se quedaron. "
                "ANTES de llamar di algo breve como 'a ver, la busco'. Los nombres pueden "
                "estar en inglés o en cualquier idioma: pásalos tal como los oíste, sin "
                "traducirlos ni cambiarlos por otro parecido. Si dudas del nombre, llama "
                "igual con tu mejor suposición en vez de preguntar. Si el usuario te "
                "corrige ('no, ...'), no repitas la misma búsqueda: prueba otra "
                "interpretación o una de las `alternatives` del resultado anterior. Si "
                "pidió música sin decir cuál ('pon música'), llama sin `query`: sigue con "
                "lo último que sonó. Cuando el resultado diga qué va a sonar, díselo al "
                "usuario con esas palabras; nunca inventes el nombre de la canción ni del "
                "artista. Para emisoras de radio usa la radio, no esto."
            )
            query = (
                "Los nombres tal como los dijo el usuario, sin traducir. Si nombró una "
                "canción y su artista ('<canción> de <artista>'), pon los dos: "
                "'<canción> <artista>'. Vacío si no dijo qué música."
            )
            kind = (
                "'track' si nombró una canción (con o sin artista), 'artist' si pidió "
                "'algo de' un artista sin nombrar canción, 'album', 'playlist' para una "
                "lista o un estilo de música, 'audiobook' si pidió un libro (con el "
                "título y el autor si lo dijo), 'any' si no está claro."
            )
            control = (
                "Controla la música de Spotify. action: 'pause' (pausa, para, silencio, "
                "apágala, basta; la música se pausa sola mientras el usuario habla y "
                "vuelve sola después, así que para pararla de verdad llama siempre a "
                "'pause'), "
                "'resume' (sigue, sigue con la canción, continúa, vuelve a poner la "
                "música; 'ponlo' si lo último que sonaba era música), 'next' solo si "
                "pide OTRA canción (siguiente, otra, "
                "cambia la canción, pasa), 'previous' (la anterior). 'Sigue' es seguir "
                "con la misma: 'resume', no 'next'."
            )
            whats = (
                "Dice qué está sonando en Spotify ('¿qué canción es esta?', '¿quién "
                "canta?'). Llámala SIEMPRE que pregunten, aunque creas saberlo: la "
                "canción cambia. No digas nada antes de llamarla. Después haz "
                "exactamente lo que diga `then`; la música vuelve sola."
            )
        elif lang == "fr":
            play = (
                "Joue de la musique Spotify : une chanson, un artiste, un album ou une "
                "playlist ; aussi des livres audio (`kind: 'audiobook'`), qui reprennent "
                "où ils en étaient. AVANT l'appel, dis brièvement 'je cherche'. Les noms peuvent "
                "être en anglais ou en toute autre langue : passe-les tels que tu les as "
                "entendus, sans les traduire ni les remplacer par un nom proche. Si tu "
                "doutes du nom, appelle quand même avec ta meilleure supposition au lieu "
                "de demander. Si l'utilisateur te corrige ('non, ...'), ne refais pas la "
                "même recherche : essaie une autre interprétation ou une des "
                "`alternatives` du résultat précédent. S'il demande de la musique sans "
                "dire laquelle ('mets de la musique'), appelle sans `query` : la dernière "
                "musique reprend. Annonce ce qui va jouer avec les mots du résultat ; "
                "n'invente jamais le titre ni l'artiste. Pour une station de radio, "
                "utilise la radio."
            )
            query = (
                "Les noms tels que l'utilisateur les a dits, sans traduire. S'il a nommé "
                "une chanson et son artiste ('<chanson> de <artiste>'), mets les deux : "
                "'<chanson> <artiste>'. Vide s'il n'a pas dit quelle musique."
            )
            kind = (
                "'track' s'il a nommé une chanson (avec ou sans artiste), 'artist' pour "
                "'quelque chose de' un artiste sans chanson précise, 'album', 'playlist' "
                "pour une liste ou un style de musique, 'audiobook' pour un livre (titre "
                "et auteur s'il l'a dit), 'any' sinon."
            )
            control = (
                "Contrôle la musique Spotify. action : 'pause', 'resume' (reprends, "
                "continue, remets-la), 'next' seulement pour UNE AUTRE chanson "
                "(suivante, une autre, change de chanson), 'previous' (précédente). "
                "'Continue' veut dire la même chanson : 'resume', pas 'next'."
            )
            whats = (
                "Dit ce qui joue sur Spotify ('c'est quelle chanson ?'). Appelle-la "
                "TOUJOURS, même si tu crois savoir : la chanson change. Ne dis rien "
                "avant. Ensuite fais exactement ce que dit `then` ; la musique "
                "reprend toute seule."
            )
        else:
            play = (
                "Play music from Spotify: a song, an artist, an album or a playlist; "
                "audiobooks too (`kind: 'audiobook'`), which continue where they left off. "
                "BEFORE calling, briefly say something like 'let me find it'. Names may "
                "be in any language: pass them as you heard them, without translating "
                "them or swapping in a similar name. If you're unsure of the name, call "
                "anyway with your best guess instead of asking. If the user corrects you "
                "('no, ...'), don't repeat the same search: try another reading or one "
                "of the `alternatives` from the previous result. If they asked for music "
                "without saying which ('play some music'), call without `query`: the "
                "last music continues. When the result says what will play, tell the "
                "user in those words; never invent the song or artist. For radio "
                "stations use the radio, not this."
            )
            query = (
                "The names as the user said them, untranslated. If they named a song "
                "and its artist ('<song> by <artist>'), include both: '<song> <artist>'. "
                "Empty if they didn't say which music."
            )
            kind = (
                "'track' if they named a song (with or without the artist), 'artist' for "
                "'something by' an artist with no song named, 'album', 'playlist' for a "
                "list or a style of music, 'audiobook' for a book (title, and author if "
                "they said it), 'any' if unclear."
            )
            control = (
                "Control the Spotify music. action: 'pause' (pause, stop, quiet), "
                "'resume' (continue, keep going, play again; 'play it' if music was the "
                "last thing playing), 'next' only for "
                "ANOTHER song (next, another one, skip), 'previous' (the previous one). "
                "'Keep going' means the same song: 'resume', not 'next'."
            )
            whats = (
                "Says what is playing on Spotify ('what song is this?', 'who sings "
                "this?'). ALWAYS call it when asked, even if you think you know: the "
                "song changes. Say nothing before calling it. Then do exactly what "
                "`then` says; the music comes back on its own."
            )
        return [
            ToolDefinition(
                name="play_music",
                description=play,
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": query},
                        "kind": {"type": "string", "enum": kinds, "description": kind},
                    },
                    # No query means "some music": the last thing continues.
                    "required": [],
                },
            ),
            ToolDefinition(
                name="music_control",
                description=control,
                parameters={
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": list(_ACTIONS)}},
                    "required": ["action"],
                },
            ),
            ToolDefinition(
                name="whats_playing",
                description=whats,
                parameters={"type": "object", "properties": {}},
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
                return self._whats_playing()
            case _:
                return ToolResult(output=json.dumps({"error": f"unknown_tool:{tool_name}"}))

    async def _play_music(self, query: str, kind: str) -> ToolResult:
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
            reason = "book_not_found" if kind == "audiobook" else "not_found"
            return await self._fail(reason, query=query)
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
        """Music asked for with nothing named: continue what Soloist had
        loaded last (it keeps it across restarts). Persona-neutral: no
        default genre or artist is ever guessed. With nothing loaded,
        ask instead of failing after the 10 s start timeout."""
        state = self._require_client().state
        context = state.context_uri
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
        before = {"next": ("skip_next",), "previous": ("skip_prev",)}.get(action, ())
        return ToolResult(
            output=json.dumps({"ok": True, "action": action}),
            side_effect=self._stream(before=before),
        )

    def _whats_playing(self) -> ToolResult:
        state = self._require_client().state
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
        book = self._book
        if book is not None and state.context_uri == book.uri:
            # A chapter's own name is only "Capítulo 3": say the book too.
            title, artists = f"{book.title}, {title}", book.artist
        payload: dict[str, Any] = {"ok": True, "title": title, "by": artists}
        # An info tool: the model speaks the answer in a follow-up round
        # (a tool with an AudioStream gets none). The press that asked
        # paused the music; the framework resumes it after the answer.
        lang = self._lang()
        line = _SAY_SONG[lang].format(title=title, by=artists or "?")
        payload["then"] = _THEN[lang].format(line=line)
        return ToolResult(output=json.dumps(payload, ensure_ascii=False))

    # --- Streaming ---

    def _stream(self, *, start: Match | None = None, before: tuple[str, ...] = ()) -> AudioStream:
        """An AudioStream whose first run starts `start` (or resumes) and
        whose later runs, after a reminder or call paused it, resume."""
        # Flips once the first run has actually sent its play: a run
        # cancelled before that (say, during the capture connect) must
        # leave the next run to start `start`, not resume something else.
        progress = {"played": False}

        async def factory() -> AsyncIterator[bytes]:
            initial = not progress["played"]
            run = self._run(
                start=start if initial else None,
                before=before if initial else (),
                earcon=initial and start is not None,
                progress=progress,
            )
            # aclosing: on cancel, run's finally (pause, close the capture)
            # happens now, not whenever the generator is collected.
            async with contextlib.aclosing(run):
                async for chunk in run:
                    yield chunk

        async def on_patience_expired() -> None:
            # A call outlasted the patience window; the music won't come
            # back on its own. Called from the focus manager: schedule only.
            self._narrate("patience_expired")

        label = start.title if start else "Spotify"
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
        before: tuple[str, ...],
        earcon: bool,
        progress: dict[str, bool],
    ) -> AsyncGenerator[bytes]:
        client = self._require_client()
        log = self._log()
        generation = client.new_generation()
        requested = time.monotonic()
        reason = "cancelled"
        writer: asyncio.StreamWriter | None = None
        try:
            try:
                reader, writer = await open_capture(self._pcm_host, self._pcm_port)
            except CaptureError as exc:
                reason = "capture_unreachable"
                await log.awarning("spotify.capture_failed", error=str(exc))
                self._narrate("player_offline")
                return

            errors_before = client.state.error_count
            for command in before:
                client.send(command, generation=generation)
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
                self._narrate("start_failed")
                return
            # Volume belongs to the client (SetVolume); keep Soloist at full
            # so a phone can't silently turn this device down.
            client.send("set_volume", volume=100, generation=generation)
            await log.ainfo(
                "spotify.play_started",
                latency_ms=int((time.monotonic() - requested) * 1000),
                resumed=start is None,
                context=state.context_uri,
            )

            if start is not None and start.kind == "audiobook":
                self._book = start
            started_at = time.monotonic()
            seen_active = False
            paused_since: float | None = None
            chunks = 0
            async for chunk in read_chunks(reader):
                state = client.state
                if not state.connected:
                    reason = "player_offline"
                    self._narrate("player_offline")
                    return
                # Soloist turns active a moment after playback starts; only
                # active-then-not means someone picked another device.
                if state.is_active:
                    seen_active = True
                elif seen_active:
                    reason = "takeover"
                    self._narrate("takeover")
                    return
                if state.status == "idle":
                    if start is not None and time.monotonic() - started_at < REFUSED_S:
                        reason = "refused"
                        await log.awarning(
                            "spotify.start_refused", kind=start.kind, uri=start.uri, chunks=chunks
                        )
                        self._narrate(
                            "audiobook_refused" if start.kind == "audiobook" else "start_failed"
                        )
                        return
                    reason = "finished"
                    return
                if state.status == "paused":
                    # Paused from a Spotify app: let the speaker go.
                    paused_since = paused_since or time.monotonic()
                    if time.monotonic() - paused_since > PAUSED_GRACE_S:
                        reason = "paused_elsewhere"
                        return
                else:
                    paused_since = None
                chunks += 1
                yield chunk
            # The capture closed under us (the container's sound server is
            # down, or the container restarted). Behind Docker on a Mac the
            # connect succeeds and closes at once, so a stream with no audio
            # at all is the offline case.
            reason = "capture_closed"
            await log.awarning("spotify.capture_closed", chunks=chunks)
            self._narrate("interrupted" if chunks else "player_offline")
        finally:
            if writer is not None:
                writer.close()
            # Never await Soloist here: this runs in the coordinator's
            # cancel path. Queue the pause; a newer stream drops it. Not
            # when the music moved, was paused elsewhere, or ended.
            if reason not in (
                "takeover",
                "finished",
                "refused",
                "paused_elsewhere",
                "capture_unreachable",
            ):
                client.send("pause", generation=generation)
            await log.ainfo(
                "spotify.stream_ended",
                reason=reason,
                generation=generation,
                played_ms=int((time.monotonic() - requested) * 1000),
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

    def _narrate(self, reason: str) -> None:
        self._inject(self._say(reason), f"spotify:{reason}")

    def _inject(self, prompt: str, dedup_key: str) -> None:
        """Schedule an inject_turn; never awaited from a tool or a stream."""
        if self._ctx is None:
            return
        task = asyncio.create_task(self._ctx.inject_turn(prompt, dedup_key=dedup_key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _say(self, reason: str) -> str:
        return _SAY[self._lang()].get(reason, _SAY[self._lang()]["start_failed"])

    def _lang(self) -> str:
        for code in ("es", "fr"):
            if self._language.startswith(code):
                return code
        return "en"

    def _require_client(self) -> SoloistClient:
        assert self._client is not None, "setup() not called"
        return self._client

    def _log(self) -> SkillLogger:
        assert self._logger is not None, "setup() not called"
        return self._logger


__all__ = ["SpotifySkill"]
