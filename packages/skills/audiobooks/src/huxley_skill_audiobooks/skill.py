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

import asyncio
import json
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from huxley_sdk import (
    AudioStream,
    CancelMedia,
    Catalog,
    Hit,
    InjectTurn,
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

# Per-language default prompts sent to the LLM when a book ends naturally
# (not interrupted). Personas can still override any single language via
# `skills.audiobooks.on_complete_prompt` (default language) or
# `skills.audiobooks.i18n.<lang>.on_complete_prompt` (per-language) in
# persona.yaml. Unknown languages fall back to English.
_DEFAULT_ON_COMPLETE_PROMPTS: dict[str, str] = {
    "es": (
        "El libro ha llegado a su fin. Felicita al usuario por haber "
        "terminado el libro y pregúntale si quiere que busque otro."
    ),
    "en": (
        "The book has just finished. Congratulate the user on finishing "
        "it and ask if they'd like you to find another one."
    ),
    "fr": (
        "Le livre vient de se terminer. Félicite l'utilisateur d'avoir "
        "terminé le livre et demande-lui s'il veut que tu lui en trouves "
        "un autre."
    ),
}


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

# Patience window applied to the audiobook's CONTENT Activity. When a
# higher-priority channel (DIALOG/COMMS) preempts the book, the Activity
# stays parked in BACKGROUND/MUST_PAUSE for this long; if the preemptor
# releases before the timer fires, FM auto-promotes the book back to
# FOREGROUND and the pump respawns from the saved position. If the
# timer fires first, the `on_patience_expired` callback narrates an
# acknowledgement and the Activity is evicted.
#
# 30 minutes: covers nearly every realistic call length without
# abandoning a forgotten book indefinitely. Revisit if real usage
# surfaces longer-call patterns.
BOOK_PATIENCE = timedelta(minutes=30)

# Per-language prompts narrated when a long preemptor exhausts patience
# and the book's CONTENT Activity is about to be evicted. The skill
# fires one of these via `inject_turn` from `on_patience_expired`, so
# the user hears "hey, I had to let your book go" rather than
# experiencing silent state loss.
_PATIENCE_EXPIRED_PROMPTS: dict[str, str] = {
    "es": (
        "Dile al usuario con tono cálido y breve, en español, que pausaste "
        "su libro porque la interrupción fue larga; invítalo a decir 'sigue "
        "con el libro' cuando quiera retomarlo."
    ),
    "en": (
        "Tell the user warmly and briefly, in English, that you paused their "
        "book because the interruption ran long; invite them to say 'keep "
        "going with the book' whenever they want to resume."
    ),
    "fr": (
        "Dis à l'utilisateur d'un ton chaleureux et bref, en français, que "
        "tu as mis son livre en pause parce que l'interruption a été longue; "
        "invite-le à dire 'reprends le livre' quand il veut le reprendre."
    ),
}


# --- Per-language string catalog -------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "es": {
        "unknown_author": "Desconocido",
        "library_header": "Biblioteca de audiolibros disponibles",
        "book_line": '- "{title}" por {author}',
        "library_empty": "La biblioteca está vacía.",
        "list_preview_msg": "Éstos son los libros que tengo.",
        "search_no_match": ("No encontré nada con esas palabras. ¿Quiere que le diga qué tengo?"),
        "search_match_msg": "Encontré estos libros.",
        "not_found": "No encuentro '{query}'. ¿Quiere que le diga qué libros tengo?",
        "probe_failed": "No pude abrir ese libro. Déjeme intentarlo otra vez.",
        "no_resume": "No tiene ningún libro a medias. ¿Busco algo?",
        "no_active_book": "No hay ningún libro activo. ¿Quiere que busque uno?",
        "missing_book": "No encuentro ese libro en la biblioteca.",
        "no_in_progress": "No tiene ningún libro empezado. ¿Quiere que le busque uno?",
        "nothing_to_resume": "No hay ningún libro para reanudar.",
        "no_active_to_seek": "No hay ningún libro activo para mover.",
        "cant_resolve_seek": "No encuentro ese libro.",
        "action_unclear": "No entendí la acción.",
        "need_speed": "Indícame la velocidad. Por ejemplo, 0.85 para más lento.",
        "position_start": "el inicio",
        "unit_hour_one": "hora",
        "unit_hour_many": "horas",
        "unit_minute_one": "minuto",
        "unit_minute_many": "minutos",
        "unit_second_one": "segundo",
        "unit_second_many": "segundos",
        "and_join": " y ",
    },
    "en": {
        "unknown_author": "Unknown",
        "library_header": "Audiobooks available in the library",
        "book_line": '- "{title}" by {author}',
        "library_empty": "The library is empty.",
        "list_preview_msg": "These are the books I have.",
        "search_no_match": (
            "I couldn't find anything matching those words. Want me to list what I have?"
        ),
        "search_match_msg": "I found these books.",
        "not_found": "I can't find '{query}'. Want me to list what I have?",
        "probe_failed": "I couldn't open that book. Let me try again.",
        "no_resume": "You don't have any book in progress. Want me to find one?",
        "no_active_book": "There's no active book. Would you like me to find one?",
        "missing_book": "I can't find that book in the library.",
        "no_in_progress": "You haven't started any book. Want me to find one?",
        "nothing_to_resume": "There's no book to resume.",
        "no_active_to_seek": "No active book to move.",
        "cant_resolve_seek": "I can't find that book.",
        "action_unclear": "I didn't catch the action.",
        "need_speed": "Tell me the speed. For example, 0.85 for a bit slower.",
        "position_start": "the beginning",
        "unit_hour_one": "hour",
        "unit_hour_many": "hours",
        "unit_minute_one": "minute",
        "unit_minute_many": "minutes",
        "unit_second_one": "second",
        "unit_second_many": "seconds",
        "and_join": " and ",
    },
    "fr": {
        "unknown_author": "Inconnu",
        "library_header": "Livres audio disponibles dans la bibliothèque",
        "book_line": "- « {title} » de {author}",
        "library_empty": "La bibliothèque est vide.",
        "list_preview_msg": "Voici les livres que j'ai.",
        "search_no_match": (
            "Je n'ai rien trouvé avec ces mots. Veux-tu que je te dise ce que j'ai ?"
        ),
        "search_match_msg": "J'ai trouvé ces livres.",
        "not_found": "Je ne trouve pas « {query} ». Veux-tu que je te dise ce que j'ai ?",
        "probe_failed": "Je n'ai pas pu ouvrir ce livre. Laisse-moi réessayer.",
        "no_resume": "Tu n'as aucun livre en cours. Veux-tu que j'en cherche un ?",
        "no_active_book": "Aucun livre actif. Veux-tu que je t'en trouve un ?",
        "missing_book": "Je ne trouve pas ce livre dans la bibliothèque.",
        "no_in_progress": "Tu n'as commencé aucun livre. Veux-tu que j'en cherche un ?",
        "nothing_to_resume": "Aucun livre à reprendre.",
        "no_active_to_seek": "Aucun livre actif à déplacer.",
        "cant_resolve_seek": "Je ne trouve pas ce livre.",
        "action_unclear": "Je n'ai pas compris l'action.",
        "need_speed": "Dis-moi la vitesse. Par exemple, 0.85 pour un peu plus lent.",
        "position_start": "le début",
        "unit_hour_one": "heure",
        "unit_hour_many": "heures",
        "unit_minute_one": "minute",
        "unit_minute_many": "minutes",
        "unit_second_one": "seconde",
        "unit_second_many": "secondes",
        "and_join": " et ",
    },
}


