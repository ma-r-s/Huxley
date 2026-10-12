"""What the skill says about books, word for word (T1.40).

Exact lines, not instructions: told to "tell the user Spotify wouldn't play
the book", the model said the book was playing (2026-10-12). A narration
states the fact and gives the line (`notice`); a tool result gives the line
to say (`then`). One plain cause each, an alternative where there is one,
and no "tú" or "usted": the persona owns the form of address.
"""

from __future__ import annotations

_NOTICE = {
    "es": "[Aviso del sistema, no lo dijo el usuario: {fact}] Di en voz alta exactamente "
    "«{line}» No llames a ninguna herramienta.",
    "en": '[System notice, not said by the user: {fact}] Say out loud exactly "{line}" '
    "Do not call any tool.",
    "fr": "[Avis du système, pas dit par l'utilisateur : {fact}] Dis à voix haute "
    "exactement « {line} » N'appelle aucun outil.",
}
_THEN = {
    "es": "Di en voz alta exactamente «{line}» No llames a ninguna herramienta.",
    "en": 'Say out loud exactly "{line}" Do not call any tool.',
    "fr": "Dis à voix haute exactement « {line} » N'appelle aucun outil.",
}

# Narrations: (fact, line). They follow a tool result that said the book
# was starting, so the fact comes first.
_SPOKEN: dict[str, dict[str, tuple[str, str]]] = {
    "es": {
        "book_must_buy": (
            "el libro NO está sonando, Spotify no lo deja poner con este plan.",
            "Ese libro hay que comprarlo aparte; no lo puedo poner. ¿Busco otro libro?",
        ),
        "book_no_hours": (
            "el libro NO está sonando, ya se usaron las horas de libros de este mes.",
            "Este mes ya no quedan horas para escuchar libros. ¿Pongo la radio?",
        ),
        "book_failed": (
            "el libro NO está sonando.",
            "No pude poner el libro. ¿Pongo la radio?",
        ),
        "book_finished": ("el libro terminó.", "Se terminó {title}. ¿Busco otro libro?"),
        "book_cut": ("el libro dejó de sonar.", "Se cortó el libro. ¿Lo vuelvo a poner?"),
        "book_moved": (
            "el libro ya no suena aquí, lo pusieron en otro aparato.",
            "El libro dejó de sonar aquí porque lo pusieron en otro aparato.",
        ),
        "book_offline": (
            "el libro NO está sonando, Spotify no responde.",
            "Spotify no responde ahora. ¿Pongo la radio?",
        ),
        "book_nav_failed": (
            "no se pudo llegar a ese capítulo; el libro sigue donde estaba.",
            "No pude llegar a ese capítulo; el libro sigue donde iba.",
        ),
        "book_patience": (
            "el libro quedó en pausa.",
            "El libro quedó en pausa; para seguir, basta con pedirlo.",
        ),
    },
    "en": {
        "book_must_buy": (
            "the book is NOT playing, Spotify won't play it on this plan.",
            "That book has to be bought separately; I can't play it. Shall I find another book?",
        ),
        "book_no_hours": (
            "the book is NOT playing, this month's book hours are used up.",
            "There are no book hours left this month. Shall I put the radio on?",
        ),
        "book_failed": (
            "the book is NOT playing.",
            "I couldn't start the book. Shall I put the radio on?",
        ),
        "book_finished": ("the book ended.", "{title} has finished. Shall I find another book?"),
        "book_cut": ("the book stopped.", "The book cut out. Shall I put it back on?"),
        "book_moved": (
            "the book is no longer playing here, someone put it on another device.",
            "The book stopped here because someone put it on another device.",
        ),
        "book_offline": (
            "the book is NOT playing, Spotify isn't answering.",
            "Spotify isn't answering right now. Shall I put the radio on?",
        ),
        "book_nav_failed": (
            "the chapter couldn't be reached; the book stays where it was.",
            "I couldn't get to that chapter; the book carries on where it was.",
        ),
        "book_patience": (
            "the book stayed paused.",
            "The book is paused; to carry on, just ask.",
        ),
    },
    "fr": {
        "book_must_buy": (
            "le livre NE joue PAS, Spotify ne le permet pas avec cet abonnement.",
            "Ce livre doit être acheté à part ; je ne peux pas le lancer. J'en cherche un autre ?",
        ),
        "book_no_hours": (
            "le livre NE joue PAS, les heures de livres du mois sont épuisées.",
            "Il ne reste plus d'heures de livres ce mois-ci. Je mets la radio ?",
        ),
        "book_failed": (
            "le livre NE joue PAS.",
            "Je n'ai pas pu lancer le livre. Je mets la radio ?",
        ),
        "book_finished": ("le livre est terminé.", "{title} est terminé. J'en cherche un autre ?"),
        "book_cut": ("le livre s'est arrêté.", "Le livre s'est coupé. Je le remets ?"),
        "book_moved": (
            "le livre ne joue plus ici, on l'a mis sur un autre appareil.",
            "Le livre s'est arrêté ici parce qu'on l'a mis sur un autre appareil.",
        ),
        "book_offline": (
            "le livre NE joue PAS, Spotify ne répond pas.",
            "Spotify ne répond pas pour l'instant. Je mets la radio ?",
        ),
        "book_nav_failed": (
            "le chapitre n'a pas pu être atteint ; le livre reste où il était.",
            "Je n'ai pas pu aller à ce chapitre ; le livre continue où il était.",
        ),
        "book_patience": (
            "le livre est resté en pause.",
            "Le livre est en pause ; pour continuer, il suffit de le demander.",
        ),
    },
}

