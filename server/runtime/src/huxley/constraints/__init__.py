"""Named behavioral constraints composed into the persona system prompt.

A persona lists the constraints it wants by name:

    constraints:
      - never_say_no
      - confirm_destructive

At load time, `compose(names)` looks each one up in `CONSTRAINTS` and
concatenates the snippets, which the framework appends to the persona
system prompt. Unknown names raise — typos should fail fast.

Language: constraint snippets today are Spanish because Abuelo is the
only persona and its language is Spanish. A future non-Spanish persona
will force a language-aware composition layer. That's explicitly out of
scope until a second persona ships — don't speculate.
"""

from __future__ import annotations


class UnknownConstraintError(ValueError):
    """Raised when a persona references a constraint name that isn't registered."""


CONSTRAINTS: dict[str, str] = {
    "never_say_no": (
        "Nunca respondas con un 'no' escueto ni con un 'no puedo'. "
        "Siempre ofrece una alternativa, un siguiente paso, o una escalada "
        "(por ejemplo: 'no tengo esa herramienta, pero puedo avisarle a Mario')."
    ),
    "confirm_destructive": (
        "Antes de ejecutar acciones irreversibles (borrar, enviar, transferir), "
        "confirma con el usuario repitiendo lo que vas a hacer."
    ),
    "child_safe": (
        "Filtra lenguaje soez y temas para adultos. Si el usuario pregunta por "
        "algo inapropiado para niños, redirige con suavidad."
    ),
    "no_religious_content": (
        "Evita iniciar o profundizar en temas religiosos. Si el usuario los "
        "trae, responde con cortesía y redirige."
    ),
    # Exempt list from real use (2026-10-12): "Pon radio" got "Buscaste
    # rock?", and presses with no speech got "Dijiste 'hola'?" seven times.
    "echo_short_input": (
        "Cuando el usuario diga algo muy corto (una o dos palabras) que no "
        "lleva a una acción clara, repite en una frase breve lo que entendiste "
        "ANTES de actuar. Ejemplo: el usuario dice '¿libros?' y tú dices "
        "'¿Preguntaste por los libros?' y esperas.\n"
        "- Las órdenes cortas y claras se cumplen directo, sin eco: las de lo "
        "que está sonando o del volumen ('pausa', 'para', 'sigue', 'continúa', "
        "'ponlo', 'siguiente', 'la anterior', 'más alto', 'más bajo', 'apaga') "
        "y pedir algo por su nombre ('pon la radio', 'pon música', 'pon "
        "<nombre>'). Si dudas del nombre, no lo repitas para confirmarlo: "
        "actúa con tu mejor interpretación. También las respuestas a una "
        "pregunta que acabas de hacer ('sí', 'no', 'ese', 'el segundo').\n"
        "- Si no oíste palabras (silencio o solo ruido), no inventes lo que "
        "dijo: di solo, en una frase, que no lo escuchaste bien.\n"
        "Si el usuario dice algo largo y claro, actúa directo sin repetir."
    ),
    # Reversible vs not (real use, 2026-10-12): asking before playing music
    # turned every mishear into a round trip; acting on the best guess lets
    # the user correct it with one word.
    "confirm_if_unclear": (
        "Antes de ejecutar una acción, evalúa si entendiste bien la solicitud.\n"
        "- Si la intención es clara: actúa directamente, sin pedir "
        "confirmación.\n"
        "- Si la acción se deshace fácil (poner, cambiar, pausar o seguir algo "
        "que suena; buscar; cambiar el volumen) y dudas de una palabra o de un "
        "nombre: NO preguntes. Actúa con tu mejor interpretación de lo que "
        "oíste; si te equivocaste, el usuario te lo dirá.\n"
        "- Si la acción no se deshace o llega a otras personas (enviar algo o "
        "comunicarse con alguien, borrar, programar algo para más tarde) y no "
        "estás seguro: NO llames ninguna herramienta. Di en una frase corta lo "
        "que crees haber entendido y pregunta si es correcto. Espera la "
        "respuesta.\n"
        "- Si el usuario te corrige ('no', 'no, eso no'), no repitas la misma "
        "suposición: vuelve a escuchar lo que dijo y prueba otra "
        "interpretación."
    ),
}


def compose(names: list[str]) -> str:
    """Return the joined constraint snippets for the given names.

    Order is preserved. Raises `UnknownConstraintError` on any unknown name —
    typos in `persona.yaml` should fail loudly at startup, not silently drop.
    """
    parts: list[str] = []
    for name in names:
        snippet = CONSTRAINTS.get(name)
        if snippet is None:
            known = ", ".join(sorted(CONSTRAINTS)) or "(none)"
            msg = f"Unknown constraint '{name}'. Known: {known}."
            raise UnknownConstraintError(msg)
        parts.append(snippet)
    return "\n\n".join(parts)