# --- Per-language tool descriptions ----------------------------------------

_TOOL_DESC: dict[str, dict[str, str]] = {
    "es": {
        "search_audiobooks": (
            "Busca audiolibros en la biblioteca local del usuario. "
            "Devuelve una lista de libros que coinciden con la búsqueda."
        ),
        "search_query_param": "Texto de búsqueda (título, autor, o parte del nombre)",
        "play_audiobook": (
            "Reproduce un audiolibro. Puedes pasarle el ID exacto (de "
            "search_audiobooks), o simplemente el título o el autor — la "
            "skill hace coincidencia aproximada. Reanuda desde la última "
            "posición guardada a menos que se especifique lo contrario. "
            "Si el usuario dice 'desde el principio', 'desde el inicio', "
            "'empieza de nuevo', 'vuelve al inicio', 'empieza de cero' o "
            "algo parecido, pasa `from_beginning: true`. "
            "Antes de reproducir, acusa recibo brevemente al usuario "
            "(por ejemplo: 'Ahí le pongo {título}.'). Nunca empieces el "
            "libro en silencio."
        ),
        "play_book_id": (
            "ID, título o autor del libro. La skill acepta coincidencias "
            "aproximadas — no necesitas pasar el ID exacto."
        ),
        "play_from_beginning": (
            "Pasa `true` cuando el usuario pida explícitamente empezar de "
            "cero ('desde el principio', 'desde el inicio', 'empieza de "
            "nuevo', 'vuelve al inicio'). Por defecto `false` — reanuda "
            "donde se quedó."
        ),
        "resume_last": (
            "Reanuda el audiolibro que el usuario escuchó por última vez, "
            "desde donde lo dejó. Úsalo cuando el usuario diga 'sigue con "
            "el libro', 'el libro de anoche', 'continúa el libro' y "
            "similares, sin mencionar un título específico. Antes de "
            "reanudar, acusa recibo brevemente ('Sigo con {título} donde "
            "lo dejó.')."
        ),
        "audiobook_control": (
            "Controla la reproducción del audiolibro actual: pausar, "
            "reanudar, retroceder, adelantar, detener, o cambiar la "
            "velocidad. Antes de llamar esta herramienta, acusa recibo "
            "brevemente al usuario (por ejemplo: 'Listo, retrocedo 30 "
            "segundos.'). Nunca ejecutes la acción en silencio. Para "
            "retroceder/adelantar, el valor por defecto es 30 segundos. "
            "Para `set_speed`, usa `speed` entre 0.5 (mitad de velocidad) "
            "y 2.0 (doble); 1.0 es la velocidad normal. Sugerencias: "
            "0.85 para 'un poco más lento', 0.7 para 'mucho más lento', "
            "1.15 para 'un poco más rápido'."
        ),
        "control_action": "Acción a realizar",
        "control_seconds": "Segundos para retroceder/adelantar (default: 30)",
        "control_speed": (
            "Velocidad de reproducción para `set_speed`. Rango 0.5 a 2.0; 1.0 es normal."
        ),
        "get_progress": (
            "Devuelve el progreso del libro que se está escuchando (o el "
            "último reproducido): posición actual, duración total y tiempo "
            "restante. Úsalo cuando el usuario pregunte '¿cuánto llevo?', "
            "'¿cuánto me queda?', '¿en qué parte voy?' y similares."
        ),
        "list_in_progress": (
            "Lista todos los audiolibros que tienen una posición guardada — "
            "es decir, los que el usuario ha empezado y no ha terminado. "
            "Úsalo cuando el usuario pregunte '¿qué libros tengo empezados?', "
            "'¿cuáles tengo a medias?', '¿qué estaba escuchando?' y similares."
        ),
    },
    "en": {
        "search_audiobooks": (
            "Search the user's local audiobook library. "
            "Returns a list of books matching the query."
        ),
        "search_query_param": "Search text (title, author, or part of the name)",
        "play_audiobook": (
            "Play an audiobook. Pass the exact id (from search_audiobooks) "
            "or just the title or author — the skill fuzzy-matches. "
            "Resumes from the last saved position unless `from_beginning` "
            "is set. If the user says 'from the start', 'start over', "
            "'begin again' or similar, pass `from_beginning: true`. "
            "Before playback, briefly acknowledge the user (e.g. 'Playing "
            "{title} for you.'). Never start the book silently."
        ),
        "play_book_id": (
            "Book id, title, or author. The skill accepts fuzzy matches — "
            "you don't need the exact id."
        ),
        "play_from_beginning": (
            "Pass `true` when the user explicitly asks to start from the "
            "beginning ('from the start', 'start over', 'begin again'). "
            "Default `false` — resumes where they left off."
        ),
        "resume_last": (
            "Resume the audiobook the user was last listening to, from "
            "where they left off. Use when the user says 'keep going with "
            "the book', 'continue my book' and similar without naming a "
            "title. Before resuming, briefly acknowledge (e.g. 'Resuming "
            "{title} where you left off.')."
        ),
        "audiobook_control": (
            "Control current audiobook playback: pause, resume, rewind, "
            "forward, stop, or change speed. Before calling this tool, "
            "briefly acknowledge the user (e.g. 'Going back 30 seconds.'). "
            "Never perform the action silently. Rewind/forward defaults to "
            "30 seconds. For `set_speed`, use `speed` between 0.5 (half) "
            "and 2.0 (double); 1.0 is normal speed. Suggestions: 0.85 for "
            "'a bit slower', 0.7 for 'much slower', 1.15 for 'a bit faster'."
        ),
        "control_action": "Action to perform",
        "control_seconds": "Seconds to rewind/forward (default: 30)",
        "control_speed": "Playback speed for `set_speed`. Range 0.5 to 2.0; 1.0 is normal.",
        "get_progress": (
            "Return progress on the book currently playing (or the last "
            "one played): current position, total duration, remaining "
            "time. Use when the user asks 'how far am I?', 'how much is "
            "left?', 'where am I in the book?' and similar."
        ),
        "list_in_progress": (
            "List every audiobook with a saved position — books the user "
            "has started but not finished. Use when the user asks 'which "
            "books have I started?', 'what was I listening to?' and similar."
        ),
    },
    "fr": {
        "search_audiobooks": (
            "Recherche des livres audio dans la bibliothèque locale de "
            "l'utilisateur. Renvoie une liste de livres correspondant à "
            "la recherche."
        ),
        "search_query_param": "Texte de recherche (titre, auteur, ou partie du nom)",
        "play_audiobook": (
            "Lit un livre audio. Passe l'identifiant exact (de "
            "search_audiobooks) ou simplement le titre ou l'auteur — la "
            "compétence fait une correspondance approximative. Reprend "
            "depuis la dernière position sauvegardée sauf indication "
            "contraire. Si l'utilisateur dit 'depuis le début', "
            "'recommence', 'reprends au début', passe "
            "`from_beginning: true`. Avant de lancer, accuse brièvement "
            "réception (par exemple : 'Je te mets {title}.'). Ne commence "
            "jamais le livre en silence."
        ),
        "play_book_id": (
            "Identifiant, titre ou auteur du livre. La compétence accepte "
            "les correspondances approximatives — pas besoin de l'id exact."
        ),
        "play_from_beginning": (
            "Passe `true` quand l'utilisateur demande explicitement de "
            "recommencer ('depuis le début', 'recommence', 'reprends au "
            "début'). Par défaut `false` — reprend où il s'est arrêté."
        ),
        "resume_last": (
            "Reprend le livre audio que l'utilisateur écoutait en dernier, "
            "là où il s'est arrêté. À utiliser quand l'utilisateur dit "
            "'reprends le livre', 'continue le livre' et similaires sans "
            "mentionner de titre. Avant de reprendre, accuse brièvement "
            "réception ('Je reprends {title} où tu t'es arrêté.')."
        ),
        "audiobook_control": (
            "Contrôle la lecture du livre audio en cours : pause, reprise, "
            "retour en arrière, avance, arrêt, ou changement de vitesse. "
            "Avant d'appeler cet outil, accuse brièvement réception (par "
            "exemple : 'Je reviens 30 secondes en arrière.'). N'exécute "
            "jamais l'action en silence. Pour reculer/avancer, la valeur "
            "par défaut est 30 secondes. Pour `set_speed`, utilise `speed` "
            "entre 0.5 (moitié) et 2.0 (double) ; 1.0 est la vitesse "
            "normale. Suggestions : 0.85 pour 'un peu plus lent', 0.7 pour "
            "'beaucoup plus lent', 1.15 pour 'un peu plus rapide'."
        ),
        "control_action": "Action à effectuer",
        "control_seconds": "Secondes pour reculer/avancer (défaut : 30)",
        "control_speed": (
            "Vitesse de lecture pour `set_speed`. Plage 0.5 à 2.0 ; 1.0 est normale."
        ),
        "get_progress": (
            "Renvoie la progression du livre en cours d'écoute (ou du "
            "dernier écouté) : position actuelle, durée totale et temps "
            "restant. À utiliser quand l'utilisateur demande 'où j'en "
            "suis ?', 'combien il me reste ?' et similaires."
        ),
        "list_in_progress": (
            "Liste tous les livres audio avec une position sauvegardée — "
            "ceux que l'utilisateur a commencés sans les finir. À utiliser "
            "quand l'utilisateur demande 'quels livres j'ai commencés ?', "
            "'qu'est-ce que j'écoutais ?' et similaires."
        ),
    },
}


