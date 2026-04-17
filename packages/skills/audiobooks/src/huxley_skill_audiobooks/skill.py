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

_AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wav"}

LAST_BOOK_KEY = "last_id"


def _position_key(book_id: str) -> str:
    return f"position:{book_id}"


def _fuzzy_score(query: str, candidate: str) -> float:
    """Case-insensitive fuzzy match score between 0 and 1."""
    return SequenceMatcher(None, query.lower(), candidate.lower()).ratio()


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
                    "ejemplo: 'Listo, retrocedo 10 segundos, don.'). Nunca "
                    "ejecutes la acción en silencio. Para retroceder/adelantar, "
                    "el valor por defecto es 10 segundos si el usuario no "
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
                            "description": "Segundos para retroceder/adelantar (default: 10)",
                        },
                    },
                    "required": ["action"],
                },
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
                    seconds=args.get("seconds", 10),
                )
            case _:
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def setup(self, ctx: SkillContext) -> None:
        """Resolve config, build the player, scan the library."""
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
        await ctx.logger.ainfo(
            "audiobooks.catalog_loaded",
            count=len(self._catalog),
            path=str(library_path),
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
        player = self._player
        logger = self._logger
        set_position = self._set_position

        async def stream() -> AsyncIterator[bytes]:
            bytes_read = 0
            await logger.ainfo("audiobooks.stream_started", book_id=book_id, start=start_position)
            try:
                async for chunk in player.stream(path, start_position=start_position):
                    bytes_read += len(chunk)
                    yield chunk
            finally:
                elapsed = bytes_read / BYTES_PER_SECOND
                final_pos = start_position + elapsed
                try:
                    await set_position(book_id, final_pos)
                    await logger.ainfo(
                        "audiobooks.stream_ended",
                        book_id=book_id,
                        elapsed=round(elapsed, 2),
                        final_pos=round(final_pos, 2),
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
            start_position = await self._get_position(resolved_id)

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

        return ToolResult(
            output=json.dumps(
                {
                    "playing": True,
                    "title": book["title"],
                    "author": book["author"],
                    "position": start_position,
                    "message": f'Cargado: "{book["title"]}" por {book["author"]}.',
                }
            ),
            side_effect=AudioStream(factory=factory),
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

    async def _control(self, action: str, seconds: float = 10) -> ToolResult:
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
                            "position": new_pos,
                            "message": (
                                f"Retrocediendo a {int(new_pos)}s"
                                if action == "rewind"
                                else f"Adelantando a {int(new_pos)}s"
                            ),
                        }
                    ),
                    side_effect=AudioStream(factory=factory),
                )

            case _:
                return ToolResult(
                    output=json.dumps({"ok": False, "message": "No entendí la acción."})
                )
