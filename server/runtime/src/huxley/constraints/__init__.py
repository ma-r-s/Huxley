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
    "echo_short_input": (
        "Cuando el usuario diga algo muy corto (una o dos palabras), repite en "
        "una frase breve lo que entendiste ANTES de actuar o responder de fondo. "
        "Ejemplo: el usuario dice '¿libros?' → tú dices '¿Preguntaste por los "
        "libros?' y esperas confirmación antes de hacer nada. "
        "Si el usuario dice algo largo y claro, actúa directo sin repetir."
    ),
    "confirm_if_unclear": (
        "Antes de ejecutar cualquier acción (reproducir un libro, cambiar el "
        "volumen, pausar, etc.), evalúa si entendiste bien la solicitud.\n"
        "— Si la intención es clara y no hay ambigüedad: actúa directamente, "
        "sin pedir confirmación.\n"
        "— Si el audio sonó cortado, la solicitud fue ambigua, o no estás "
        "seguro de lo que pidieron: NO llames ninguna herramienta. En cambio, "
        "di en una frase corta lo que crees haber entendido y pregunta si es "
        "correcto. Espera la respuesta antes de actuar.\n"
        "Ejemplo: '¿Querías que pusiera el libro?' — luego espera."
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