def _lang_bucket(language: str) -> str:
    code = (language or "en").lower()
    for key in ("es", "en", "fr"):
        if code.startswith(key):
            return key
    return "en"


def _clamp_speed(value: float) -> float:
    return max(MIN_SPEED, min(MAX_SPEED, value))


def _fmt_duration(seconds: float, language: str = "es") -> str:
    """Format a duration in seconds to a natural language string.

    Uses the `_STRINGS[lang]` unit words + the "and" connector so a
    single implementation works for ES/EN/FR (and trivially extends).
    """
    bucket = _lang_bucket(language)
    s_table = _STRINGS[bucket]
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)

    def plural(count: int, one: str, many: str) -> str:
        return f"{count} {one if count == 1 else many}"

    joiner = s_table["and_join"]
    if h:
        return (
            plural(h, s_table["unit_hour_one"], s_table["unit_hour_many"])
            + joiner
            + plural(m, s_table["unit_minute_one"], s_table["unit_minute_many"])
        )
    if m:
        return (
            plural(m, s_table["unit_minute_one"], s_table["unit_minute_many"])
            + joiner
            + plural(s, s_table["unit_second_one"], s_table["unit_second_many"])
        )
    return plural(s, s_table["unit_second_one"], s_table["unit_second_many"])


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
        # Held for the `on_patience_expired` callback so a backgrounded-
        # then-evicted book can narrate "pausé tu libro por la llamada
        # larga" via inject_turn. None in test fixtures and pre-setup().
        self._inject_turn: InjectTurn | None = None
        # Live handles to deferred inject tasks fired from
        # `on_patience_expired`. We must retain a strong reference so
        # Python's GC doesn't collect the task mid-run; the
        # done-callback discards the entry once the task completes.
        self._patience_tasks: set[asyncio.Task[None]] = set()
        # Personal-content catalog (T1.1). Built at setup() from the library
        # scan; all fuzzy match + prompt-context generation goes through it.
        self._catalog: Catalog | None = None
        # Sound palette: {name: raw_pcm_bytes}. Loaded at setup() from sounds_path.
        # Empty dict = no earcons; skill runs silently.
        self._sounds: dict[str, bytes] = {}
        # Trailing silence injected after book_end earcon to buffer model latency.
        self._silence_ms: int = 500
        # Active UI language — drives tool descriptions, localized
        # default prompts, and `_fmt_duration` output. Set by setup()
        # from the initial context and refreshed on every reconfigure().
        self._language: str = "en"
        # Prompt the LLM narrates when a book ends naturally. Persona
        # override wins; otherwise falls back to the per-language default.
        # Refreshed inside reconfigure() whenever the session language
        # or merged skill config changes.
        self._on_complete_prompt: str = _DEFAULT_ON_COMPLETE_PROMPTS["en"]
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

    def _td(self) -> dict[str, str]:
        """Per-session tool description pack for the active language."""
        return _TOOL_DESC.get(_lang_bucket(self._language), _TOOL_DESC["en"])

    def _t(self, key: str, **fmt: str) -> str:
        """Look up a localized skill string for the active language."""
        bucket = _lang_bucket(self._language)
        table = _STRINGS.get(bucket) or _STRINGS["en"]
        template = table.get(key) or _STRINGS["en"].get(key) or key
        return template.format(**fmt) if fmt else template

    @property
    def tools(self) -> list[ToolDefinition]:
        td = self._td()
        return [
            ToolDefinition(
                name="search_audiobooks",
                description=td["search_audiobooks"],
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": td["search_query_param"],
                        }
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="play_audiobook",
                description=td["play_audiobook"],
                parameters={
                    "type": "object",
                    "properties": {
                        "book_id": {
                            "type": "string",
                            "description": td["play_book_id"],
                        },
                        "from_beginning": {
                            "type": "boolean",
                            "description": td["play_from_beginning"],
                        },
                    },
                    "required": ["book_id"],
                },
            ),
            ToolDefinition(
                name="resume_last",
                description=td["resume_last"],
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="audiobook_control",
                description=td["audiobook_control"],
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
                            "description": td["control_action"],
                        },
                        "seconds": {
                            "type": "number",
                            "description": td["control_seconds"],
                        },
                        "speed": {
                            "type": "number",
                            "description": td["control_speed"],
                        },
                    },
                    "required": ["action"],
                },
            ),
            ToolDefinition(
                name="get_progress",
                description=td["get_progress"],
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="list_in_progress",
                description=td["list_in_progress"],
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
        self._inject_turn = ctx.inject_turn
        # Seed language BEFORE scanning the library — `_scan_library`
        # uses `self._t("unknown_author")` for root-level books without
        # a parent directory, and reads the active language via `self._t`.
        self._language = ctx.language or "en"
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
        self._on_complete_prompt = self._resolve_complete_prompt(cfg)

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

    def _resolve_complete_prompt(self, cfg: dict[str, Any]) -> str:
        """Pick the on_complete_prompt the LLM will narrate when a book ends.

        Preference order:
        1. `skills.audiobooks.on_complete_prompt` in persona config
           (persona authors override by writing the prompt out), also
           picks up the per-language merge from
           `skills.audiobooks.i18n.<lang>.on_complete_prompt`.
        2. Built-in per-language default for the active language.
        3. English built-in default.
        """
        override = cfg.get("on_complete_prompt")
        if isinstance(override, str) and override.strip():
            return override
        bucket = _lang_bucket(self._language)
        return _DEFAULT_ON_COMPLETE_PROMPTS.get(bucket, _DEFAULT_ON_COMPLETE_PROMPTS["en"])

    async def reconfigure(self, ctx: SkillContext) -> None:
        """Refresh language + per-language prompts on every session."""
        self._language = ctx.language or self._language
        self._on_complete_prompt = self._resolve_complete_prompt(ctx.config)
        await ctx.logger.ainfo(
            "audiobooks.reconfigure",
            language=self._language,
            complete_prompt_source=(
                "persona" if ctx.config.get("on_complete_prompt") else "builtin"
            ),
        )

    async def teardown(self) -> None:
        """No teardown state — the running factory saves its own position on cancel."""

    def prompt_context(self) -> str:
        """Text injected into the session prompt so the LLM knows what's available.

        Capped at 50 books via Catalog (T1.1). The header + line format
        follow the active session language; book titles and authors
        themselves are untranslated (they're proper content, not framework
        copy).
        """
        if self._catalog is None or len(self._catalog) == 0:
            return ""
        line_tmpl = self._t("book_line")
        return self._catalog.as_prompt_lines(
            limit=50,
            header=self._t("library_header"),
            line=lambda h: line_tmpl.format(
                title=h.fields.get("title", ""),
                author=h.fields.get("author", self._t("unknown_author")),
            ),
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
                # Author unknown in the filesystem; surface a localized
                # label so the LLM and any UI read it naturally. Using
                # `_t` means the label flips with the session language.
                author = self._t("unknown_author")
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

    async def _on_book_patience_expired(self) -> None:
        """Called by the framework when the book's BACKGROUND patience
        window elapses before a FOREGROUND return (e.g. a COMMS call
        parked this stream and lasted longer than `BOOK_PATIENCE`).

        Fires BEFORE the terminal NONE/MUST_STOP eviction, from INSIDE
        the FocusManager's actor task processing `_handle_patience_expired`.
        That means we must NOT `await ctx.inject_turn(...)` here — the
        inject path calls `fm.acquire() + fm.wait_drained()`, and
        `wait_drained` waits for the `PatienceExpired` event's
        `task_done()`, which only fires after THIS callback returns.
        Awaiting inline would deadlock the FM actor. Same pattern the
        Telegram skill documents in `_on_claim_end` — schedule the
        inject_turn via `create_task` and return immediately.

        Silent no-op with a structured log if `setup()` hasn't completed
        yet or teardown has already scrubbed our callbacks; the missing
        narration is itself observable via the log, not just gone.
        """
        if self._inject_turn is None or self._logger is None:
            # Structured log so the missing narration is diagnosable
            # from the server log alone (observability rule: every
            # decision branch leaves a trace).
            if self._logger is not None:
                await self._logger.ainfo(
                    "audiobooks.patience_expired_skipped",
                    reason="inject_turn_unavailable",
                )
            return
        await self._logger.ainfo("audiobooks.patience_expired")
        inject = self._inject_turn

        async def _deferred_inject() -> None:
            assert self._logger is not None
            try:
                prompt = _PATIENCE_EXPIRED_PROMPTS.get(
                    _lang_bucket(self._language),
                    _PATIENCE_EXPIRED_PROMPTS["en"],
                )
                await inject(prompt, dedup_key="book_patience_expired")
            except Exception:
                await self._logger.aexception("audiobooks.patience_expired_inject_failed")

        # Fire-and-return: lets the FM actor complete
        # `_handle_patience_expired` → `task_done` → release the
        # queue.join so the inject's own acquire can land. Retain a
        # strong ref in `_patience_tasks` so GC can't collect the task
        # mid-run; done_callback scrubs on completion.
        task = asyncio.create_task(_deferred_inject(), name="audiobook_patience_inject")
        self._patience_tasks.add(task)
        task.add_done_callback(self._patience_tasks.discard)

    async def _search(self, query: str) -> ToolResult:
        """Search the catalog by fuzzy matching against title and author.

        Refactored onto Catalog (T1.1). Behavior preserved:
        - Empty catalog → localized "empty library" message
        - Query < 2 chars → return first 20 books in insertion order
        - Otherwise: catalog.search with `_SEARCH_THRESHOLD` filter,
          top 5 results
        """
        catalog = self._catalog_req
        total = len(catalog)
        if total == 0:
            return ToolResult(
                output=json.dumps(
                    {"results": [], "message": self._t("library_empty")},
                    ensure_ascii=False,
                )
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
                        "message": self._t("list_preview_msg"),
                    },
                    ensure_ascii=False,
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
                        "message": self._t("search_no_match"),
                    },
                    ensure_ascii=False,
                )
            )

        return ToolResult(
            output=json.dumps(
                {
                    "results": [_hit_summary(h) for h in results],
                    "count": len(results),
                    "message": self._t("search_match_msg"),
                },
                ensure_ascii=False,
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
        speed: float = DEFAULT_SPEED,
    ) -> Callable[[], AsyncIterator[bytes]]:
        """Build a playback factory for the coordinator's terminal barrier.

        The returned callable can be invoked **multiple times** over a
        stream's lifetime — `ContentStreamObserver` re-calls it whenever
        FocusManager re-promotes the Activity to FOREGROUND after a
        MUST_PAUSE (e.g., a call paused the book and ended). Each call
        reads the CURRENT saved position from storage via
        `_get_position(book_id)` so the resumed stream picks up from
        where the previous pump's `finally` block saved it — NOT from
        a position captured at build time.

        Callers must write the desired initial position to storage
        BEFORE returning this factory inside an `AudioStream` side
        effect. See `_play`, the speed-change path, and the seek path.

        `speed` is the tempo applied via ffmpeg's atempo filter (see
        AudiobookPlayer.stream). Position math accounts for it: at
        speed=0.5, one wall-clock second = 0.5 book seconds, so
        `book_advance = wall_elapsed * speed`.
        """
        player = self._player_req
        logger = self._logger_req
        set_position = self._set_position
        get_position = self._get_position
        skill = self  # for live-position tracking via get_progress
        book_start_pcm = self._sounds.get("book_start", b"")
        book_end_pcm = self._sounds.get("book_end", b"")
        # Note: trailing silence buffer is owned by the coordinator (via the
        # AudioStream.completion_silence_ms field) so it can be sent AFTER the
        # request_response, overlapping with model first-token latency.

        async def stream() -> AsyncIterator[bytes]:
            # Read the CURRENT position from storage at every invocation.
            # First call: equals whatever the tool handler wrote before
            # returning this factory. Subsequent calls (post-cancel-and-
            # re-FOREGROUND): equals the value the prior pump's `finally`
            # saved, so resume picks up from there.
            start_position = await get_position(book_id)
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
                # Interrupted (cancel or error) → save current position so a
                # subsequent `stream()` invocation (e.g., FG return after a
                # call paused us) can resume from here.
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
                        "message": self._t("not_found", query=book_id),
                    },
                    ensure_ascii=False,
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
                        "message": self._t("probe_failed"),
                    },
                    ensure_ascii=False,
                )
            )

        await self._storage_req.set_setting(LAST_BOOK_KEY, resolved_id)
        speed = await self._get_speed()
        # Write the initial position to storage BEFORE returning the
        # factory — the factory's `stream()` reads the current saved
        # position at each invocation, so the first pump starts here
        # and later FG-return pumps pick up from the `finally`-saved
        # advance.
        await self._set_position(resolved_id, start_position)
        await self._logger_req.ainfo(
            "audiobooks.factory_built",
            book_id=resolved_id,
            start_position=start_position,
            speed=speed,
        )

        factory = self._build_factory(resolved_id, book["path"], speed=speed)

        resuming = start_position > 0
        return ToolResult(
            output=json.dumps(
                {
                    "playing": True,
                    "title": book["title"],
                    "author": book["author"],
                    "position_seconds": start_position,
                    "position_label": (
                        _fmt_duration(start_position, self._language)
                        if resuming
                        else self._t("position_start")
                    ),
                    "resuming": resuming,
                },
                ensure_ascii=False,
            ),
            side_effect=AudioStream(
                factory=factory,
                on_complete_prompt=self._on_complete_prompt,
                completion_silence_ms=self._silence_ms,
                label=book["title"],
                preroll_ms=len(self._sounds.get("book_start", b"")) * 1000 // BYTES_PER_SECOND,
                patience=BOOK_PATIENCE,
                on_patience_expired=self._on_book_patience_expired,
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
                        "message": self._t("no_resume"),
                    },
                    ensure_ascii=False,
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
                    output=json.dumps({"message": self._t("no_active_book")}, ensure_ascii=False)
                )
            current_pos = await self._get_position(book_id)
            is_live = False

        book = await self._resolve_book(book_id)
        if book is None:
            return ToolResult(
                output=json.dumps({"message": self._t("missing_book")}, ensure_ascii=False)
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
            "position_label": _fmt_duration(current_pos, self._language),
            "playing": is_live,
        }
        if total_duration:
            remaining = max(0.0, total_duration - current_pos)
            result["total_seconds"] = round(total_duration, 1)
            result["remaining_seconds"] = round(remaining, 1)
            result["remaining_label"] = _fmt_duration(remaining, self._language)
            result["percent"] = min(100, int(current_pos / total_duration * 100))

        return ToolResult(output=json.dumps(result, ensure_ascii=False))

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
                        "position_label": _fmt_duration(pos, self._language),
                    }
                )

        if not in_progress:
            return ToolResult(
                output=json.dumps({"message": self._t("no_in_progress")}, ensure_ascii=False)
            )

        return ToolResult(
            output=json.dumps(
                {
                    "count": len(in_progress),
                    "books": in_progress,
                },
                ensure_ascii=False,
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
                                "message": self._t("nothing_to_resume"),
                            },
                            ensure_ascii=False,
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
                                "message": self._t("no_active_to_seek"),
                            },
                            ensure_ascii=False,
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
                                "message": self._t("cant_resolve_seek"),
                            },
                            ensure_ascii=False,
                        )
                    )
                speed = await self._get_speed()
                # Persist the seek target before building the factory so
                # the factory's first stream() invocation reads the
                # right position. Outgoing stream's finally-block will
                # overwrite with its own final_pos on cancel, but the
                # coordinator cancels the old stream before starting
                # the new one via CancelMedia-then-AudioStream.
                await self._set_position(book_id, new_pos)
                factory = self._build_factory(book_id, book["path"], speed=speed)
                return ToolResult(
                    output=json.dumps(
                        {
                            "playing": True,
                            "title": book["title"],
                            "author": book["author"],
                            "position_seconds": new_pos,
                            "position_label": _fmt_duration(new_pos, self._language),
                        },
                        ensure_ascii=False,
                    ),
                    side_effect=AudioStream(
                        factory=factory,
                        on_complete_prompt=self._on_complete_prompt,
                        completion_silence_ms=self._silence_ms,
                        label=book["title"],
                        preroll_ms=len(self._sounds.get("book_start", b""))
                        * 1000
                        // BYTES_PER_SECOND,
                        patience=BOOK_PATIENCE,
                        on_patience_expired=self._on_book_patience_expired,
                    ),
                )

            case "set_speed":
                return await self._set_speed(speed)

            case _:
                return ToolResult(
                    output=json.dumps(
                        {"ok": False, "message": self._t("action_unclear")},
                        ensure_ascii=False,
                    )
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
                        "message": self._t("need_speed"),
                    },
                    ensure_ascii=False,
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
        factory = self._build_factory(book_id, book["path"], speed=new_speed)
        return ToolResult(
            output=json.dumps(
                {
                    "ok": True,
                    "speed": new_speed,
                    "playing": True,
                    "title": book["title"],
                    "position_seconds": round(live_pos, 1),
                    "position_label": _fmt_duration(live_pos, self._language),
                },
                ensure_ascii=False,
            ),
            side_effect=AudioStream(
                factory=factory,
                on_complete_prompt=self._on_complete_prompt,
                completion_silence_ms=self._silence_ms,
                label=book["title"],
                preroll_ms=len(self._sounds.get("book_start", b"")) * 1000 // BYTES_PER_SECOND,
                patience=BOOK_PATIENCE,
                on_patience_expired=self._on_book_patience_expired,
            ),
        )
