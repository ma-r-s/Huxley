"""Audiobook search and playback skill.

Provides tools for searching a local audiobook library and controlling
playback via the `AudiobookPlayer` (ffmpeg subprocess that streams PCM16
over the same WebSocket channel as the OpenAI model audio). The LLM handles
the conversational discovery flow — this skill just does file search and
media control.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

import structlog

from abuel_os.media.audiobook_player import PlayerError
from abuel_os.types import ToolAction, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from abuel_os.media.audiobook_player import AudiobookPlayer
    from abuel_os.storage.db import Storage

logger = structlog.get_logger()

_AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wav"}

LAST_BOOK_SETTING = "last_audiobook_id"


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

    def __init__(
        self,
        library_path: Path,
        player: AudiobookPlayer,
        storage: Storage,
    ) -> None:
        self._library_path = library_path
        self._player = player
        self._storage = storage
        self._catalog: list[dict[str, str]] = []
        self._current_book_id: str | None = None

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
                    seconds=args.get("seconds", 30),
                )
            case _:
                return ToolResult(output=json.dumps({"error": f"Unknown tool: {tool_name}"}))

    async def setup(self) -> None:
        """Scan the library directory and build the catalog."""
        self._catalog = self._scan_library()
        await logger.ainfo(
            "audiobooks_catalog_loaded",
            count=len(self._catalog),
            path=str(self._library_path),
        )

    async def teardown(self) -> None:
        """Persist current position and stop the player."""
        await self.save_current_position()
        await self._player.stop()

    async def save_current_position(self) -> None:
        """Persist the current book's position to storage, if any book is loaded."""
        if self._current_book_id is None:
            return
        position = self._player.position
        if position > 0:
            await self._storage.save_audiobook_position(self._current_book_id, position)
            await logger.ainfo(
                "audiobook_position_saved",
                book_id=self._current_book_id,
                position=position,
            )

    def prompt_context(self) -> str:
        """Text injected into the session prompt so the LLM knows what's available.

        Returned at connect time by `SkillRegistry.get_prompt_context()` and
        appended to `Settings.system_prompt` before `session.update`. The LLM
        reads this and can answer _"¿qué libros tienes?"_ without having to
        call `search_audiobooks` first.

        Capped at 50 books to keep the prompt bounded. If the library ever
        grows past that, switch to tool-based listing instead.
        """
        if not self._catalog:
            return ""
        lines = ["Biblioteca de audiolibros disponibles:"]
        for book in self._catalog[:50]:
            lines.append(f'- "{book["title"]}" por {book["author"]}')
        if len(self._catalog) > 50:
            lines.append(f"(y {len(self._catalog) - 50} más, búscalos por título o autor)")
        return "\n".join(lines)

    def _scan_library(self) -> list[dict[str, str]]:
        """Scan the library directory for audio files."""
        catalog: list[dict[str, str]] = []

        if not self._library_path.exists():
            return catalog

        for path in sorted(self._library_path.rglob("*")):
            if path.suffix.lower() not in _AUDIOBOOK_EXTENSIONS:
                continue

            relative = path.relative_to(self._library_path)
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

    async def _search(self, query: str) -> ToolResult:
        """Search the catalog by fuzzy matching against title and author.

        For an empty or very short query (< 2 chars), returns the full catalog
        instead of filtering. This covers the _"¿qué libros tienes?"_ case
        where the LLM calls search without a specific term.
        """
        if not self._catalog:
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [],
                        "message": (
                            "La biblioteca está vacía. Hay que pedirle a Mario que agregue libros."
                        ),
                    }
                )
            )

        query_stripped = query.strip()
        if len(query_stripped) < 2:
            # Empty/very-short query → list the whole catalog (capped).
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

    def _resolve_book(self, reference: str) -> dict[str, str] | None:
        """Look up a book by exact ID, or fall back to fuzzy title/author match.

        The LLM often passes the human-readable title (e.g. `"Cien años de
        soledad"`) as `book_id` because that's what the prompt context shows
        it. The real internal IDs are relative paths (`"Gabriel García
        Márquez/Cien años de soledad.m4b"`), so an exact match would fail
        and strand the user on a "no encuentro el libro" dead-end. We try
        exact first, then fuzzy across id / title / author / author+title,
        with a high confidence threshold (0.5) so we don't guess wildly.
        """
        # Exact ID match first — fastest, zero-ambiguity path.
        exact = next((b for b in self._catalog if b["id"] == reference), None)
        if exact is not None:
            return exact

        if not self._catalog:
            return None

        # Fuzzy fallback across every relevant string the user might have passed.
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
            return best_book
        return None

    async def _play(self, book_id: str, *, from_beginning: bool = False) -> ToolResult:
        """Load an audiobook in paused state and return START_PLAYBACK.

        The player is pre-loaded but NOT streaming yet (paused=True). The
        state machine transition to PLAYING is deferred by `SessionManager`
        until after the model has narrated the verbal acknowledgement (the
        `message` field in this return). `Application._enter_playing` resumes
        the player, so book audio begins streaming only after the ack is
        fully spoken. This prevents the book from "jumping straight in"
        without warning.
        """
        book = self._resolve_book(book_id)
        if not book:
            # Not a dead-end: the LLM can fall back to search + offer alternative.
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

        # After fuzzy resolution, use the book's canonical ID — NOT the
        # original parameter (which could have been a title like
        # "Cien años de soledad" when the real id is "García Márquez/Cien…m4b").
        resolved_id = book["id"]

        start_position = 0.0
        if not from_beginning:
            start_position = await self._storage.get_audiobook_position(resolved_id)

        try:
            await self._player.load(book["path"], start_position=start_position, paused=True)
        except PlayerError as exc:
            await logger.aerror("audiobook_load_failed", book_id=resolved_id, error=str(exc))
            return ToolResult(
                output=json.dumps(
                    {
                        "playing": False,
                        "message": "No pude abrir ese libro. Déjeme intentarlo otra vez.",
                    }
                )
            )

        self._current_book_id = resolved_id
        await self._storage.set_setting(LAST_BOOK_SETTING, resolved_id)
        await logger.ainfo(
            "audiobook_playing",
            book_id=resolved_id,
            start_position=start_position,
        )

        return ToolResult(
            output=json.dumps(
                {
                    "playing": True,
                    "title": book["title"],
                    "author": book["author"],
                    "position": start_position,
                    # Short factual description, no instructions. The model
                    # narrates its own ack BEFORE calling the tool (per the
                    # tool description); this field is kept for the
                    # nunca-decir-no convention and for dev observability.
                    "message": f'Cargado: "{book["title"]}" por {book["author"]}.',
                }
            ),
            action=ToolAction.START_PLAYBACK,
        )

    async def _resume_last(self) -> ToolResult:
        """Resume the most-recently-played book from its saved position."""
        last_id = await self._storage.get_setting(LAST_BOOK_SETTING)
        if not last_id:
            return ToolResult(
                output=json.dumps(
                    {
                        "resumed": False,
                        "message": "No tiene ningún libro a medias. ¿Busco algo?",
                    }
                )
            )
        # Delegate to _play, which handles load + persistence + action tagging.
        # `from_beginning=False` so the saved position is honored.
        return await self._play(last_id, from_beginning=False)

    async def _current_or_last_book_id(self) -> str | None:
        """Return the in-memory current book id, falling back to the stored
        `last_audiobook_id` setting. Used by control actions that fire from
        CONVERSING state (where the player has been stopped by `_exit_playing`
        and `_current_book_id` may or may not still be set).
        """
        if self._current_book_id is not None:
            return self._current_book_id
        return await self._storage.get_setting(LAST_BOOK_SETTING)

    async def _control(self, action: str, seconds: float = 10) -> ToolResult:
        """Control current playback.

        Rewind, forward, and resume all funnel through `_play` so they share
        the pause-then-resume-on-state-transition flow — otherwise ffmpeg
        would start streaming the new position while the model is still
        narrating its verbal ack, and the two audios would fight.

        Pause and stop don't resume playback, so they're plain no-side-effect
        acknowledgements.
        """
        match action:
            case "pause":
                # The player was already stopped by `_exit_playing` when the
                # user pressed PTT. Just save and acknowledge.
                await self.save_current_position()
                return ToolResult(output=json.dumps({"paused": True, "message": "Pausado."}))

            case "stop":
                await self.save_current_position()
                await self._player.stop()
                return ToolResult(output=json.dumps({"stopped": True, "message": "Detenido."}))

            case "resume":
                book_id = await self._current_or_last_book_id()
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
                book_id = await self._current_or_last_book_id()
                if book_id is None:
                    return ToolResult(
                        output=json.dumps(
                            {
                                "ok": False,
                                "message": "No hay ningún libro activo para mover.",
                            }
                        )
                    )
                saved = await self._storage.get_audiobook_position(book_id)
                delta = abs(seconds)
                new_pos = max(0.0, saved - delta) if action == "rewind" else saved + delta
                # Persist the new position BEFORE delegating to `_play`, which
                # reads it back via `get_audiobook_position`.
                await self._storage.save_audiobook_position(book_id, new_pos)
                return await self._play(book_id, from_beginning=False)

            case _:
                return ToolResult(
                    output=json.dumps({"ok": False, "message": "No entendí la acción."})
                )
