"""Internet radio skill — persona-agnostic.

Plays from a curated list of HTTP/Icecast URLs configured per-persona.
Returns `AudioStream` for live playback (same coordinator path as
audiobooks); optional `PlaySound` chime via `start_sound` config.

Tools:
- `play_station(station?)` — start (or switch to) a station. With no
  argument, plays the persona's `default` station.
- `resume_radio()` — restart the most-recently-played station (mirrors
  the audiobooks `resume_last` pattern).
- `stop_radio()` — stop the current stream.
- `list_stations()` — return the configured station list (the LLM uses
  this to answer "qué emisoras tengo" without guessing).

Configuration (persona's `skills.radio` block):
- Required: `stations` (list of `{id, name, url, description?}`),
  `default` (station id).
- Optional: `start_sound` (sound palette role), `sounds_path`
  (default "sounds"), `ffmpeg` (default "ffmpeg").

Storage layout (per-skill namespaced KV via `huxley_sdk.SkillStorage`):
- `last_id` → most-recently-played station id

Honest design note: radio is live, so there's no "where I left off"
position. `resume_radio` just restarts the same station from its live
feed — same as if the user said `play_station(<that-id>)` themselves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from huxley_sdk import (
    AudioStream,
    CancelMedia,
    SkillContext,
    SkillLogger,
    SkillStorage,
    ToolDefinition,
    ToolResult,
)
from huxley_sdk.audio import load_pcm_palette
from huxley_skill_radio.player import PlayerError, RadioPlayer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


LAST_STATION_KEY = "last_id"


class RadioSkill:
    """Streams internet radio from a configured list of stations."""

    # `config_schema = None`: radio's primary config is a list of
    # station records (`{id, name, url, language}`), which JSON Schema
    # describes but a v2 PWA form-renderer would render as a deeply
    # nested accordion that's painful to edit. Per
    # docs/skill-marketplace.md § Config schema convention, complex
    # list-of-records configs leave config_schema None and v2's PWA
    # falls back to "edit YAML directly."
    config_schema: ClassVar[dict[str, Any] | None] = None

    # Bump on incompatible change to the persisted last-station key
    # (`last_station_id`) or any future per-station play-count storage.
    data_schema_version: ClassVar[int] = 1

    def __init__(self, *, player: RadioPlayer | None = None) -> None:
        # `player` is keyword-only and reserved for tests that inject a fake.
        # Production setup() builds a RadioPlayer from ctx.config.
        self._player: RadioPlayer | None = player
        self._stations: list[dict[str, str]] = []
        self._stations_by_id: dict[str, dict[str, str]] = {}
        self._default_id: str = ""
        # Radio has no separate "feed language" — stations are live URLs.
        # `_language_code` tracks the session's UI language so tool
        # descriptions and user-facing messages match what the LLM speaks.
        # Updated on each session connect via `reconfigure()`.
        self._language_code: str = "en"
        self._storage: SkillStorage | None = None
        self._logger: SkillLogger | None = None
        self._start_sound_role: str | None = None
        self._sounds: dict[str, bytes] = {}

    @property
    def name(self) -> str:
        return "radio"

    @property
    def tools(self) -> list[ToolDefinition]:
        code = self._language_code.lower()
        if code.startswith("es"):
            return self._tools_es()
        if code.startswith("fr"):
            return self._tools_fr()
        return self._tools_en()

    def _station_choices(self) -> str:
        # Compact "id (name)" list for the tool description so the LLM
        # picks a real station id rather than improvising.
        if not self._stations:
            return ""
        return ", ".join(f"{s['id']} ({s['name']})" for s in self._stations)

    def _tools_es(self) -> list[ToolDefinition]:
        choices = self._station_choices()
        return [
            ToolDefinition(
                name="play_station",
                description=(
                    "Empieza a reproducir una emisora de radio. ANTES de llamar di "
                    "brevemente algo como 'a ver, prendo la radio' para que el "
                    "usuario sepa que escuchaste mientras carga. "
                    "Sin argumento usa la emisora predeterminada. "
                    f"Emisoras disponibles (id y nombre): {choices}. "
                    "Pasa el ID exacto de la emisora elegida."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "station": {
                            "type": "string",
                            "description": (
                                "El id de la emisora (no el nombre completo). "
                                "Si el usuario nombra la emisora por nombre, "
                                "encuéntrala en la lista y pasa el id. "
                                "Omite para usar la emisora predeterminada."
                            ),
                        },
                    },
                },
            ),
            ToolDefinition(
                name="resume_radio",
                description=(
                    "Reanuda la última emisora que se reprodujo. Usa cuando el "
                    "usuario diga 'sigue con la radio', 'pon la radio otra vez' "
                    "o similar sin nombrar emisora."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="stop_radio",
                description=(
                    "Apaga la radio. Usa para 'apaga la radio', 'para la radio', "
                    "'silencio', 'quita la radio'."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="list_stations",
                description=(
                    "Devuelve la lista de emisoras configuradas. Úsala cuando el "
                    "usuario pregunte 'qué emisoras tengo' o 'qué radios hay'."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    def _tools_fr(self) -> list[ToolDefinition]:
        choices = self._station_choices()
        return [
            ToolDefinition(
                name="play_station",
                description=(
                    "Lance une station de radio. AVANT d'appeler, dis brièvement "
                    "quelque chose comme 'un instant, j'allume la radio' pour "
                    "que l'utilisateur sache que tu l'as entendu pendant le "
                    "chargement. "
                    "Sans argument, lance la station par défaut. "
                    f"Stations disponibles (id et nom) : {choices}. "
                    "Passe l'identifiant exact de la station."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "station": {
                            "type": "string",
                            "description": (
                                "Identifiant de la station (pas son nom complet). "
                                "Si l'utilisateur nomme la station par son nom, "
                                "trouve-la dans la liste et passe son id. "
                                "Omets pour utiliser la station par défaut."
                            ),
                        },
                    },
                },
            ),
            ToolDefinition(
                name="resume_radio",
                description=(
                    "Reprend la dernière station écoutée. À utiliser quand "
                    "l'utilisateur dit 'remets la radio' ou similaire sans "
                    "nommer de station."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="stop_radio",
                description=(
                    "Arrête la radio. À utiliser pour 'coupe la radio', 'silence', 'éteins-la'."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="list_stations",
                description=(
                    "Renvoie la liste des stations configurées. À utiliser "
                    "quand l'utilisateur demande 'quelles stations j'ai' ou "
                    "'quelles radios y a-t-il'."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    def _tools_en(self) -> list[ToolDefinition]:
        choices = self._station_choices()
        return [
            ToolDefinition(
                name="play_station",
                description=(
                    "Start playing a radio station. BEFORE calling, briefly say "
                    "something like 'one moment, turning on the radio' so the user "
                    "knows you heard them while it loads. "
                    "Without argument, plays the default station. "
                    f"Available stations (id and name): {choices}. "
                    "Pass the exact station id."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "station": {
                            "type": "string",
                            "description": (
                                "Station id (not the full name). If the user names "
                                "a station by name, find it in the list and pass "
                                "the id. Omit to use the default station."
                            ),
                        },
                    },
                },
            ),
            ToolDefinition(
                name="resume_radio",
                description=(
                    "Resume the last station that played. Use when the user says "
                    "'play the radio again' or similar without naming a station."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="stop_radio",
                description="Stop the radio. Use for 'stop the radio', 'silence', 'turn it off'.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="list_stations",
                description=(
                    "Return the configured station list. Use when the user asks "
                    "'what stations do I have' or 'what radios are there'."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        cfg = ctx.config
        self._logger = ctx.logger
        self._storage = ctx.storage

        # Required config — fail fast at startup with a clear message.
        try:
            raw_stations = cfg["stations"]
            self._default_id = str(cfg["default"])
        except KeyError as exc:
            raise ValueError(
                f"radio skill: missing required config key {exc.args[0]!r}. "
                "Required: stations (list), default (station id)."
            ) from exc

        if not isinstance(raw_stations, list) or not raw_stations:
            raise ValueError("radio skill: `stations` must be a non-empty list.")

        # Normalize + validate stations: each must have id, name, url.
        stations: list[dict[str, str]] = []
        for entry in raw_stations:
            if not isinstance(entry, dict):
                raise ValueError(f"radio skill: station entry must be a dict, got {type(entry)}")
            for key in ("id", "name", "url"):
                if key not in entry:
                    raise ValueError(
                        f"radio skill: station {entry!r} missing required key {key!r}"
                    )
            stations.append(
                {
                    "id": str(entry["id"]),
                    "name": str(entry["name"]),
                    "url": str(entry["url"]),
                    "description": str(entry.get("description", "")),
                }
            )
        self._stations = stations
        self._stations_by_id = {s["id"]: s for s in stations}

        if self._default_id not in self._stations_by_id:
            raise ValueError(
                f"radio skill: `default` station id {self._default_id!r} "
                f"not found in stations list. Available: {list(self._stations_by_id)}"
            )

        # Seed the tool-description language from the session context.
        # `reconfigure()` keeps it in sync across session connects; the
        # persona's `skills.radio.language_code` is retained as a
        # fallback for clients that never send a language (back-compat
        # with the pre-i18n shape).
        cfg_lang = cfg.get("language_code")
        self._language_code = str(
            ctx.language or cfg_lang or "en",
        ).lower()

        if self._player is None:
            self._player = RadioPlayer(ffmpeg_path=str(cfg.get("ffmpeg", "ffmpeg")))

        # Sound palette — only loaded if persona configured a start_sound role.
        self._start_sound_role = cfg.get("start_sound")
        if self._start_sound_role:
            sounds_raw = cfg.get("sounds_path", "sounds")
            sounds_dir = (
                Path(sounds_raw)
                if Path(sounds_raw).is_absolute()
                else (ctx.persona_data_dir / sounds_raw)
            )
            self._sounds = load_pcm_palette(sounds_dir, [self._start_sound_role])
            if self._start_sound_role not in self._sounds:
                await ctx.logger.awarning(
                    "radio.start_sound_missing",
                    role=self._start_sound_role,
                    path=str(sounds_dir),
                )

        await ctx.logger.ainfo(
            "radio.setup_complete",
            stations=len(self._stations),
            default=self._default_id,
            chime=self._start_sound_role if self._sounds else None,
        )

    async def reconfigure(self, ctx: SkillContext) -> None:
        """Refresh the UI language so tool descriptions match the session."""
        self._language_code = (ctx.language or self._language_code).lower()
        await ctx.logger.ainfo("radio.reconfigure", language=self._language_code)

    async def teardown(self) -> None:
        """No persistent state to flush — the running stream is owned by the
        coordinator's media task and is cancelled by `interrupt()`."""

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "play_station":
                return await self._play_station(args.get("station"))
            case "resume_radio":
                return await self._resume_radio()
            case "stop_radio":
                return self._stop_radio()
            case "list_stations":
                return self._list_stations()
            case _:
                return ToolResult(output=json.dumps({"error": f"unknown_tool:{tool_name}"}))

    # --- Tool handlers ---

    async def _play_station(self, station_id: str | None) -> ToolResult:
        assert self._storage is not None
        assert self._logger is not None
        # Resolve which station to play.
        target_id = station_id or self._default_id
        station = self._stations_by_id.get(target_id)
        if station is None:
            # Try a case-insensitive name match before giving up. The LLM
            # is told to pass the id, but persona never_say_no constraint
            # benefits from a graceful fallback when it slips.
            for s in self._stations:
                if s["name"].lower() == (station_id or "").lower():
                    station = s
                    target_id = s["id"]
                    break
        if station is None:
            await self._logger.awarning(
                "radio.unknown_station",
                requested=station_id,
                available=list(self._stations_by_id),
            )
            return ToolResult(
                output=json.dumps(
                    {
                        "playing": False,
                        "error": "unknown_station",
                        "requested": station_id,
                        "available": [{"id": s["id"], "name": s["name"]} for s in self._stations],
                    },
                    ensure_ascii=False,
                )
            )

        await self._storage.set_setting(LAST_STATION_KEY, target_id)
        factory = self._build_factory(station["url"], target_id)
        await self._logger.ainfo(
            "radio.play_station",
            station_id=target_id,
            station_name=station["name"],
        )
        return self._success_result(
            payload={
                "playing": True,
                "station_id": target_id,
                "station_name": station["name"],
            },
            factory=factory,
            label=station["name"],
        )

    async def _resume_radio(self) -> ToolResult:
        assert self._storage is not None
        last_id = await self._storage.get_setting(LAST_STATION_KEY)
        if last_id is None:
            return ToolResult(
                output=json.dumps(
                    {
                        "playing": False,
                        "reason": "no_history",
                        "message": self._no_history_message(),
                    },
                    ensure_ascii=False,
                )
            )
        return await self._play_station(last_id)

    def _no_history_message(self) -> str:
        """Localized 'nothing to resume' message surfaced to the LLM.

        The LLM narrates or paraphrases this — it's a content cue rather
        than a literal line — so the strings can stay short and direct
        without persona tone baked in.
        """
        code = self._language_code.lower()
        if code.startswith("es"):
            return "Aún no he reproducido ninguna emisora. ¿Cuál quieres que prenda?"
        if code.startswith("fr"):
            return "Je n'ai encore lancé aucune station. Laquelle veux-tu ?"
        return "I haven't played any station yet. Which one do you want?"

    def _stop_radio(self) -> ToolResult:
        return ToolResult(
            output=json.dumps({"stopped": True}),
            side_effect=CancelMedia(),
        )

    def _list_stations(self) -> ToolResult:
        return ToolResult(
            output=json.dumps(
                {
                    "stations": [
                        {"id": s["id"], "name": s["name"], "description": s["description"]}
                        for s in self._stations
                    ],
                    "default": self._default_id,
                    "count": len(self._stations),
                },
                ensure_ascii=False,
            )
        )

    # --- Internals ---

    def _build_factory(self, url: str, station_id: str) -> Callable[[], AsyncIterator[bytes]]:
        """Build a playback factory for the coordinator's terminal barrier."""
        player = self._player
        logger = self._logger
        assert player is not None
        assert logger is not None

        async def stream() -> AsyncIterator[bytes]:
            await logger.ainfo("radio.stream_started", station_id=station_id)
            try:
                async for chunk in player.stream(url):
                    yield chunk
            except PlayerError as exc:
                await logger.aexception("radio.stream_error", station_id=station_id, exc=str(exc))
            finally:
                await logger.ainfo("radio.stream_ended", station_id=station_id)

        return stream

    def _success_result(
        self,
        *,
        payload: dict[str, Any],
        factory: Callable[[], AsyncIterator[bytes]],
        label: str | None = None,
    ) -> ToolResult:
        chime = self._sounds.get(self._start_sound_role) if self._start_sound_role else None
        # AudioStream + PlaySound are mutually exclusive on a single
        # ToolResult. Radio uses AudioStream for the long-running stream;
        # the chime is yielded as the FIRST chunk of the stream factory
        # instead — same trick the audiobooks skill uses for book_start.
        if chime:
            factory = self._wrap_with_chime(factory, chime)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            side_effect=AudioStream(
                factory=factory,
                label=label,
                preroll_ms=len(chime) * 1000 // 48000 if chime else 0,
            ),
        )

    def _wrap_with_chime(
        self,
        inner: Callable[[], AsyncIterator[bytes]],
        chime_pcm: bytes,
    ) -> Callable[[], AsyncIterator[bytes]]:
        async def wrapped() -> AsyncIterator[bytes]:
            yield chime_pcm
            async for chunk in inner():
                yield chunk

        return wrapped
