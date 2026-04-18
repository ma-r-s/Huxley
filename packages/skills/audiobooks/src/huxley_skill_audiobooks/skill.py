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
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    AudioStream,
    CancelMedia,
    SkillContext,
    SkillLogger,
    SkillStorage,
    ToolDefinition,
    ToolResult,
)
from huxley_skill_audiobooks.player import (
    BYTES_PER_SECOND,
    AudiobookPlayer,
    PlayerError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

# Prompt sent to the LLM when a book ends naturally (not interrupted).
# The model narrates it in the persona's tone and language.
_ON_COMPLETE_PROMPT = (
    "El libro ha llegado a su fin. "
    "Felicita al usuario por haber terminado el libro y preguntale "
    "si quiere que busque otro."
)

# WAV header size in bytes (standard PCM WAV: 44 bytes). Stripped when
# loading sound files so only raw PCM16 24kHz mono bytes remain.
_WAV_HEADER_BYTES = 44

_AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wav"}

LAST_BOOK_KEY = "last_id"

# Seconds to rewind before the saved position when resuming — avoids starting
# mid-sentence after an interrupt. 20s covers most sentence/paragraph lengths.
RESUME_REWIND_SECONDS = 20.0

# Default jump amount when the user says "atrás un poco" / "adelanta un poco"
# without specifying a time. 30s covers a typical spoken paragraph.
DEFAULT_SEEK_SECONDS = 30.0


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


def _fuzzy_score(query: str, candidate: str) -> float:
    """Case-insensitive fuzzy match score between 0 and 1."""
    return SequenceMatcher(None, query.lower(), candidate.lower()).ratio()


def _load_sound_palette(directory: Path) -> dict[str, bytes]:
    """Load *.wav files from directory; strip WAV header; cache as raw PCM16."""
    from pathlib import Path as _Path

    palette: dict[str, bytes] = {}
    d = _Path(directory)
    if not d.exists():
        return palette
    for wav in sorted(d.glob("*.wav")):
        raw = wav.read_bytes()
        if len(raw) > _WAV_HEADER_BYTES:
            palette[wav.stem] = raw[_WAV_HEADER_BYTES:]
    return palette


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
        self._catalog: list[dict[str, str]] = []
        # Sound palette: {name: raw_pcm_bytes}. Loaded at setup() from sounds_path.
        # Empty dict = no earcons; skill runs silently.
        self._sounds: dict[str, bytes] = {}
        # Trailing silence injected after book_end earcon to buffer model latency.
        self._silence_ms: int = 500
        # Live-playback tracking — set at stream start, cleared in finally.
        # Used by get_progress to estimate current position without storage round-trip.
        self._now_playing_id: str | None = None
        self._now_playing_start_pos: float = 0.0
        self._now_playing_start_time: float = 0.0

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
                            "description": "Si es true, empieza desde el inicio",
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
                    "reanudar, retroceder, adelantar, detener. Antes de llamar "
                    "esta herramienta, acusa recibo brevemente al usuario (por "
                    "ejemplo: 'Listo, retrocedo 30 segundos, don.'). Nunca "
                    "ejecutes la acción en silencio. Para retroceder/adelantar, "
                    "el valor por defecto es 30 segundos si el usuario no "
                    "especifica un tiempo."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["pause", "resume", "rewind", "forward", "stop"],
                            "description": "Acción a realizar",
                        },
                        "seconds": {
                            "type": "number",
                            "description": "Segundos para retroceder/adelantar (default: 30)",
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
        self._catalog = self._scan_library(library_path)

        sounds_raw = cfg.get("sounds_path", "sounds")
        sounds_dir = (
            Path(sounds_raw)
            if Path(sounds_raw).is_absolute()
            else (ctx.persona_data_dir / sounds_raw)
        )
        self._sounds = _load_sound_palette(sounds_dir)
        self._silence_ms = int(cfg.get("silence_ms", 500))

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

        Capped at 50 books to keep the prompt bounded.
        """
        if not self._catalog:
            return ""
        lines = ["Biblioteca de audiolibros disponibles:"]
        for book in self._catalog[:50]:
            lines.append(f'- "{book["title"]}" por {book["author"]}')
        if len(self._catalog) > 50:
            lines.append(f"(y {len(self._catalog) - 50} más, búscalos por título o autor)")
        return "\n".join(lines)

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
        """Search the catalog by fuzzy matching against title and author."""
        if not self._catalog:
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [],
                        "message": "La biblioteca está vacía.",
                    }
                )
            )

        query_stripped = query.strip()
        if len(query_stripped) < 2:
            results = self._catalog[:20]
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [
                            {"id": b["id"], "title": b["title"], "author": b["author"]}
                            for b in results
                        ],
                        "count": len(results),
                        "total": len(self._catalog),
                        "message": "Éstos son los libros que tengo.",
                    }
                )
            )

        scored = []
        for book in self._catalog:
            title_score = _fuzzy_score(query_stripped, book["title"])
            author_score = _fuzzy_score(query_stripped, book["author"])
            combined = f"{book['author']} {book['title']}"
            combined_score = _fuzzy_score(query_stripped, combined)
            best = max(title_score, author_score, combined_score)
            if best > 0.3:
                scored.append((best, book))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [book for _, book in scored[:5]]

        if not results:
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [],
                        "count": 0,
                        "total": len(self._catalog),
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
                    "results": [
                        {"id": b["id"], "title": b["title"], "author": b["author"]}
                        for b in results
                    ],
                    "count": len(results),
                    "message": "Encontré estos libros.",
                }
            )
        )

    async def _resolve_book(self, reference: str) -> dict[str, str] | None:
        """Look up a book by exact ID, or fall back to fuzzy title/author match."""
        exact = next((b for b in self._catalog if b["id"] == reference), None)
        if exact is not None:
            await self._logger_req.ainfo(
                "audiobooks.resolve",
                reference=reference,
                method="exact",
                resolved_id=exact["id"],
            )
            return exact

        if not self._catalog:
            await self._logger_req.ainfo("audiobooks.resolve", reference=reference, resolved=None)
            return None

        best_score = 0.0
        best_book: dict[str, str] | None = None
        for candidate in self._catalog:
            candidates = [
                candidate["title"],
                candidate["author"],
                f"{candidate['author']} {candidate['title']}",
                candidate["id"],
            ]
            score = max(_fuzzy_score(reference, c) for c in candidates)
            if score > best_score:
                best_score = score
                best_book = candidate
        if best_book is not None and best_score > 0.5:
            await self._logger_req.ainfo(
                "audiobooks.resolve",
                reference=reference,
                method="fuzzy",
                resolved_id=best_book["id"],
                score=round(best_score, 3),
            )
            return best_book
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
    ) -> Callable[[], AsyncIterator[bytes]]:
        """Build a playback factory for the coordinator's terminal barrier."""
        player = self._player_req
        logger = self._logger_req
        set_position = self._set_position
        skill = self  # for live-position tracking via get_progress
        book_start_pcm = self._sounds.get("book_start", b"")
        book_end_pcm = self._sounds.get("book_end", b"")
        # Trailing silence (PCM16 24kHz mono) buffering model generation latency.
        sample_rate, channels, bytes_per_sample = 24000, 1, 2
        silence_bytes = b"\x00" * (
            sample_rate * channels * bytes_per_sample * self._silence_ms // 1000
        )

        async def stream() -> AsyncIterator[bytes]:
            skill._now_playing_id = book_id
            skill._now_playing_start_pos = start_position
            skill._now_playing_start_time = time.monotonic()
            bytes_read = 0
            stream_error: str | None = None
            completed = False
            await logger.ainfo("audiobooks.stream_started", book_id=book_id, start=start_position)
            try:
                if book_start_pcm:
                    yield book_start_pcm

                async for chunk in player.stream(path, start_position=start_position):
                    bytes_read += len(chunk)
                    yield chunk

                # Natural completion — trailing earcon + silence before on_complete_prompt.
                if book_end_pcm:
                    yield book_end_pcm
                yield silence_bytes
                completed = True
            except Exception as exc:
                # CancelledError is BaseException (not Exception) so cancellations
                # propagate through here; only real errors (PlayerError, OSError, etc.)
                # land in this branch.
                stream_error = type(exc).__name__
                await logger.aexception("audiobooks.stream_error", book_id=book_id, exc=str(exc))
            finally:
                skill._now_playing_id = None
                elapsed = bytes_read / BYTES_PER_SECOND
                # Natural completion → reset to 0 so next listen starts over.
                # Interrupted (cancel or error) → save current position to resume.
                final_pos = 0.0 if completed else start_position + elapsed
                try:
                    await set_position(book_id, final_pos)
                    await logger.ainfo(
                        "audiobooks.stream_ended",
                        book_id=book_id,
                        elapsed=round(elapsed, 2),
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
        await self._logger_req.ainfo(
            "audiobooks.factory_built",
            book_id=resolved_id,
            start_position=start_position,
        )

        factory = self._build_factory(resolved_id, book["path"], start_position)

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
            side_effect=AudioStream(factory=factory, on_complete_prompt=_ON_COMPLETE_PROMPT),
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

    async def _get_progress(self) -> ToolResult:
        """Return position, duration, and remaining time for the active or last book."""
        # Prefer live tracking (book is currently streaming) for accuracy.
        book_id = self._now_playing_id
        if book_id is not None:
            elapsed = time.monotonic() - self._now_playing_start_time
            current_pos = self._now_playing_start_pos + elapsed
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
        for book in self._catalog:
            pos = await self._get_position(book["id"])
            if pos > 0:
                in_progress.append(
                    {
                        "id": book["id"],
                        "title": book["title"],
                        "author": book["author"],
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

    async def _control(self, action: str, seconds: float = DEFAULT_SEEK_SECONDS) -> ToolResult:
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
                factory = self._build_factory(book_id, book["path"], new_pos)
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
                    side_effect=AudioStream(factory=factory),
                )

            case _:
                return ToolResult(
                    output=json.dumps({"ok": False, "message": "No entendí la acción."})
                )
