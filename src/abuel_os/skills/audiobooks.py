"""Audiobook search and playback skill.

Provides tools for searching a local audiobook library and controlling
playback via mpv. The LLM handles the conversational discovery flow —
this skill just does the file search and media control.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

import structlog

from abuel_os.types import ToolAction, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from abuel_os.media.mpv import MpvClient
    from abuel_os.storage.db import Storage

logger = structlog.get_logger()

_AUDIOBOOK_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wav"}


def _fuzzy_score(query: str, candidate: str) -> float:
    """Case-insensitive fuzzy match score between 0 and 1."""
    return SequenceMatcher(None, query.lower(), candidate.lower()).ratio()


class AudiobooksSkill:
    """Skill for searching and playing audiobooks from a local library.

    The library is a directory of audio files. File names (minus extension)
    are treated as book titles. Subdirectories can represent authors.

    Structure:
        audiobooks/
        ├── Gabriel García Márquez/
        │   ├── El coronel no tiene quien le escriba.mp3
        │   └── Cien años de soledad.mp3
        └── Jorge Isaacs/
            └── María.mp3
    """

    def __init__(
        self,
        library_path: Path,
        mpv: MpvClient,
        storage: Storage,
    ) -> None:
        self._library_path = library_path
        self._mpv = mpv
        self._storage = storage
        self._catalog: list[dict[str, str]] = []

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
                    "Reproduce un audiolibro. Reanuda desde la última posición guardada "
                    "a menos que se especifique lo contrario."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "book_id": {
                            "type": "string",
                            "description": "ID del libro (devuelto por search_audiobooks)",
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
                name="audiobook_control",
                description=(
                    "Controla la reproducción del audiolibro actual: "
                    "pausar, reanudar, retroceder, adelantar."
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
        # Save current playback position if playing
        pass

    def _scan_library(self) -> list[dict[str, str]]:
        """Scan the library directory for audio files."""
        catalog: list[dict[str, str]] = []

        if not self._library_path.exists():
            return catalog

        for path in sorted(self._library_path.rglob("*")):
            if path.suffix.lower() not in _AUDIOBOOK_EXTENSIONS:
                continue

            # If file is in a subdirectory, treat parent as author
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
        """Search the catalog by fuzzy matching against title and author."""
        if not self._catalog:
            return ToolResult(
                output=json.dumps(
                    {
                        "results": [],
                        "message": "No hay audiolibros en la biblioteca.",
                    }
                )
            )

        scored = []
        for book in self._catalog:
            title_score = _fuzzy_score(query, book["title"])
            author_score = _fuzzy_score(query, book["author"])
            combined = f"{book['author']} {book['title']}"
            combined_score = _fuzzy_score(query, combined)
            best = max(title_score, author_score, combined_score)
            if best > 0.3:
                scored.append((best, book))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [book for _, book in scored[:5]]

        return ToolResult(
            output=json.dumps(
                {
                    "results": [
                        {"id": b["id"], "title": b["title"], "author": b["author"]}
                        for b in results
                    ],
                    "count": len(results),
                }
            )
        )

    async def _play(self, book_id: str, *, from_beginning: bool = False) -> ToolResult:
        """Start playing an audiobook."""
        book = next((b for b in self._catalog if b["id"] == book_id), None)
        if not book:
            return ToolResult(output=json.dumps({"error": f"Libro no encontrado: {book_id}"}))

        await self._mpv.loadfile(book["path"])

        if not from_beginning:
            position = await self._storage.get_audiobook_position(book_id)
            if position > 0:
                await self._mpv.seek_absolute(position)
                await logger.ainfo("audiobook_resumed", book_id=book_id, position=position)

        return ToolResult(
            output=json.dumps(
                {
                    "playing": True,
                    "title": book["title"],
                    "author": book["author"],
                }
            ),
            action=ToolAction.START_PLAYBACK,
        )

    async def _control(self, action: str, seconds: float = 30) -> ToolResult:
        """Control current playback."""
        match action:
            case "pause":
                await self._mpv.pause()
            case "resume":
                await self._mpv.resume()
            case "rewind":
                await self._mpv.seek(-abs(seconds))
            case "forward":
                await self._mpv.seek(abs(seconds))
            case "stop":
                position = await self._mpv.get_position()
                await self._mpv.stop_playback()
                return ToolResult(output=json.dumps({"stopped": True, "position": position}))
            case _:
                return ToolResult(output=json.dumps({"error": f"Acción desconocida: {action}"}))

        return ToolResult(output=json.dumps({"action": action, "ok": True}))