# Tool results: the line to say.
_SAY: dict[str, dict[str, str]] = {
    "es": {
        "book_not_found": "No encontré ese libro en Spotify. ¿Busco otro?",
        "first_chapter": "Ya es el primer capítulo.",
        "last_chapter": "Ya es el último capítulo.",
        "no_book": "No hay ningún libro puesto. ¿Qué libro busco?",
        "nothing_started": "Todavía no hay ningún libro empezado. ¿Qué libro busco?",
        "finished_replay": "Ese libro ya se terminó. ¿Lo pongo desde el principio?",
        "ask_whole_book": "¿Desde el principio del libro, o solo de este capítulo?",
        "ask_which": "¿Es {title}, de {author}?",
        "no_such_chapter": "Ese libro no tiene un capítulo {chapter}.",
        "no_saved_place": "No tengo guardado otro lugar de ese libro.",
        "player_offline": "Los libros de Spotify no están disponibles ahora. ¿Pongo la radio?",
        "player_not_paired": "Spotify todavía no está conectado aquí. ¿Pongo la radio?",
        "search_failed": "No pude buscar en Spotify ahora. ¿Pongo la radio?",
        "search_not_configured": "Todavía no puedo buscar libros en Spotify. ¿Pongo la radio?",
        "books_none": "Todavía no se ha escuchado ningún libro aquí. ¿De qué autor o de "
        "qué tema busco?",
        "status": "Es {title}, de {author}. Va en {chapter}: faltan {chapter_left} para "
        "terminar el capítulo y {book_left} para terminar el libro.",
    },
    "en": {
        "book_not_found": "I didn't find that book on Spotify. Shall I look for another?",
        "first_chapter": "This is already the first chapter.",
        "last_chapter": "This is already the last chapter.",
        "no_book": "No book is on. Which book shall I look for?",
        "nothing_started": "No book has been started yet. Which book shall I look for?",
        "finished_replay": "That book has already finished. Shall I start it from the beginning?",
        "ask_whole_book": "From the beginning of the book, or just of this chapter?",
        "ask_which": "Is it {title}, by {author}?",
        "no_such_chapter": "That book has no chapter {chapter}.",
        "no_saved_place": "I have no other place saved for that book.",
        "player_offline": "Spotify books aren't available right now. Shall I put the radio on?",
        "player_not_paired": "Spotify isn't connected here yet. Shall I put the radio on?",
        "search_failed": "I couldn't search Spotify just now. Shall I put the radio on?",
        "search_not_configured": "I can't search Spotify for books yet. Shall I put the radio on?",
        "books_none": "No book has been listened to here yet. Which author or subject "
        "shall I look for?",
        "status": "It's {title}, by {author}. It's on {chapter}: {chapter_left} left in the "
        "chapter and {book_left} left in the book.",
    },
    "fr": {
        "book_not_found": "Je n'ai pas trouvé ce livre sur Spotify. J'en cherche un autre ?",
        "first_chapter": "C'est déjà le premier chapitre.",
        "last_chapter": "C'est déjà le dernier chapitre.",
        "no_book": "Aucun livre n'est lancé. Quel livre je cherche ?",
        "nothing_started": "Aucun livre n'a encore été commencé. Quel livre je cherche ?",
        "finished_replay": "Ce livre est déjà terminé. Je le reprends depuis le début ?",
        "ask_whole_book": "Depuis le début du livre, ou seulement de ce chapitre ?",
        "ask_which": "C'est {title}, de {author} ?",
        "no_such_chapter": "Ce livre n'a pas de chapitre {chapter}.",
        "no_saved_place": "Je n'ai pas d'autre endroit enregistré pour ce livre.",
        "player_offline": "Les livres Spotify ne sont pas disponibles pour l'instant. "
        "Je mets la radio ?",
        "player_not_paired": "Spotify n'est pas encore connecté ici. Je mets la radio ?",
        "search_failed": "Je n'ai pas pu chercher sur Spotify. Je mets la radio ?",
        "search_not_configured": "Je ne peux pas encore chercher de livres sur Spotify. "
        "Je mets la radio ?",
        "books_none": "Aucun livre n'a encore été écouté ici. Quel auteur ou quel sujet "
        "je cherche ?",
        "status": "C'est {title}, de {author}. On en est à {chapter} : il reste "
        "{chapter_left} pour finir le chapitre et {book_left} pour finir le livre.",
    },
}

