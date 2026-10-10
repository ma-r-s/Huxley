"""Spotify skill: play music from a household Spotify Premium account.

Engine: Spotify Soloist, Spotify's official headless Spotify Connect
client, running beside the server (systemd unit on Linux; `dev/` has a
container for a Mac). The skill controls it over its local WebSocket
(`soloist.py`) and streams its audio, captured from a null sink over
TCP (`capture.py`), as an `AudioStream`, the same path radio uses.
Huxley stays the one audio pipe: Soloist never plays to a speaker.

Tools:
- `play_music(query, kind?)`: search and play a song, artist, album or
  playlist.
- `music_control(action)`: pause, resume, next, previous.
- `whats_playing()`: what is on; the music comes back after the answer.

A press always stops the music first (the coordinator cancels the
stream), so next/previous/what's-playing act and then return a resume
stream; the factory's `finally` queues a pause without awaiting it.

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
    from collections.abc import AsyncIterator

    from huxley_sdk import SkillLogger


DEFAULT_WS = "ws://127.0.0.1:9090"
DEFAULT_PCM = "127.0.0.1:4712"
# How long to wait for Soloist to report "playing" after a play command.
START_TIMEOUT_S = 10.0

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
        "start_failed": (
            "Dile al usuario, en una frase, que Spotify no pudo poner la música y "
            "ofrécele poner la radio."
        ),
        "takeover": (
            "Dile al usuario, en una frase, que la música de Spotify pasó a sonar en otro aparato."
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
        "start_failed": (
            "Tell the user, in one sentence, that Spotify couldn't start the music; "
            "offer the radio."
        ),
        "takeover": (
            "Tell the user, in one sentence, that the Spotify music moved to another device."
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
        "start_failed": (
            "Dis à l'utilisateur, en une phrase, que Spotify n'a pas pu lancer la "
            "musique ; propose la radio."
        ),
        "takeover": (
            "Dis à l'utilisateur, en une phrase, que la musique Spotify est passée "
            "sur un autre appareil."
        ),
    },
}

_ACTIONS = ("pause", "resume", "next", "previous")


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
        if lang == "es":
            play = (
                "Pone música de Spotify: una canción, un artista, un álbum o una lista. "
                "ANTES de llamar di algo breve como 'a ver, la busco'. Cuando el resultado "
                "diga qué va a sonar, díselo al usuario con esas palabras; nunca inventes "
                "el nombre de la canción ni del artista. Para emisoras de radio usa la "
                "radio, no esto."
            )
            query = "Lo que pidió el usuario: título y/o artista, o un género ('boleros')."
            kind = (
                "'track' si pidió una canción concreta, 'artist' si pidió 'algo de' un "
                "artista, 'album', 'playlist' para una lista o un género, 'any' si no "
                "está claro."
            )
            control = (
                "Controla la música de Spotify. action: 'pause' (pausa, para, silencio), "
                "'resume' (sigue, continúa, vuelve a poner la música), 'next' (siguiente, "
                "otra canción), 'previous' (la anterior)."
            )
            whats = (
                "Dice qué está sonando en Spotify ('¿qué canción es esta?', '¿quién "
                "canta?'). La música sigue después de responder."
            )
        elif lang == "fr":
            play = (
                "Joue de la musique Spotify : une chanson, un artiste, un album ou une "
                "playlist. AVANT l'appel, dis brièvement 'je cherche'. Annonce ce qui va "
                "jouer avec les mots du résultat ; n'invente jamais le titre ni "
                "l'artiste. Pour une station de radio, utilise la radio."
            )
            query = "Ce que l'utilisateur a demandé : titre et/ou artiste, ou un genre."
            kind = (
                "'track' pour une chanson précise, 'artist' pour 'quelque chose de' un "
                "artiste, 'album', 'playlist' pour une liste ou un genre, 'any' sinon."
            )
            control = (
                "Contrôle la musique Spotify. action : 'pause', 'resume' (reprendre), "
                "'next' (suivante), 'previous' (précédente)."
            )
            whats = (
                "Dit ce qui joue sur Spotify ('c'est quelle chanson ?'). La musique "
                "reprend après la réponse."
            )
        else:
            play = (
                "Play music from Spotify: a song, an artist, an album or a playlist. "
                "BEFORE calling, briefly say something like 'let me find it'. When the "
                "result says what will play, tell the user in those words; never invent "
                "the song or artist. For radio stations use the radio, not this."
            )
            query = "What the user asked for: title and/or artist, or a genre ('boleros')."
            kind = (
                "'track' for a specific song, 'artist' for 'something by' an artist, "
                "'album', 'playlist' for a list or a genre, 'any' if unclear."
            )
            control = (
                "Control the Spotify music. action: 'pause' (pause, stop, quiet), "
                "'resume' (continue, play again), 'next' (next, another song), "
                "'previous' (the previous one)."
            )
            whats = (
                "Says what is playing on Spotify ('what song is this?', 'who sings "
                "this?'). The music continues after the answer."
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
                    "required": ["query"],
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
        if self._search is None:
            return await self._fail("search_not_configured", query=query)
        try:
            match = await self._search.find(query, kind)
        except SearchError as exc:
            await self._log().awarning("spotify.search_failed", query=query, error=str(exc))
            return await self._fail("search_failed", query=query)
        if match is None:
            return await self._fail("not_found", query=query)
        await self._log().ainfo(
            "spotify.play_requested",
            query=query,
            kind=match.kind,
            title=match.title,
            artist=match.artist,
            uri=match.uri,
            queued=len(match.queue),
        )
        payload = {"ok": True, "playing": match.title, "by": match.artist, "kind": match.kind}
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            side_effect=self._stream(start=match),
        )

    async def _music_control(self, action: str) -> ToolResult:
        if action not in _ACTIONS:
            return ToolResult(
                output=json.dumps({"ok": False, "error": f"unknown_action:{action}"})
            )
        await self._log().ainfo("spotify.control", action=action)
        if action == "pause":
            # The press already cancelled the stream (its finally queued a
            # pause); this covers a pause asked while nothing streamed.
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
        payload = {"ok": True, "title": title, "by": artists}
        # Asking stopped the music (the press); bring it back after the answer.
        side_effect = self._stream() if self._unavailable() is None else None
        return ToolResult(output=json.dumps(payload, ensure_ascii=False), side_effect=side_effect)

    # --- Streaming ---

    def _stream(self, *, start: Match | None = None, before: tuple[str, ...] = ()) -> AudioStream:
        """An AudioStream whose first run starts `start` (or resumes) and
        whose later runs, after a reminder or call paused it, resume."""
        first = True

        async def factory() -> AsyncIterator[bytes]:
            nonlocal first
            initial, first = first, False
            async for chunk in self._run(
                start=start if initial else None,
                before=before if initial else (),
                earcon=initial and start is not None,
            ):
                yield chunk

        label = start.title if start else "Spotify"
        return AudioStream(
            factory=factory,
            content_type=ContentType.NONMIXABLE,
            label=label,
            preroll_ms=len(self._earcon) * 1000 // 48_000 if self._earcon and start else 0,
            patience=self._patience,
        )

    async def _run(
        self, *, start: Match | None, before: tuple[str, ...], earcon: bool
    ) -> AsyncIterator[bytes]:
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
            # Volume belongs to the client (SetVolume); keep Soloist at full
            # so a phone can't silently turn this device down.
            client.send("set_volume", volume=100, generation=generation)
            if start is not None:
                client.send("play", uri=start.uri, generation=generation)
                for uri in start.queue:
                    client.send("add_to_queue", uri=uri, generation=generation)
            else:
                client.send("play", generation=generation)
            if earcon and self._earcon:
                yield self._earcon

            def started(s: PlayerState) -> bool:
                return s.status == "playing" or s.error_count > errors_before or not s.connected

            await client.wait_until(started, START_TIMEOUT_S)
            if client.state.status != "playing":
                reason = "start_failed"
                await log.awarning(
                    "spotify.start_failed",
                    status=client.state.status,
                    error=client.state.last_error,
                    connected=client.state.connected,
                )
                self._narrate("start_failed")
                return
            await log.ainfo(
                "spotify.play_started",
                latency_ms=int((time.monotonic() - requested) * 1000),
                resumed=start is None,
            )

            async for chunk in read_chunks(reader):
                state = client.state
                if not state.connected:
                    reason = "player_offline"
                    self._narrate("player_offline")
                    return
                if not state.is_active:
                    # Someone picked another device in a Spotify app.
                    reason = "takeover"
                    self._narrate("takeover")
                    return
                if state.status == "idle":
                    reason = "finished"
                    return
                yield chunk
            reason = "capture_closed"
            await log.awarning("spotify.capture_closed")
        finally:
            if writer is not None:
                writer.close()
            # Never await Soloist here: this runs in the coordinator's
            # cancel path. Queue the pause; a newer stream drops it.
            # Not after a takeover (that would pause the other device) or
            # when the music already ended.
            if reason not in ("takeover", "finished", "capture_unreachable"):
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
        if self._ctx is None:
            return
        task = asyncio.create_task(
            self._ctx.inject_turn(self._say(reason), dedup_key=f"spotify:{reason}")
        )
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
