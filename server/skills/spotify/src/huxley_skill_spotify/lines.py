"""What the skill says about books, word for word (T1.40).

Exact lines, not instructions: told to "tell the user Spotify wouldn't play
the book", the model said the book was playing (2026-10-12). A narration
states the fact and gives the line (`notice`); a tool result gives the line
to say (`then`). One plain cause each, an alternative where there is one,
and no "tú" or "usted": the persona owns the form of address.
"""

from __future__ import annotations

import re

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
        "book_unavailable": (
            "el libro NO está sonando, Spotify no lo deja escuchar.",
            "Ese libro no está disponible para escuchar. ¿Busco otro libro?",
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
        "book_unavailable": (
            "the book is NOT playing, Spotify won't let it be heard.",
            "That book isn't available to listen to. Shall I find another book?",
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
        "book_unavailable": (
            "le livre NE joue PAS, Spotify ne permet pas de l'écouter.",
            "Ce livre n'est pas disponible à l'écoute. J'en cherche un autre ?",
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
        "book_not_found_by": "No encontré {title}, de {author}, en Spotify. ¿Busco otro?",
        "book_not_found_title": "No encontré {title} en Spotify. ¿Busco otro?",
        "book_other_by": "No encontré ese libro en Spotify. De {author} está {other}. ¿Lo pongo?",
        "first_chapter": "Ya es el primer capítulo.",
        "last_chapter": "Ya es el último capítulo.",
        "no_book": "No hay ningún libro puesto. ¿Qué libro busco?",
        "nothing_started": "Todavía no hay ningún libro empezado. ¿Qué libro busco?",
        "finished_replay": "Ese libro ya se terminó. ¿Lo pongo desde el principio?",
        "ask_whole_book": "¿Desde el principio del libro, o solo de este capítulo?",
        "cant_restart": "Ahora no puedo volver al principio del libro. Sigue donde ibas.",
        "ask_which": "¿Es {title}, de {author}?",
        "no_such_chapter": "Ese libro no tiene un capítulo {chapter}.",
        "no_saved_place": "No tengo guardado otro lugar de ese libro.",
        "player_offline": "Los libros de Spotify no están disponibles ahora. ¿Pongo la radio?",
        "player_not_paired": "Spotify todavía no está conectado aquí. ¿Pongo la radio?",
        "search_failed": "No pude buscar en Spotify ahora. ¿Pongo la radio?",
        "search_not_configured": "Todavía no puedo buscar libros en Spotify. ¿Pongo la radio?",
        "books_none": "Todavía no se ha escuchado ningún libro aquí. ¿De qué autor o de "
        "qué tema busco?",
        "none_available": "No encontré libros de eso que se puedan escuchar. ¿Busco otro "
        "autor o tema?",
        "no_hours": "Este mes ya no quedan horas para escuchar libros. ¿Pongo la radio?",
        "status": "Es {title}, de {author}. Va en {chapter}: faltan {chapter_left} para "
        "terminar el capítulo y {book_left} para terminar el libro.",
        "status_plain": "Es {title}{by}.{at}",
        "status_by": ", de {author}",
        "status_at": " Va en {chapter}.",
    },
    "en": {
        "book_not_found": "I didn't find that book on Spotify. Shall I look for another?",
        "book_not_found_by": "I didn't find {title}, by {author}, on Spotify. Shall I look for another?",
        "book_not_found_title": "I didn't find {title} on Spotify. Shall I look for another?",
        "book_other_by": "I didn't find that book on Spotify. By {author} there's {other}. Shall I put it on?",
        "first_chapter": "This is already the first chapter.",
        "last_chapter": "This is already the last chapter.",
        "no_book": "No book is on. Which book shall I look for?",
        "nothing_started": "No book has been started yet. Which book shall I look for?",
        "finished_replay": "That book has already finished. Shall I start it from the beginning?",
        "ask_whole_book": "From the beginning of the book, or just of this chapter?",
        "cant_restart": "I can't go back to the start of the book right now. It goes on where you were.",
        "ask_which": "Is it {title}, by {author}?",
        "no_such_chapter": "That book has no chapter {chapter}.",
        "no_saved_place": "I have no other place saved for that book.",
        "player_offline": "Spotify books aren't available right now. Shall I put the radio on?",
        "player_not_paired": "Spotify isn't connected here yet. Shall I put the radio on?",
        "search_failed": "I couldn't search Spotify just now. Shall I put the radio on?",
        "search_not_configured": "I can't search Spotify for books yet. Shall I put the radio on?",
        "books_none": "No book has been listened to here yet. Which author or subject "
        "shall I look for?",
        "none_available": "I found no books on that which can be listened to. Shall I try "
        "another author or subject?",
        "no_hours": "There are no book hours left this month. Shall I put the radio on?",
        "status": "It's {title}, by {author}. It's on {chapter}: {chapter_left} left in the "
        "chapter and {book_left} left in the book.",
        "status_plain": "It's {title}{by}.{at}",
        "status_by": ", by {author}",
        "status_at": " It's on {chapter}.",
    },
    "fr": {
        "book_not_found": "Je n'ai pas trouvé ce livre sur Spotify. J'en cherche un autre ?",
        "book_not_found_by": "Je n'ai pas trouvé {title}, de {author}, sur Spotify. J'en cherche un autre ?",
        "book_not_found_title": "Je n'ai pas trouvé {title} sur Spotify. J'en cherche un autre ?",
        "book_other_by": "Je n'ai pas trouvé ce livre sur Spotify. De {author}, il y a {other}. Je le mets ?",
        "first_chapter": "C'est déjà le premier chapitre.",
        "last_chapter": "C'est déjà le dernier chapitre.",
        "no_book": "Aucun livre n'est lancé. Quel livre je cherche ?",
        "nothing_started": "Aucun livre n'a encore été commencé. Quel livre je cherche ?",
        "finished_replay": "Ce livre est déjà terminé. Je le reprends depuis le début ?",
        "ask_whole_book": "Depuis le début du livre, ou seulement de ce chapitre ?",
        "cant_restart": "Je ne peux pas revenir au début du livre pour l'instant. Il reprend où tu en étais.",
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
        "none_available": "Je n'ai trouvé aucun livre là-dessus qu'on puisse écouter. Je "
        "cherche un autre auteur ou sujet ?",
        "no_hours": "Il ne reste plus d'heures de livres ce mois-ci. Je mets la radio ?",
        "status": "C'est {title}, de {author}. On en est à {chapter} : il reste "
        "{chapter_left} pour finir le chapitre et {book_left} pour finir le livre.",
        "status_plain": "C'est {title}{by}.{at}",
        "status_by": ", de {author}",
        "status_at": " On en est à {chapter}.",
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


def book_status_plain(lang: str, title: str, author: str | None, chapter: str) -> str:
    """The book on when its chapter list isn't known: "Es X, de Y. Va en
    el capítulo 2." (author and chapter only when known)."""
    words = _SAY[_lang(lang)]
    by = words["status_by"].format(author=author) if author else ""
    at = words["status_at"].format(chapter=chapter_name(lang, chapter)) if chapter else ""
    return then(lang, "status_plain", title=title, by=by, at=at)


def then(lang: str, key: str, **fields: str) -> str:
    """For a tool result: say exactly this line."""
    return _THEN[_lang(lang)].format(line=line(lang, key, **fields))


_TRACK = re.compile(r"^(?:track|pista)\s*(\d+)$", re.IGNORECASE)
_CHAPTER_WORD = {"es": "el capítulo {n}", "en": "chapter {n}", "fr": "le chapitre {n}"}


def chapter_name(lang: str, name: str) -> str:
    """How to say a chapter: some books name them "Track 2" (heard as
    "Va en Track 2", 2026-10-12); those become "el capítulo 2"."""
    m = _TRACK.match(name.strip())
    return _CHAPTER_WORD[_lang(lang)].format(n=m.group(1)) if m else name


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
    "(y cuánto dura si viene `length`), y pregúntale cuál quiere. Todos se pueden "
    "escuchar aquí. Si pregunta de qué trata uno, usa su `about`, sin inventar nada. No "
    "busques otra vez a menos que él lo pida. No llames a ninguna herramienta.",
    "en": "Read these books to the user, numbered, with each one's title and author (and "
    "its length if `length` is there), and ask which one they want. All of them can be "
    "listened to here. If they ask what one is about, use its `about`, inventing nothing. "
    "Don't search again unless they ask. Do not call any tool.",
    "fr": "Lis ces livres à l'utilisateur, numérotés, avec le titre et l'auteur de chacun "
    "(et sa durée si `length` est là), et demande lequel il veut. Tous peuvent être "
    "écoutés ici. S'il demande de quoi parle l'un d'eux, utilise son `about`, sans rien "
    "inventer. Ne cherche pas à nouveau sauf s'il le demande. N'appelle aucun outil.",
}
# Without a query: the books already started here. They are not "what there
# is": Mario heard his one started book read as "the books available".
_LISTING_STARTED = {
    "es": "Estos son los libros que ya se empezaron aquí, no todos los que hay. Dile eso, "
    "léeselos numerados con título y autor, y pregúntale si quiere seguir con uno o de "
    "qué autor o qué tipo de libro le busco otros. No llames a ninguna herramienta.",
    "en": "These are the books already started here, not everything there is. Say so, "
    "read them numbered with title and author, and ask whether they want to carry on "
    "with one, or which author or kind of book to look for. Do not call any tool.",
    "fr": "Ce sont les livres déjà commencés ici, pas tout ce qu'il y a. Dis-le, lis-les "
    "numérotés avec titre et auteur, et demande s'il veut continuer l'un d'eux ou quel "
    "auteur ou quel genre de livre chercher. N'appelle aucun outil.",
}


def listing(lang: str, *, started: bool = False) -> str:
    """For a list of books: read them out, numbered, and ask which one."""
    return (_LISTING_STARTED if started else _LISTING)[_lang(lang)]


# The shelf (T1.45): offers the skill words itself, so the model reads
# exactly the books it was given and nothing it remembers.
_SHELF: dict[str, dict[str, str]] = {
    "es": {
        "head": "De {topic} tengo {n}.",
        "head_pick": "Te recomiendo {n}.",
        "head_like": "Parecidos a {topic} tengo {n}.",
        "head_more": "Tengo {n} más de {topic}.",
        "head_more_like": "Tengo {n} más parecidos a {topic}.",
        "head_found": "De {topic} encontré {n}.",
        # More of the subject left to offer: never a count that reads as
        # the total ("De Julio Verne tengo tres", then a fourth; 2026-10-14).
        "head_some": "De {topic} tengo varios; te digo {n}.",
        "head_more_some": "Te digo {n} más de {topic}.",
        "head_like_some": "Parecidos a {topic} tengo varios; te digo {n}.",
        "head_more_like_some": "Te digo {n} más parecidos a {topic}.",
        "item": "{ordinal}, {title}, de {author}: {pitch}",
        "item_bare": "{ordinal}, {title}, de {author}.",
        "one": "{title}, de {author}: {pitch}",
        "one_bare": "{title}, de {author}.",
        "ask_many": "¿Cuál pongo, o busco otros?",
        "ask_one": "¿Lo pongo, o busco otros?",
        "nothing": "En la repisa no tengo libros de eso. ¿Te recomiendo otros?",
        "not_found": "No lo encontré en Spotify. ¿Te recomiendo otros?",
        "no_more": "Ya no tengo más de {topic}. ¿Te recomiendo otros?",
        "liked": "Anotado que {title} gustó. Buscaré más libros así.",
        "disliked": "Anotado que {title} no gustó; no lo vuelvo a ofrecer.",
        "opinion_which": "No sé de qué libro. ¿Cuál fue?",
    },
    "en": {
        "head": "For {topic} I have {n}.",
        "head_pick": "Here are {n} I'd suggest.",
        "head_like": "Like {topic}, I have {n}.",
        "head_more": "I have {n} more for {topic}.",
        "head_more_like": "I have {n} more like {topic}.",
        "head_found": "By {topic} I found {n}.",
        "head_some": "For {topic} I have several; here are {n}.",
        "head_more_some": "Here are {n} more for {topic}.",
        "head_like_some": "Like {topic}, I have several; here are {n}.",
        "head_more_like_some": "Here are {n} more like {topic}.",
        "item": "{ordinal}, {title}, by {author}: {pitch}",
        "item_bare": "{ordinal}, {title}, by {author}.",
        "one": "{title}, by {author}: {pitch}",
        "one_bare": "{title}, by {author}.",
        "ask_many": "Which one shall I play, or shall I look for others?",
        "ask_one": "Shall I play it, or look for others?",
        "nothing": "I have no books on that on the shelf. Shall I suggest others?",
        "not_found": "I didn't find it on Spotify. Shall I suggest others?",
        "no_more": "I have no more for {topic}. Shall I suggest others?",
        "liked": "Noted: {title} was liked. I'll look for more books like it.",
        "disliked": "Noted: {title} wasn't liked; I won't offer it again.",
        "opinion_which": "I don't know which book. Which one was it?",
    },
    "fr": {
        "head": "En {topic}, j'en ai {n}.",
        "head_pick": "Je t'en propose {n}.",
        "head_like": "Dans le style de {topic}, j'en ai {n}.",
        "head_more": "J'en ai {n} de plus en {topic}.",
        "head_more_like": "J'en ai {n} de plus dans le style de {topic}.",
        "head_found": "De {topic}, j'en ai trouvé {n}.",
        "head_some": "En {topic}, j'en ai plusieurs ; en voici {n}.",
        "head_more_some": "En voici {n} de plus en {topic}.",
        "head_like_some": "Dans le style de {topic}, j'en ai plusieurs ; en voici {n}.",
        "head_more_like_some": "En voici {n} de plus dans le style de {topic}.",
        "item": "{ordinal}, {title}, de {author} : {pitch}",
        "item_bare": "{ordinal}, {title}, de {author}.",
        "one": "{title}, de {author} : {pitch}",
        "one_bare": "{title}, de {author}.",
        "ask_many": "Lequel je mets, ou j'en cherche d'autres ?",
        "ask_one": "Je le mets, ou j'en cherche d'autres ?",
        "nothing": "Je n'ai pas de livres de ce genre sur l'étagère. Je t'en propose d'autres ?",
        "not_found": "Je ne l'ai pas trouvé sur Spotify. Je t'en propose d'autres ?",
        "no_more": "Je n'en ai plus en {topic}. Je t'en propose d'autres ?",
        "liked": "Noté : {title} a plu. Je chercherai d'autres livres comme ça.",
        "disliked": "Noté : {title} n'a pas plu ; je ne le proposerai plus.",
        "opinion_which": "Je ne sais pas de quel livre. Lequel c'était ?",
    },
}
_COUNT = {
    "es": ("uno", "dos", "tres"),
    "en": ("one", "two", "three"),
    "fr": ("un", "deux", "trois"),
}
_ORDINAL = {
    "es": ("Uno", "Dos", "Tres"),
    "en": ("One", "Two", "Three"),
    "fr": ("Un", "Deux", "Trois"),
}


def shelf_line(lang: str, key: str, **fields: str) -> str:
    return _SHELF[_lang(lang)][key].format(**fields)


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if not text or text[-1] in ".!?" else f"{text}."


def offer(
    lang: str,
    head: str,
    topic: str,
    books: list[tuple[str, str, str]],
    *,
    more_left: bool = False,
) -> str:
    """The spoken offer: "De misterio tengo tres. Uno, X, de Y: <pitch>.
    ... ¿Cuál pongo, o busco otros?" `books` are (title, author, pitch),
    at most three; `head` is one of the "head*" keys, or "" for a single
    book named on its own. `more_left`: the subject has more books than
    these, so the head gives no count of them all."""
    lang = _lang(lang)
    words = _SHELF[lang]
    parts: list[str] = []
    if head and more_left and f"{head}_some" in words:
        head = f"{head}_some"
    if head:
        parts.append(words[head].format(topic=topic, n=_COUNT[lang][len(books) - 1]))
    for i, (title, author, pitch) in enumerate(books):
        fields = {"title": title, "author": author, "pitch": _sentence(pitch)}
        if len(books) == 1:
            parts.append(words["one" if pitch else "one_bare"].format(**fields))
        else:
            fields["ordinal"] = _ORDINAL[lang][i]
            parts.append(words["item" if pitch else "item_bare"].format(**fields))
    parts.append(words["ask_one" if len(books) == 1 else "ask_many"])
    return " ".join(parts)


def say(lang: str, text: str) -> str:
    """For a tool result: say exactly this text."""
    return _THEN[_lang(lang)].format(line=text)
