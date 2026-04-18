"""Audiobook search and playback skill.

Provides tools for searching a local audiobook library and controlling
playback. Playback is modelled as a `ToolResult(side_effect=AudioStream(
factory=...))` — a closure the `TurnCoordinator` invokes at the turn's
terminal barrier, after the model has finished speaking. The factory
wraps `AudiobookPlayer.stream()` and persists the final playback
position in its `finally` block, so rewind/forward/interrupt all get
correct atomicity without the skill touching storage during dispatch.

State persisted via the SDK's `SkillStorage` (per-skill namespaced KV):
- `last_id`            → most-recently-played book id
- `position:<book_id>` → float seconds for that book

See `docs/turns.md` for the turn model and `docs/skills/audiobooks.md`
for the skill's behavioral contract.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    AudioStream,
    CancelMedia,
    Catalog,
    Hit,
    SkillContext,
    SkillLogger,
    SkillStorage,
    ToolDefinition,
    ToolResult,
)
from huxley_sdk.audio import load_pcm_palette
from huxley_skill_audiobooks.player import (
    BYTES_PER_SECOND,
    AudiobookPlayer,
    PlayerError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

# Default prompt sent to the LLM when a book ends naturally (not interrupted).
# Personas override via `skills.audiobooks.on_complete_prompt` in persona.yaml —
# the default is Spanish because AbuelOS is the only persona today; localize it
# from the persona for any other language.
_DEFAULT_ON_COMPLETE_PROMPT = (
    "El libro ha llegado a su fin. "
    "Felicita al usuario por haber terminado el libro y preguntale "
    "si quiere que busque otro."
)


_AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wav"}

LAST_BOOK_KEY = "last_id"

# Seconds to rewind before the saved position when resuming — avoids starting
# mid-sentence after an interrupt. 20s covers most sentence/paragraph lengths.
RESUME_REWIND_SECONDS = 20.0

# Default jump amount when the user says "atrás un poco" / "adelanta un poco"
# without specifying a time. 30s covers a typical spoken paragraph.
DEFAULT_SEEK_SECONDS = 30.0

# Persistent per-skill key for the user's chosen tempo. Survives across books
# and across server restarts so "más lento" once stays slow forever (or until
# the user asks for "normal").
CURRENT_SPEED_KEY = "current_speed"

# atempo's single-filter range. The persona prompt teaches the LLM these
# bounds; the skill clamps as a defense.
MIN_SPEED = 0.5
MAX_SPEED = 2.0
DEFAULT_SPEED = 1.0


def _clamp_speed(value: float) -> float:
    return max(MIN_SPEED, min(MAX_SPEED, value))


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds to a natural Spanish string."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} hora{'s' if h != 1 else ''} y {m} minuto{'s' if m != 1 else ''}"
    if m:
        return f"{m} minuto{'s' if m != 1 else ''} y {s} segundo{'s' if s != 1 else ''}"
    return f"{s} segundo{'s' if s != 1 else ''}"


def _position_key(book_id: str) -> str:
    return f"position:{book_id}"


def _hit_summary(hit: Hit) -> dict[str, str]:
    """Pluck the LLM-facing fields from a Catalog Hit (id/title/author).

    The catalog stores `path` (and other audio metadata) in `payload`; the
    skill only surfaces the user-readable fields when listing search
    results. Used by `_search` and `_list_in_progress` for consistent
    JSON shape.
    """
    return {
        "id": hit.id,
        "title": hit.fields.get("title", ""),
        "author": hit.fields.get("author", ""),
    }


def _hit_to_book(hit: Hit) -> dict[str, str]:
    """Flatten a Catalog Hit into the dict shape callers of `_resolve_book`
    expect: `{id, title, author, path}`.

    Bridge between the new Catalog primitive (fields + payload split) and
    the legacy book-dict shape inside the skill — keeps the refactor a
    drop-in for `_play`, `_get_progress`, etc., without touching every
    callsite.
    """
    return {
        "id": hit.id,
        "title": hit.fields.get("title", ""),
        "author": hit.fields.get("author", ""),
        "path": str(hit.payload.get("path", "")),
    }


_KNOWN_SOUND_ROLES = ("book_start", "book_end")

# Confidence threshold for fuzzy resolution. Below this, the catalog hit is
# treated as "no match" — preserves the legacy `_resolve_book` behavior
# (sub-threshold = None) after the Catalog refactor (T1.1).
_RESOLVE_THRESHOLD = 0.5
# Threshold for `_search` results. Lower than resolve because search is
# user-facing (returns a list to choose from), not a definitive resolve.
_SEARCH_THRESHOLD = 0.3


class AudiobooksSkill:
    """Skill for searching and playing audiobooks from a local library.

    The library is a directory of audio files. File names (minus extension)
    are treated as book titles. Subdirectories represent authors.

    Structure:
        audiobooks/
        ├── Gabriel García Márquez/
        │   ├── El coronel no tiene quien le escriba.m4b
        │   └── Cien años de soledad.m4b
        └── Jorge Isaacs/
            └── María.m4b
    """

    def __init__(self, *, player: AudiobookPlayer | None = None) -> None:
        # `player` is keyword-only and reserved for tests that inject a mock.
        # In production setup() builds the player from ctx.config.
        self._library_path: Path | None = None
        self._player: AudiobookPlayer | None = player
        self._storage: SkillStorage | None = None
        self._logger: SkillLogger | None = None
        # Personal-content catalog (T1.1). Built at setup() from the library
        # scan; all fuzzy match + prompt-context generation goes through it.
        self._catalog: Catalog | None = None
        # Sound palette: {name: raw_pcm_bytes}. Loaded at setup() from sounds_path.
        # Empty dict = no earcons; skill runs silently.
        self._sounds: dict[str, bytes] = {}
        # Trailing silence injected after book_end earcon to buffer model latency.
        self._silence_ms: int = 500
        # Prompt the LLM narrates when a book ends naturally. Resolved from
        # persona config in setup() with the Spanish default as fallback.
        self._on_complete_prompt: str = _DEFAULT_ON_COMPLETE_PROMPT
        # Live-playback tracking — set at stream start, cleared in finally.
        # Used by get_progress to estimate current position without storage round-trip.
        self._now_playing_id: str | None = None
        self._now_playing_start_pos: float = 0.0
        self._now_playing_start_time: float = 0.0
        # Speed of the currently-playing stream. Tracked per-stream because
        # set_speed restarts the stream and the new value persists; needed by
        # position math (book_advance = wall_elapsed * speed, see
        # docs/triage.md T1.7).
        self._now_playing_speed: float = DEFAULT_SPEED

    def _require_setup(self, attr: str) -> None:
        if getattr(self, attr) is None:
            raise RuntimeError(f"AudiobooksSkill.{attr[1:]} not set — call setup() first")

    @property
    def _storage_req(self) -> SkillStorage:
        self._require_setup("_storage")
        return self._storage  # type: ignore[return-value]

    @property
    def _logger_req(self) -> SkillLogger:
        self._require_setup("_logger")
        return self._logger  # type: ignore[return-value]

    @property
    def _player_req(self) -> AudiobookPlayer:
        self._require_setup("_player")
        return self._player  # type: ignore[return-value]

    @property
    def _catalog_req(self) -> Catalog:
        self._require_setup("_catalog")
        return self._catalog  # type: ignore[return-value]

    @property
    def name(self) -> str:
        return "audiobooks"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_audiobooks",
                description=(
                    "Busca audiolibros en la biblioteca local del usuario. "
                    "Devuelve una lista de libros que coinciden con la búsqueda."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Texto de búsqueda (título, autor, o parte del nombre)",
                        }
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="play_audiobook",
                description=(
                    "Reproduce un audiolibro. Puedes pasarle el ID exacto (de "
                    "search_audiobooks), o simplemente el título o el autor — la "
                    "skill hace coincidencia aproximada. Reanuda desde la última "
                    "posición guardada a menos que se especifique lo contrario. "
                    "Si el usuario dice 'desde el principio', 'desde el inicio', "
                    "'empieza de nuevo', 'vuelve al inicio', 'empieza de cero' "
                    "o algo parecido, pasa `from_beginning: true`. "
                    "Antes de reproducir, acusa recibo brevemente al usuario "
                    "(por ejemplo: 'Ahí le pongo {título}, don.'). Nunca empieces "
                    "el libro en silencio."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "book_id": {
                            "type": "string",
                            "description": (
                                "ID, título o autor del libro. La skill acepta "
                                "coincidencias aproximadas — no necesitas pasar el ID exacto."
                            ),
                        },
                        "from_beginning": {
                            "type": "boolean",
                            "description": (
                                "Pasa `true` cuando el usuario pida explícitamente "
                                "empezar de cero ('desde el principio', 'desde el "
                                "inicio', 'empieza de nuevo', 'vuelve al inicio'). "
                                "Por defecto `false` — reanuda donde se quedó."
                            ),
                        },
                    },
                    "required": ["book_id"],
                },
            ),
            ToolDefinition(
                name="resume_last",
                description=(
                    "Reanuda el audiolibro que el usuario escuchó por última vez, "
                    "desde donde lo dejó. Úsalo cuando el usuario diga 'sigue con "
                    "el libro', 'el libro de anoche', 'continúa el libro' y similares, "
                    "sin mencionar un título específico. Antes de reanudar, acusa "
                    "recibo brevemente ('Sigo con {título} donde lo dejó, don.')."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="audiobook_control",
                description=(
                    "Controla la reproducción del audiolibro actual: pausar, "
                    "reanudar, retroceder, adelantar, detener, o cambiar la "
                    "velocidad. Antes de llamar esta herramienta, acusa recibo "
                    "brevemente al usuario (por ejemplo: 'Listo, retrocedo 30 "
                    "segundos, don.'). Nunca ejecutes la acción en silencio. "
                    "Para retroceder/adelantar, el valor por defecto es 30 "
                    "segundos. Para `set_speed`, usa `speed` entre 0.5 (mitad "
                    "de velocidad) y 2.0 (doble); 1.0 es la velocidad normal. "
                    "Sugerencias: 0.85 para 'un poco más lento', 0.7 para "
                    "'mucho más lento', 1.15 para 'un poco más rápido'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "pause",
                                "resume",
                                "rewind",
                                "forward",
                                "stop",
                                "set_speed",
                            ],
                            "description": "Acción a realizar",
                        },
                        "seconds": {
                            "type": "number",
                            "description": "Segundos para retroceder/adelantar (default: 30)",
                        },
                        "speed": {
                            "type": "number",
                            "description": (
                                "Velocidad de reproducción para `set_speed`. "
                                "Rango 0.5 a 2.0; 1.0 es normal."
                            ),
                        },
                    },
                    "required": ["action"],
                },
            ),
            ToolDefinition(
                name="get_progress",
                description=(
                    "Devuelve el progreso del libro que se está escuchando (o el último "
                    "reproducido): posición actual, duración total y tiempo restante. "
                    "Úsalo cuando el usuario pregunte '¿cuánto llevo?', '¿cuánto me "
                    "queda?', '¿en qué parte voy?' y similares."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="list_in_progress",
                description=(
                    "Lista todos los audiolibros que tienen una posición guardada — "
                    "es decir, los que el usuario ha empezado y no ha terminado. "
                    "Úsalo cuando el usuario pregunte '¿qué libros tengo empezados?', "
                    "'¿cuáles tengo a medias?', '¿qué estaba escuchando?' y similares."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "search_audiobooks":
                return await self._search(args.get("query", ""))
            case "play_audiobook":
                return await self._play(
                    args["book_id"],
                    from_beginning=args.get("from_beginning", False),
                )
            case "resume_last":
                return await self._resume_last()
            case "audiobook_control":
                return await self._control(
                    args["action"],
                    seconds=args.get("seconds", DEFAULT_SEEK_SECONDS),
                    speed=args.get("speed"),
                )
            case "get_progress":
                return await self._get_progress()
            case "list_in_progress":
                return await self._list_in_progress()
            case _:
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def setup(self, ctx: SkillContext) -> None:
        """Resolve config, build the player, scan the library, load sounds."""
        from pathlib import Path

        cfg = ctx.config
        library_raw = cfg.get("library", "data/audiobooks")
        library_path = (ctx.persona_data_dir / library_raw).resolve()
        self._library_path = library_path
        if self._player is None:
            self._player = AudiobookPlayer(
                ffmpeg_path=str(cfg.get("ffmpeg", "ffmpeg")),
                ffprobe_path=str(cfg.get("ffprobe", "ffprobe")),
            )
        self._storage = ctx.storage
        self._logger = ctx.logger
        self._catalog = ctx.catalog()
        for book in self._scan_library(library_path):
            await self._catalog.upsert(
                id=book["id"],
                fields={"title": book["title"], "author": book["author"]},
                payload={"path": book["path"]},
            )

        sounds_enabled = bool(cfg.get("sounds_enabled", True))
        sounds_raw = cfg.get("sounds_path", "sounds")
        sounds_dir = (
            Path(sounds_raw)
            if Path(sounds_raw).is_absolute()
            else (ctx.persona_data_dir / sounds_raw)
        )
        self._sounds = load_pcm_palette(sounds_dir, _KNOWN_SOUND_ROLES) if sounds_enabled else {}
        self._silence_ms = int(cfg.get("silence_ms", 500)) if sounds_enabled else 0
        self._on_complete_prompt = str(cfg.get("on_complete_prompt", _DEFAULT_ON_COMPLETE_PROMPT))

        if sounds_enabled and sounds_dir.exists() and not self._sounds:
            await ctx.logger.awarning(
                "audiobooks.sounds_empty",
                path=str(sounds_dir),
                hint="Directory exists but no PCM16/24kHz/mono WAV files found.",
            )
        if self._sounds and "book_start" not in self._sounds:
            await ctx.logger.awarning(
                "audiobooks.sound_missing", role="book_start", path=str(sounds_dir)
            )
        if self._sounds and "book_end" not in self._sounds:
            await ctx.logger.awarning(
                "audiobooks.sound_missing", role="book_end", path=str(sounds_dir)
            )

        await ctx.logger.ainfo(
            "audiobooks.catalog_loaded",
            count=len(self._catalog),
            path=str(library_path),
            sounds=list(self._sounds.keys()),
        )

    async def teardown(self) -> None:
        """No teardown state — the running factory saves its own position on cancel."""

    def prompt_context(self) -> str:
        """Text injected into the session prompt so the LLM knows what's available.

        Capped at 50 books via Catalog (T1.1). Output is byte-identical to
        the pre-refactor format so the LLM's first-connect behavior is
        preserved.
        """
        if self._catalog is None or len(self._catalog) == 0:
            return ""
        return self._catalog.as_prompt_lines(
            limit=50,
            header="Biblioteca de audiolibros disponibles",
            line=lambda h: f'- "{h.fields["title"]}" por {h.fields["author"]}',
        )

    def _scan_library(self, library_path: Path) -> list[dict[str, str]]:
        """Scan the library directory for audio files."""
        catalog: list[dict[str, str]] = []

        if not library_path.exists():
            return catalog

        for path in sorted(library_path.rglob("*")):
            if path.suffix.lower() not in _AUDIOBOOK_EXTENSIONS:
                continue

            relative = path.relative_to(library_path)
            parts = relative.parts

            if len(parts) > 1:
                author = parts[0]
                title = path.stem
            else:
                author = "Desconocido"
                title = path.stem

            book_id = str(relative)
            catalog.append(
                {
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "path": str(path),
                }
            )

        return catalog

    async def _get_position(self, book_id: str) -> float:
        raw = await self._storage_req.get_setting(_position_key(book_id))
        if raw is None:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    async def _set_position(self, book_id: str, position: float) -> None:
        await self._storage_req.set_setting(_position_key(book_id), str(position))

    async def _search(self, query: str) -> ToolResult:
        """Search the catalog by fuzzy matching against title and author.

        Refactored onto Catalog (T1.1). Behavior preserved:
        - Empty catalog → "biblioteca vacía"
        - Query < 2 chars → return first 20 books in insertion order
        - Otherwise: catalog.search with `_SEARCH_THRESHOLD` filter,
          top 5 results
        """
        catalog = self._catalog_req
        total = len(catalog)
        if total == 0:
            return ToolResult(
                output=json.dumps({"results": [], "message": "La biblioteca está vacía."})
            )

        query_stripped = query.strip()
        if len(query_stripped) < 2:
            preview = list(catalog)[:20]
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [_hit_summary(h) for h in preview],
                        "count": len(preview),
                        "total": total,
                        "message": "Éstos son los libros que tengo.",
                    }
                )
            )

        hits = await catalog.search(query_stripped, limit=5)
        # Catalog returns score-sorted hits with score > 0; apply the
        # search confidence floor that the legacy `_search` used.
        results = [h for h in hits if h.score > _SEARCH_THRESHOLD]

        if not results:
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [],
                        "count": 0,
                        "total": total,
                        "message": (
                            "No encontré nada con esas palabras, don. "
                            "¿Quiere que le diga qué tengo?"
                        ),
                    }
                )
            )

        return ToolResult(
            output=json.dumps(
                {
                    "results": [_hit_summary(h) for h in results],
                    "count": len(results),
                    "message": "Encontré estos libros.",
                }
            )
        )

    async def _resolve_book(self, reference: str) -> dict[str, str] | None:
        """Look up a book by exact ID, or fall back to fuzzy match.

        Refactored onto Catalog (T1.1). Behavior preserved:
        - Exact id hit → resolved
        - Fuzzy match with score > `_RESOLVE_THRESHOLD` (0.5) → resolved
        - Otherwise → None (skill caller's responsibility to surface
          "no encuentro" UX)

        Returns a flat dict for caller compatibility (`{id, title,
        author, path}`); the Catalog stores `path` in `payload` and the
        rest in `fields`.
        """
        catalog = self._catalog_req

        exact = await catalog.get(reference)
        if exact is not None:
            await self._logger_req.ainfo(
                "audiobooks.resolve",
                reference=reference,
                method="exact",
                resolved_id=exact.id,
            )
            return _hit_to_book(exact)

        if len(catalog) == 0:
            await self._logger_req.ainfo("audiobooks.resolve", reference=reference, resolved=None)
            return None

        hits = await catalog.search(reference, limit=1)
        if hits and hits[0].score > _RESOLVE_THRESHOLD:
            top = hits[0]
            await self._logger_req.ainfo(
                "audiobooks.resolve",
                reference=reference,
                method="fuzzy",
                resolved_id=top.id,
                score=round(top.score, 3),
            )
            return _hit_to_book(top)

        best_score = hits[0].score if hits else 0.0
        await self._logger_req.ainfo(
            "audiobooks.resolve",
            reference=reference,
            method="fuzzy",
            resolved=None,
            best_score=round(best_score, 3),
        )
        return None

    def _build_factory(
        self,
        book_id: str,
        path: str,
        start_position: float,
        speed: float = DEFAULT_SPEED,
    ) -> Callable[[], AsyncIterator[bytes]]:
        """Build a playback factory for the coordinator's terminal barrier.

        `speed` is the tempo applied via ffmpeg's atempo filter (see
        AudiobookPlayer.stream). Position math accounts for it: at
        speed=0.5, one wall-clock second = 0.5 book seconds, so
        `book_advance = wall_elapsed * speed`.
        """
        player = self._player_req
        logger = self._logger_req
        set_position = self._set_position
        skill = self  # for live-position tracking via get_progress
        book_start_pcm = self._sounds.get("book_start", b"")
        book_end_pcm = self._sounds.get("book_end", b"")
        # Note: trailing silence buffer is owned by the coordinator (via the
        # AudioStream.completion_silence_ms field) so it can be sent AFTER the
        # request_response, overlapping with model first-token latency.

        async def stream() -> AsyncIterator[bytes]:
            skill._now_playing_id = book_id
            skill._now_playing_start_pos = start_position
            skill._now_playing_start_time = time.monotonic()
            skill._now_playing_speed = speed
            bytes_read = 0
            stream_error: str | None = None
            completed = False
            await logger.ainfo(
                "audiobooks.stream_started",
                book_id=book_id,
                start=start_position,
                speed=speed,
            )
            try:
                if book_start_pcm:
                    yield book_start_pcm

                async for chunk in player.stream(path, start_position=start_position, speed=speed):
                    bytes_read += len(chunk)
                    yield chunk

                # Book audio finished cleanly — mark completed BEFORE yielding the
                # trailing chime. If the user interrupts during the decoration,
                # the position still saves as 0.0 (book is done).
                completed = True

                if book_end_pcm:
                    yield book_end_pcm
            except Exception as exc:
                # CancelledError is BaseException (not Exception) so cancellations
                # propagate through here; only real errors (PlayerError, OSError, etc.)
                # land in this branch.
                stream_error = type(exc).__name__
                await logger.aexception("audiobooks.stream_error", book_id=book_id, exc=str(exc))
            finally:
                skill._now_playing_id = None
                # `bytes_read / BYTES_PER_SECOND` is OUTPUT seconds (wall-clock).
                # Book content advanced = output_seconds * speed. With -re
                # throttling output to realtime, output_seconds == wall_elapsed.
                output_seconds = bytes_read / BYTES_PER_SECOND
                book_advance = output_seconds * speed
                # Natural completion → reset to 0 so next listen starts over.
                # Interrupted (cancel or error) → save current position to resume.
                final_pos = 0.0 if completed else start_position + book_advance
                try:
                    await set_position(book_id, final_pos)
                    await logger.ainfo(
                        "audiobooks.stream_ended",
                        book_id=book_id,
                        elapsed=round(output_seconds, 2),
                        speed=speed,
                        final_pos=round(final_pos, 2),
                        completed=completed,
                        error=stream_error,
                    )
                except Exception:
                    await logger.aexception("audiobooks.position_save_failed")

        return stream

    async def _play(self, book_id: str, *, from_beginning: bool = False) -> ToolResult:
        """Resolve a book, build its factory, stamp last_id."""
        book = await self._resolve_book(book_id)
        if not book:
            return ToolResult(
                output=json.dumps(
                    {
                        "playing": False,
                        "message": (
                            f"No encuentro '{book_id}'. ¿Quiere que le diga qué libros tengo?"
                        ),
                    }
                )
            )

        resolved_id = book["id"]

        start_position = 0.0
        if not from_beginning:
            saved = await self._get_position(resolved_id)
            start_position = max(0.0, saved - RESUME_REWIND_SECONDS)

        try:
            await self._player_req.probe(book["path"])
        except PlayerError as exc:
            await self._logger_req.aerror(
                "audiobooks.probe_failed", book_id=resolved_id, error=str(exc)
            )
            return ToolResult(
                output=json.dumps(
                    {
                        "playing": False,
                        "message": "No pude abrir ese libro. Déjeme intentarlo otra vez.",
                    }
                )
            )

        await self._storage_req.set_setting(LAST_BOOK_KEY, resolved_id)
        speed = await self._get_speed()
        await self._logger_req.ainfo(
            "audiobooks.factory_built",
            book_id=resolved_id,
            start_position=start_position,
            speed=speed,
        )

        factory = self._build_factory(resolved_id, book["path"], start_position, speed=speed)

        resuming = start_position > 0
        return ToolResult(
            output=json.dumps(
                {
                    "playing": True,
                    "title": book["title"],
                    "author": book["author"],
                    "position_seconds": start_position,
                    "position_label": _fmt_duration(start_position) if resuming else "el inicio",
                    "resuming": resuming,
                }
            ),
            side_effect=AudioStream(
                factory=factory,
                on_complete_prompt=self._on_complete_prompt,
                completion_silence_ms=self._silence_ms,
            ),
        )

    async def _resume_last(self) -> ToolResult:
        """Resume the most-recently-played book from its saved position."""
        last_id = await self._storage_req.get_setting(LAST_BOOK_KEY)
        if not last_id:
            return ToolResult(
                output=json.dumps(
                    {
                        "resumed": False,
                        "message": "No tiene ningún libro a medias. ¿Busco algo?",
                    }
                )
            )
        return await self._play(last_id, from_beginning=False)

    async def _get_speed(self) -> float:
        """Return the persisted playback speed, default 1.0 if unset."""
        raw = await self._storage_req.get_setting(CURRENT_SPEED_KEY)
        if raw is None:
            return DEFAULT_SPEED
        try:
            return _clamp_speed(float(raw))
        except (TypeError, ValueError):
            return DEFAULT_SPEED

    def _live_position(self) -> float | None:
        """Live position estimate, accounting for current playback speed.

        Returns `None` if no book is currently playing. Used by both
        `_get_progress` (to report position without storage round-trip) and
        `set_speed` (to compute the resume point when restarting the stream
        at a new tempo). Math: `book_advance = wall_elapsed * speed`.
        """
        if self._now_playing_id is None:
            return None
        elapsed = time.monotonic() - self._now_playing_start_time
        return self._now_playing_start_pos + elapsed * self._now_playing_speed

    async def _get_progress(self) -> ToolResult:
        """Return position, duration, and remaining time for the active or last book."""
        # Prefer live tracking (book is currently streaming) for accuracy.
        book_id = self._now_playing_id
        live_pos = self._live_position()
        if book_id is not None and live_pos is not None:
            current_pos = live_pos
            is_live = True
        else:
            book_id = await self._storage_req.get_setting(LAST_BOOK_KEY)
            if not book_id:
                return ToolResult(
                    output=json.dumps(
                        {"message": "No hay ningún libro activo. ¿Quiere que busque uno?"}
                    )
                )
            current_pos = await self._get_position(book_id)
            is_live = False

        book = await self._resolve_book(book_id)
        if book is None:
            return ToolResult(
                output=json.dumps({"message": "No encuentro ese libro en la biblioteca."})
            )

        # Probe for total duration. Fail gracefully if ffprobe is unavailable.
        total_duration: float | None = None
        try:
            probe = await self._player_req.probe(book["path"])
            raw = probe.get("format", {}).get("duration")
            if raw is not None:
                total_duration = float(raw)
        except Exception:
            pass

        result: dict[str, Any] = {
            "title": book["title"],
            "author": book["author"],
            "position_seconds": round(current_pos, 1),
            "position_label": _fmt_duration(current_pos),
            "playing": is_live,
        }
        if total_duration:
            remaining = max(0.0, total_duration - current_pos)
            result["total_seconds"] = round(total_duration, 1)
            result["remaining_seconds"] = round(remaining, 1)
            result["remaining_label"] = _fmt_duration(remaining)
            result["percent"] = min(100, int(current_pos / total_duration * 100))

        return ToolResult(output=json.dumps(result))

    async def _list_in_progress(self) -> ToolResult:
        """Return all books that have a non-zero saved position."""
        in_progress = []
        for hit in self._catalog_req:
            pos = await self._get_position(hit.id)
            if pos > 0:
                in_progress.append(
                    {
                        "id": hit.id,
                        "title": hit.fields.get("title", ""),
                        "author": hit.fields.get("author", ""),
                        "position_seconds": round(pos, 1),
                        "position_label": _fmt_duration(pos),
                    }
                )

        if not in_progress:
            return ToolResult(
                output=json.dumps(
                    {"message": "No tiene ningún libro empezado. ¿Quiere que le busque uno?"}
                )
            )

        return ToolResult(
            output=json.dumps(
                {
                    "count": len(in_progress),
                    "books": in_progress,
                }
            )
        )

    async def _control(
        self,
        action: str,
        seconds: float = DEFAULT_SEEK_SECONDS,
        speed: float | None = None,
    ) -> ToolResult:
        """Control current playback."""
        match action:
            case "pause":
                return ToolResult(
                    output=json.dumps({"paused": True}),
                    side_effect=CancelMedia(),
                )

            case "stop":
                return ToolResult(
                    output=json.dumps({"stopped": True}),
                    side_effect=CancelMedia(),
                )

            case "resume":
                book_id = await self._storage_req.get_setting(LAST_BOOK_KEY)
                if book_id is None:
                    return ToolResult(
                        output=json.dumps(
                            {
                                "resumed": False,
                                "message": "No hay ningún libro para reanudar.",
                            }
                        )
                    )
                return await self._play(book_id, from_beginning=False)

            case "rewind" | "forward":
                book_id = await self._storage_req.get_setting(LAST_BOOK_KEY)
                if book_id is None:
                    return ToolResult(
                        output=json.dumps(
                            {
                                "ok": False,
                                "message": "No hay ningún libro activo para mover.",
                            }
                        )
                    )
                saved = await self._get_position(book_id)
                delta = abs(seconds)
                new_pos = max(0.0, saved - delta) if action == "rewind" else saved + delta
                await self._logger_req.ainfo(
                    "audiobooks.seek",
                    action=action,
                    book_id=book_id,
                    from_pos=round(saved, 2),
                    to_pos=round(new_pos, 2),
                    delta=delta,
                )
                book = await self._resolve_book(book_id)
                if book is None:
                    return ToolResult(
                        output=json.dumps(
                            {
                                "ok": False,
                                "message": "No encuentro ese libro, don.",
                            }
                        )
                    )
                speed = await self._get_speed()
                factory = self._build_factory(book_id, book["path"], new_pos, speed=speed)
                return ToolResult(
                    output=json.dumps(
                        {
                            "playing": True,
                            "title": book["title"],
                            "author": book["author"],
                            "position_seconds": new_pos,
                            "position_label": _fmt_duration(new_pos),
                        }
                    ),
                    side_effect=AudioStream(
                        factory=factory,
                        on_complete_prompt=self._on_complete_prompt,
                        completion_silence_ms=self._silence_ms,
                    ),
                )

            case "set_speed":
                return await self._set_speed(speed)

            case _:
                return ToolResult(
                    output=json.dumps({"ok": False, "message": "No entendí la acción."})
                )

    async def _set_speed(self, speed: float | None) -> ToolResult:
        """Persist new playback speed and (re)start playback at that tempo.

        Three cases:
        1. Live stream playing → restart from live position at new speed.
        2. Nothing live but a book is in-progress (saved `last_id`) →
           resume that book at the new speed. The natural user flow is
           "PTT to interrupt → 'más lento' → expect playback to continue
           slower" — without this, set_speed would silently persist the
           value and the user would be left in silence (T1.7 follow-up
           bug captured live 2026-04-18).
        3. No live stream + no last_id → ack only. Nothing to play.
        """
        if speed is None:
            return ToolResult(
                output=json.dumps(
                    {
                        "ok": False,
                        "message": "Indícame la velocidad. Por ejemplo, 0.85 para más lento.",
                    }
                )
            )
        new_speed = _clamp_speed(float(speed))
        await self._storage_req.set_setting(CURRENT_SPEED_KEY, str(new_speed))

        live_pos = self._live_position()
        book_id = self._now_playing_id
        await self._logger_req.ainfo(
            "audiobooks.speed_set",
            speed=new_speed,
            requested=speed,
            had_active_stream=book_id is not None and live_pos is not None,
        )

        # Case 2 + 3: nothing live. Try to resume the last book if any,
        # otherwise just ack. _play loads speed from storage (which we just
        # wrote above), so the new tempo applies on the next stream.
        if book_id is None or live_pos is None:
            last_id = await self._storage_req.get_setting(LAST_BOOK_KEY)
            if last_id is not None:
                return await self._play(last_id, from_beginning=False)
            return ToolResult(
                output=json.dumps({"ok": True, "speed": new_speed, "playing": False})
            )

        # A book is playing: restart from current position at the new speed.
        # Save the position-at-cut-over so the new factory starts there. The
        # outgoing stream's finally-block will also save its own final_pos
        # when the coordinator cancels it; the new stream's start_position
        # was captured here, so its own writes on cancel/end will overwrite
        # cleanly with the right base.
        book = await self._resolve_book(book_id)
        if book is None:
            # Lost the book record between play and set_speed — degrade to ack.
            return ToolResult(
                output=json.dumps({"ok": True, "speed": new_speed, "playing": False})
            )
        await self._set_position(book_id, live_pos)
        factory = self._build_factory(book_id, book["path"], live_pos, speed=new_speed)
        return ToolResult(
            output=json.dumps(
                {
                    "ok": True,
                    "speed": new_speed,
                    "playing": True,
                    "title": book["title"],
                    "position_seconds": round(live_pos, 1),
                    "position_label": _fmt_duration(live_pos),
                }
            ),
            side_effect=AudioStream(
                factory=factory,
                on_complete_prompt=self._on_complete_prompt,
                completion_silence_ms=self._silence_ms,
            ),
        )
