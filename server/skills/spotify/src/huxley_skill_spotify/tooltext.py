"""Tool descriptions, per language.

No example artists, genres or titles anywhere: the model used them as its
guess when it misheard (2026-10-12), so they stay abstract.

Books (T1.40): pause, resume, next and previous stay in `music_control`
and act on whatever is loaded (chapters when it's a book). A second set of
the same controls made the model pick the wrong one (T1.34, T1.37). The
book tools only do what music has no word for.
"""

from __future__ import annotations

TEXT: dict[str, dict[str, str]] = {
    "es": {
        "play_music": (
            "Pone música de Spotify: una canción, un artista, un álbum o una lista. "
            "Para libros usa `play_book`. "
            "ANTES de llamar di algo breve como 'a ver, la busco'. Los nombres pueden "
            "estar en inglés o en cualquier idioma: pásalos tal como los oíste, sin "
            "traducirlos ni cambiarlos por otro parecido. Si dudas del nombre, llama "
            "igual con tu mejor suposición en vez de preguntar. Si el usuario te "
            "corrige ('no, ...'), no repitas la misma búsqueda: prueba otra "
            "interpretación o una de las `alternatives` del resultado anterior. Si "
            "pidió música sin decir cuál ('pon música'), llama sin `query`: sigue con "
            "la última música que sonó. Cuando el resultado diga qué va a sonar, díselo al "
            "usuario con esas palabras; nunca inventes el nombre de la canción ni del "
            "artista. Para emisoras de radio usa la radio, no esto."
        ),
        "play_query": (
            "Los nombres tal como los dijo el usuario, sin traducir. Si nombró una "
            "canción y su artista ('<canción> de <artista>'), pon los dos: "
            "'<canción> <artista>'. Vacío si no dijo qué música."
        ),
        "play_kind": (
            "'track' si nombró una canción (con o sin artista), 'artist' si pidió "
            "'algo de' un artista sin nombrar canción, 'album', 'playlist' para una "
            "lista o un estilo de música, 'any' si no está claro."
        ),
        "music_control": (
            "Controla lo que suena en Spotify, música o libro. action: 'pause' (pausa, "
            "para, silencio, apágala, basta; lo que suena se pausa solo mientras el "
            "usuario habla y vuelve solo después, así que para pararlo de verdad llama "
            "siempre a 'pause'), "
            "'resume' (sigue, sigue con la canción, continúa, vuelve a poner la "
            "música o el libro; 'ponlo' si lo último que sonaba era de Spotify), "
            "'next' solo si pide OTRA canción u otro capítulo (siguiente, otra, "
            "cambia la canción, pasa, el siguiente capítulo), 'previous' (la anterior, "
            "el capítulo anterior). 'Sigue' es seguir con la misma: 'resume', no 'next'."
        ),
        "whats_playing": (
            "Dice qué está sonando en Spotify ('¿qué canción es esta?', '¿quién "
            "canta?', '¿qué libro es?', '¿en qué capítulo voy?', '¿cuánto falta?'). "
            "Llámala SIEMPRE que pregunten, aunque creas saberlo: lo que suena cambia. "
            "No digas nada antes de llamarla. Después haz exactamente lo que diga "
            "`then`; lo que sonaba vuelve solo."
        ),
        "play_book": (
            "Pone un audiolibro de Spotify. Spotify recuerda dónde se quedó cada "
            "libro: pedirlo otra vez sigue donde iba. Con `title` (título, autor o "
            "los dos, tal como los dijo) pone ese libro; si acaba de oír una lista de "
            "libros y dice 'el segundo', 'el último' o 'ese', pasa el título de ese "
            "libro, contando en el orden en que los dijiste. Sin `title` ('sigue con "
            "el libro', 'pon mi libro', 'pon el libro') sigue con el último libro. "
            "ANTES de llamar di algo breve como 'a ver, lo busco'. Si el resultado "
            "trae `then`, haz exactamente lo que dice. Nunca inventes títulos, "
            "autores ni de qué trata un libro."
        ),
        "book_title": "Título y/o autor tal como los dijo el usuario. Vacío para seguir "
        "con el último libro.",
        "book_from_beginning": (
            "true solo si pidió empezar el libro desde el principio ('desde el "
            "principio', 'desde el inicio', 'empiézalo de nuevo'). Si pide empezar "
            "de nuevo el capítulo, usa `book_control` con 'restart_chapter'."
        ),
        "book_confirmed": (
            "true solo cuando el usuario ya contestó que sí quiere el libro entero "
            "desde el principio (después de que se lo preguntaste)."
        ),
        "book_control": (
            "Mueve el libro que está puesto. action: 'back' (regresa un poco, repite "
            "eso, atrás; `seconds`, por defecto 30), 'forward' (adelanta; `seconds`), "
            "'restart_chapter' (empieza el capítulo de nuevo), 'go_to_chapter' (ve al "
            "capítulo...; `chapter` con el número o el nombre que dijo), "
            "'go_back_to_saved_place' (vuelve a donde iba antes de empezar el libro "
            "desde el principio). Para pausar, seguir, siguiente y anterior capítulo "
            "usa `music_control`. ANTES de llamar di algo breve como 'listo'. Si el "
            "resultado trae `then`, haz exactamente lo que dice."
        ),
        "control_action": "Qué hacer con el libro.",
        "control_seconds": "Segundos para 'back' o 'forward' (por defecto 30).",
        "control_chapter": "Para 'go_to_chapter': el número o el nombre del capítulo, "
        "tal como lo dijo.",
        "find_books": (
            "Busca audiolibros en Spotify para recomendar o elegir: '¿qué libros me "
            "recomiendas?', '¿qué libros hay de <autor>?', '¿qué libros he "
            "escuchado?', '¿de qué trata?'. Sin `query` da los libros que ya se han "
            "escuchado aquí. Después haz exactamente lo que diga `then`."
        ),
        "find_query": "Autor, tema o título tal como lo dijo. Vacío para los libros ya "
        "escuchados.",
    },
    "en": {
        "play_music": (
            "Play music from Spotify: a song, an artist, an album or a playlist. "
            "For books use `play_book`. "
            "BEFORE calling, briefly say something like 'let me find it'. Names may "
            "be in any language: pass them as you heard them, without translating "
            "them or swapping in a similar name. If you're unsure of the name, call "
            "anyway with your best guess instead of asking. If the user corrects you "
            "('no, ...'), don't repeat the same search: try another reading or one "
            "of the `alternatives` from the previous result. If they asked for music "
            "without saying which ('play some music'), call without `query`: the "
            "last music continues. When the result says what will play, tell the "
            "user in those words; never invent the song or artist. For radio "
            "stations use the radio, not this."
        ),
        "play_query": (
            "The names as the user said them, untranslated. If they named a song "
            "and its artist ('<song> by <artist>'), include both: '<song> <artist>'. "
            "Empty if they didn't say which music."
        ),
        "play_kind": (
            "'track' if they named a song (with or without the artist), 'artist' for "
            "'something by' an artist with no song named, 'album', 'playlist' for a "
            "list or a style of music, 'any' if unclear."
        ),
        "music_control": (
            "Control what Spotify is playing, music or a book. action: 'pause' "
            "(pause, stop, quiet), 'resume' (continue, keep going, play again; "
            "'play it' if Spotify was the last thing playing), 'next' only for "
            "ANOTHER song or the next chapter (next, another one, skip), 'previous' "
            "(the previous one, the previous chapter). "
            "'Keep going' means the same song: 'resume', not 'next'."
        ),
        "whats_playing": (
            "Says what is playing on Spotify ('what song is this?', 'who sings "
            "this?', 'what book is this?', 'which chapter am I on?', 'how much is "
            "left?'). ALWAYS call it when asked, even if you think you know: it "
            "changes. Say nothing before calling it. Then do exactly what `then` "
            "says; what was playing comes back on its own."
        ),
        "play_book": (
            "Play a Spotify audiobook. Spotify remembers where each book was left: "
            "asking for it again continues it. With `title` (title, author or both, "
            "as said) it plays that book; if they just heard a list of books and say "
            "'the second', 'the last one' or 'that one', pass that book's title, "
            "counting in the order you read them. Without `title` ('continue the "
            "book', 'put my book on') the last book continues. BEFORE calling, "
            "briefly say something like 'let me find it'. If the result has `then`, "
            "do exactly what it says. Never invent titles, authors or what a book "
            "is about."
        ),
        "book_title": "Title and/or author as the user said them. Empty to continue the "
        "last book.",
        "book_from_beginning": (
            "true only if they asked to start the book from the beginning ('from the "
            "start', 'start it over'). To restart the chapter use `book_control` "
            "with 'restart_chapter'."
        ),
        "book_confirmed": (
            "true only once the user has said yes, the whole book from the beginning "
            "(after you asked)."
        ),
        "book_control": (
            "Move the book that is on. action: 'back' (go back a bit, repeat that; "
            "`seconds`, default 30), 'forward' (skip ahead; `seconds`), "
            "'restart_chapter', 'go_to_chapter' (`chapter`: the number or name they "
            "said), 'go_back_to_saved_place' (back to where it was before starting "
            "the book over). For pause, continue, next and previous chapter use "
            "`music_control`. BEFORE calling, briefly say something like 'okay'. If "
            "the result has `then`, do exactly what it says."
        ),
        "control_action": "What to do with the book.",
        "control_seconds": "Seconds for 'back' or 'forward' (default 30).",
        "control_chapter": "For 'go_to_chapter': the chapter's number or name, as said.",
        "find_books": (
            "Look for Spotify audiobooks to recommend or choose from: 'what books do "
            "you recommend?', 'what books are there by <author>?', 'which books have "
            "I listened to?', 'what is it about?'. Without `query` it gives the books "
            "already listened to here. Then do exactly what `then` says."
        ),
        "find_query": "Author, subject or title as said. Empty for books already listened to.",
    },
    "fr": {
        "play_music": (
            "Joue de la musique Spotify : une chanson, un artiste, un album ou une "
            "playlist. Pour les livres, utilise `play_book`. AVANT l'appel, dis "
            "brièvement 'je cherche'. Les noms peuvent "
            "être en anglais ou en toute autre langue : passe-les tels que tu les as "
            "entendus, sans les traduire ni les remplacer par un nom proche. Si tu "
            "doutes du nom, appelle quand même avec ta meilleure supposition au lieu "
            "de demander. Si l'utilisateur te corrige ('non, ...'), ne refais pas la "
            "même recherche : essaie une autre interprétation ou une des "
            "`alternatives` du résultat précédent. S'il demande de la musique sans "
            "dire laquelle ('mets de la musique'), appelle sans `query` : la dernière "
            "musique reprend. Annonce ce qui va jouer avec les mots du résultat ; "
            "n'invente jamais le titre ni l'artiste. Pour une station de radio, "
            "utilise la radio."
        ),
        "play_query": (
            "Les noms tels que l'utilisateur les a dits, sans traduire. S'il a nommé "
            "une chanson et son artiste ('<chanson> de <artiste>'), mets les deux : "
            "'<chanson> <artiste>'. Vide s'il n'a pas dit quelle musique."
        ),
        "play_kind": (
            "'track' s'il a nommé une chanson (avec ou sans artiste), 'artist' pour "
            "'quelque chose de' un artiste sans chanson précise, 'album', 'playlist' "
            "pour une liste ou un style de musique, 'any' sinon."
        ),
        "music_control": (
            "Contrôle ce que joue Spotify, musique ou livre. action : 'pause', "
            "'resume' (reprends, continue, remets-la), 'next' seulement pour UNE "
            "AUTRE chanson ou le chapitre suivant (suivante, une autre, change), "
            "'previous' (précédente, le chapitre précédent). "
            "'Continue' veut dire la même chanson : 'resume', pas 'next'."
        ),
        "whats_playing": (
            "Dit ce qui joue sur Spotify ('c'est quelle chanson ?', 'c'est quel "
            "livre ?', 'à quel chapitre on est ?', 'il reste combien ?'). Appelle-la "
            "TOUJOURS, même si tu crois savoir : ça change. Ne dis rien avant. "
            "Ensuite fais exactement ce que dit `then` ; ce qui jouait reprend tout "
            "seul."
        ),
        "play_book": (
            "Lance un livre audio Spotify. Spotify retient où en était chaque livre : "
            "le redemander reprend là où il en était. Avec `title` (titre, auteur ou "
            "les deux, tels que dits) il lance ce livre ; s'il vient d'entendre une "
            "liste et dit 'le deuxième', 'le dernier' ou 'celui-là', passe le titre "
            "de ce livre, dans l'ordre où tu les as lus. Sans `title` ('continue le "
            "livre', 'mets mon livre') le dernier livre reprend. AVANT l'appel, dis "
            "brièvement 'je cherche'. Si le résultat contient `then`, fais exactement "
            "ce qu'il dit. N'invente jamais de titres, d'auteurs ni de résumés."
        ),
        "book_title": "Titre et/ou auteur tels que dits. Vide pour reprendre le dernier livre.",
        "book_from_beginning": (
            "true seulement s'il demande le livre depuis le début ('depuis le "
            "début', 'recommence-le'). Pour recommencer le chapitre, utilise "
            "`book_control` avec 'restart_chapter'."
        ),
        "book_confirmed": (
            "true seulement quand l'utilisateur a répondu oui, tout le livre depuis "
            "le début (après que tu l'as demandé)."
        ),
        "book_control": (
            "Déplace le livre en cours. action : 'back' (reviens un peu, répète ça ; "
            "`seconds`, 30 par défaut), 'forward' (avance ; `seconds`), "
            "'restart_chapter', 'go_to_chapter' (`chapter` : le numéro ou le nom "
            "dit), 'go_back_to_saved_place' (retour où il en était avant de "
            "recommencer le livre). Pour pause, reprise, chapitre suivant et "
            "précédent, utilise `music_control`. AVANT l'appel, dis brièvement "
            "'d'accord'. Si le résultat contient `then`, fais exactement ce qu'il dit."
        ),
        "control_action": "Quoi faire avec le livre.",
        "control_seconds": "Secondes pour 'back' ou 'forward' (30 par défaut).",
        "control_chapter": "Pour 'go_to_chapter' : le numéro ou le nom du chapitre, tel que dit.",
        "find_books": (
            "Cherche des livres audio Spotify à recommander ou à choisir : 'quels "
            "livres tu me conseilles ?', 'quels livres de <auteur> ?', 'quels livres "
            "j'ai écoutés ?', 'de quoi ça parle ?'. Sans `query`, donne les livres "
            "déjà écoutés ici. Ensuite fais exactement ce que dit `then`."
        ),
        "find_query": "Auteur, sujet ou titre tel que dit. Vide pour les livres déjà écoutés.",
    },
}
