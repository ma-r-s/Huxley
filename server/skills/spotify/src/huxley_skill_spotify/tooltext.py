"""Tool descriptions, per language.

No example artists, genres or titles anywhere: the model used them as its
guess when it misheard (2026-10-12), so they stay abstract.

Books (T1.40): pause, resume, next and previous stay in `music_control`
and act on whatever is loaded (chapters when it's a book). A second set of
the same controls made the model pick the wrong one (T1.34, T1.37). The
book tools only do what music has no word for.

The shelf (T1.45): `find_books` offers only the persona's curated books and
the skill words the offer itself; the tool text says when to call it and
never names a kind of book, so the model has nothing to guess from.
"""

from __future__ import annotations

TEXT: dict[str, dict[str, str]] = {
    "es": {
        "play_music": (
            "Pone música de Spotify: una canción, un artista, un álbum o una lista. "
            "'Para', 'para la música', 'pausa' o 'detén' es detener, nunca poner: eso es "
            "`music_control` con 'pause', aunque no suene nada. "
            "Para un libro con título usa `play_book`; para pedir un tipo de libro, "
            "`find_books`. "
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
            "'<canción> <artista>'. Cualquier palabra además del artista es el nombre "
            "de la canción aunque suene rara o mal dicha (un título en inglés dicho "
            "en español: 'boya' por 'Voyager'): ponla tal como sonó junto al artista, "
            "nunca la quites para buscar solo al artista; Spotify la encuentra así. "
            "Escríbela como sonó en español, letra por letra, sin cambiarla por otra "
            "palabra ni 'corregirla'. "
            "Vacío si no dijo qué música."
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
            "la canción anterior, la de antes, atrás una canción, el capítulo anterior; "
            "volver a la canción anterior es 'previous', nunca 'resume'). 'Sigue' es seguir con la misma: 'resume', no 'next'. "
            "Un 'no, ...' que corrige lo que acabas de poner pide OTRA cosa: búscala "
            "con `play_music`, nunca 'resume'."
        ),
        "whats_playing": (
            "Dice qué está sonando en Spotify ('¿qué canción es esta?', '¿quién "
            "canta?', '¿qué libro es?', '¿en qué capítulo voy?', '¿cuánto falta?'); "
            "lo que está sonando ahora, no los libros que ha escuchado (eso es "
            "`find_books`). "
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
            "'Otros', 'otro' o 'más' después de una oferta nunca es elegir: es "
            "`find_books` con `more`. Un libro cualquiera ('pon un libro', 'ponme un "
            "libro', 'algo para leer') no es seguir: usa `find_books` sin argumentos. Si pide un "
            "tipo de libro sin título ('un libro de <tema>'), usa `find_books`. "
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
            "Ofrece audiolibros de la repisa: libros conocidos que se pueden escuchar "
            "aquí. Úsala para '¿qué libros tienes?', '¿qué me recomiendas?', 'quiero un "
            "libro de <tema>', 'pon algo de <tema>' para leer, 'algo como <libro>', '¿qué "
            "libros hay de <autor>?', '¿de qué trata <título>?', 'otros', '¿qué libros he "
            "escuchado?'. Sin argumentos dice qué tipos de libro hay. Llama UNA vez por "
            "pedido y después di exactamente lo que diga `then`; nunca agregues libros "
            "que no traiga el resultado. Si luego elige uno ('el segundo', 'ese' o su "
            "título), llama a `play_book` con ese título."
        ),
        "find_query": (
            "El tema, el tipo de libro, el autor o el título que dijo, con sus palabras y "
            "sin la frase entera. Vacío si no dijo ninguno."
        ),
        "find_like": (
            "Si pidió libros como uno que nombró ('algo como <libro>'), el título o el "
            "autor de ese libro."
        ),
        "find_more": "true si pide otros o más de lo mismo ('otros', '¿qué más tienes?').",
        "find_listened": "true si pregunta qué libros ha escuchado o empezado.",
        "book_opinion": (
            "Anota si al usuario le gustó o no un libro, para recomendarle mejor: 'me "
            "gustó', 'qué buen libro', 'me está gustando', 'no me gusta este libro', 'ese "
            "no me gustó'. Llámala cada vez que diga que un libro le gusta o no, aunque "
            "no lo pida como orden, y antes de contestar; un 'me gustó' o 'me encanta' "
            "solo, con un libro puesto o recién terminado, habla de ese libro. Sin "
            "`title`, el libro que está puesto o el último. "
            "Después di exactamente lo que diga `then`."
        ),
        "opinion_value": "'liked' si le gustó, 'disliked' si no.",
        "opinion_title": "El título si habla de otro libro; vacío para el que está puesto.",
    },
    "en": {
        "play_music": (
            "Play music from Spotify: a song, an artist, an album or a playlist. "
            "'Stop', 'stop the music', 'pause' are stopping, never playing: that's "
            "`music_control` with 'pause', even when nothing is playing. "
            "For a book by title use `play_book`; to ask for a kind of book, "
            "`find_books`. "
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
            "Any word besides the artist is the song's name even if it sounds odd or "
            "garbled (an English title said with another accent): keep it as it "
            "sounded, next to the artist; never drop it to search the artist alone. "
            "Spell it the way it sounded, letter by letter, never swapping it for "
            "another word or 'correcting' it. "
            "Spotify finds it that way. Empty if they didn't say which music."
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
            "(the previous one, the previous song, go back a song, the previous chapter; "
            "going back to the previous song is 'previous', never 'resume'). "
            "'Keep going' means the same song: 'resume', not 'next'. A 'no, ...' "
            "correcting what you just played asks for something ELSE: search with "
            "`play_music`, never 'resume'."
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
            "book', 'put my book on') the last book continues. 'Others', 'another' or "
            "'more' after an offer is never a pick: it's "
            "`find_books` with `more`. Any book ('put on a "
            "book', 'something to read') is not continuing: use `find_books` with "
            "no arguments. For a kind of book with "
            "no title ('a book about <subject>') use `find_books`. BEFORE calling, "
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
            "Offer audiobooks from the shelf: well-known books that can be listened to "
            "here. Use it for 'what books do you have?', 'what do you recommend?', 'I "
            "want a book about <subject>', 'something like <book>', 'what books are there "
            "by <author>?', 'what is <title> about?', 'others', 'which books have I "
            "listened to?'. Without arguments it says what kinds of book there are. Call "
            "it ONCE per request, then say exactly what `then` says; never add books the "
            "result didn't bring. If they then pick one ('the second', 'that one' or its "
            "title), call `play_book` with that title."
        ),
        "find_query": (
            "The subject, kind of book, author or title they said, in their words, not "
            "the whole sentence. Empty if they named none."
        ),
        "find_like": (
            "If they asked for books like one they named ('something like <book>'), that "
            "book's title or author."
        ),
        "find_more": "true if they ask for others or more of the same ('others', 'what else?').",
        "find_listened": "true if they ask which books they have listened to or started.",
        "book_opinion": (
            "Note whether the user liked a book, to recommend better: 'I liked it', 'what "
            "a good book', 'I'm enjoying it', 'I don't like this book', 'I didn't like "
            "that one'. Call it whenever they say they like a book or not, even if it "
            "isn't phrased as a request, and before answering; a bare 'I liked it' or "
            "'I love it' while a book is on or just ended is about that book. Without "
            "`title`, the book that is on or the last one. Then say exactly what `then` "
            "says."
        ),
        "opinion_value": "'liked' if they liked it, 'disliked' if not.",
        "opinion_title": "The title if they mean another book; empty for the one that is on.",
    },
    "fr": {
        "play_music": (
            "Joue de la musique Spotify : une chanson, un artiste, un album ou une "
            "playlist. 'Arrête', 'arrête la musique', 'pause' c'est arrêter, jamais "
            "mettre : c'est `music_control` avec 'pause', même si rien ne joue. "
            "Pour un livre par son titre, utilise `play_book` ; pour un genre "
            "de livre, `find_books`. AVANT l'appel, dis "
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
            "'<chanson> <artiste>'. Tout mot en plus de l'artiste est le nom de la "
            "chanson même s'il sonne bizarre ou mal dit (un titre anglais prononcé "
            "à la française) : garde-le tel qu'il a sonné, à côté de l'artiste ; ne "
            "l'enlève jamais pour chercher l'artiste seul. Spotify le trouve ainsi. "
            "Écris-le comme il a sonné, lettre par lettre, sans le remplacer par un "
            "autre mot ni le 'corriger'. "
            "Vide s'il n'a pas dit quelle musique."
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
            "'previous' (précédente, la chanson d'avant, le chapitre précédent ; revenir à "
            "la chanson précédente, c'est 'previous', jamais 'resume'). "
            "'Continue' veut dire la même chanson : 'resume', pas 'next'. Un 'non, ...' "
            "qui corrige ce que tu viens de mettre demande AUTRE chose : cherche avec "
            "`play_music`, jamais 'resume'."
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
            "livre', 'mets mon livre') le dernier livre reprend. 'D'autres', 'un autre' "
            "ou 'plus' après une offre n'est jamais un choix : "
            "c'est `find_books` avec `more`. Un livre quelconque "
            "('mets un livre', 'quelque chose à lire') n'est pas reprendre : "
            "utilise `find_books` sans argument. Pour un genre de livre "
            "sans titre ('un livre sur <sujet>'), utilise `find_books`. AVANT l'appel, dis "
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
            "Propose des livres audio de l'étagère : des livres connus qu'on peut écouter "
            "ici. Utilise-la pour 'quels livres tu as ?', 'qu'est-ce que tu me "
            "conseilles ?', 'je veux un livre sur <sujet>', 'quelque chose comme <livre>', "
            "'quels livres de <auteur> ?', 'de quoi parle <titre> ?', 'd'autres', 'quels "
            "livres j'ai écoutés ?'. Sans argument, elle dit quels genres de livres il y "
            "a. Appelle-la UNE fois par demande, puis dis exactement ce que dit `then` ; "
            "n'ajoute jamais de livres absents du résultat. S'il en choisit un ('le "
            "deuxième', 'celui-là' ou son titre), appelle `play_book` avec ce titre."
        ),
        "find_query": (
            "Le sujet, le genre de livre, l'auteur ou le titre qu'il a dit, avec ses mots, "
            "pas toute la phrase. Vide s'il n'en a nommé aucun."
        ),
        "find_like": (
            "S'il demande des livres comme un livre qu'il a nommé ('quelque chose comme "
            "<livre>'), le titre ou l'auteur de ce livre."
        ),
        "find_more": "true s'il demande d'autres livres ou plus du même ('d'autres', 'quoi d'autre ?').",
        "find_listened": "true s'il demande quels livres il a écoutés ou commencés.",
        "book_opinion": (
            "Note si un livre a plu à l'utilisateur, pour mieux le conseiller : 'il m'a "
            "plu', 'quel bon livre', 'ça me plaît', 'ce livre ne me plaît pas', 'celui-là "
            "ne m'a pas plu'. Appelle-la chaque fois qu'il dit qu'un livre lui plaît ou "
            "non, même sans le demander, et avant de répondre ; un 'il m'a plu' ou "
            "'j'adore' tout court, avec un livre en cours ou juste fini, parle de ce "
            "livre. Sans `title`, le livre en cours ou le dernier. "
            "Ensuite dis exactement ce que dit `then`."
        ),
        "opinion_value": "'liked' s'il a aimé, 'disliked' sinon.",
        "opinion_title": "Le titre s'il parle d'un autre livre ; vide pour celui en cours.",
    },
}