_DURATION = {
    "es": {
        "min": "{n} minutos",
        "min1": "un minuto",
        "hour": "unas {n} horas",
        "hour1": "una hora",
    },
    "en": {
        "min": "{n} minutes",
        "min1": "one minute",
        "hour": "about {n} hours",
        "hour1": "an hour",
    },
    "fr": {
        "min": "{n} minutes",
        "min1": "une minute",
        "hour": "environ {n} heures",
        "hour1": "une heure",
    },
}


def _lang(lang: str) -> str:
    return lang if lang in _SAY else "en"


def notice(lang: str, key: str, **fields: str) -> str:
    """An injected narration: the fact, then the exact line."""
    fact, line = _SPOKEN[_lang(lang)][key]
    return _NOTICE[_lang(lang)].format(fact=fact, line=line.format(**fields))


def line(lang: str, key: str, **fields: str) -> str:
    return _SAY[_lang(lang)][key].format(**fields)


def then(lang: str, key: str, **fields: str) -> str:
    """For a tool result: say exactly this line."""
    return _THEN[_lang(lang)].format(line=line(lang, key, **fields))


def duration(lang: str, ms: int) -> str:
    """Rounded the way a person says it: minutes under an hour, then hours."""
    words = _DURATION[_lang(lang)]
    minutes = max(1, round(ms / 60_000))
    if minutes < 60:
        return words["min1"] if minutes == 1 else words["min"].format(n=minutes)
    hours = round(minutes / 60)
    return words["hour1"] if hours == 1 else words["hour"].format(n=hours)


_LISTING = {
    "es": "Léele al usuario estos libros, numerados, con el título y el autor de cada uno "
    "(y cuánto dura si viene `length`), y pregúntale cuál quiere. Si pregunta de qué "
    "trata uno, usa su `about`, sin inventar nada. No llames a ninguna herramienta.",
    "en": "Read these books to the user, numbered, with each one's title and author (and "
    "its length if `length` is there), and ask which one they want. If they ask what one "
    "is about, use its `about`, inventing nothing. Do not call any tool.",
    "fr": "Lis ces livres à l'utilisateur, numérotés, avec le titre et l'auteur de chacun "
    "(et sa durée si `length` est là), et demande lequel il veut. S'il demande de quoi "
    "parle l'un d'eux, utilise son `about`, sans rien inventer. N'appelle aucun outil.",
}


def listing(lang: str) -> str:
    """For a list of books: read them out, numbered, and ask which one."""
    return _LISTING[_lang(lang)]
