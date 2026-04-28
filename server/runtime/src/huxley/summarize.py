"""LLM-backed conversation summarization for reconnect continuity.

When the OpenAI Realtime session disconnects (timeout, network drop, cost
kill switch), the next reconnect needs a brief context summary so the
model knows what was happening. Raw transcript-tail dumps (the original
behavior) degrade fast — by reconnect 5 in a long listening session, the
last 20 lines are noise unrelated to current state. A real LLM-generated
summary preserves intent ("user is mid-book X at chapter Y, last paused
5 minutes ago") in a small token budget.

Uses gpt-4o-mini via standard chat-completions — much cheaper than the
Realtime API, runs in the natural ~2s reconnect window so the user sees
no extra latency. ~$0.001 per summarization at typical sizes.

See docs/triage.md T1.5.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI

logger = structlog.get_logger()

SUMMARY_MODEL = "gpt-4o-mini"
"""Cheap chat-completions model (NOT the realtime variant)."""

# Per-language summarization prompts. The summary is fed back into the
# next session's system prompt under a localized header (see
# `CONTEXT_HEADERS`), so it must be written in the persona's language for
# the downstream LLM to narrate naturally. Unknown languages fall back to
# English — safer than mis-attributing a language.
SUMMARY_PROMPTS: dict[str, str] = {
    "es": (
        "Eres un asistente que resume conversaciones de voz para que otro "
        "asistente pueda retomar el contexto al reconectarse. La siguiente "
        "transcripción es una conversación entre un usuario y un asistente. "
        "Resúmela en 3 frases concisas en español, capturando: qué estaba "
        "haciendo el usuario (libro, radio, etc.), qué pidió por última vez, "
        "y cualquier estado relevante (capítulo, posición, emisora). No "
        "inventes detalles que no estén en la transcripción. Devuelve solo "
        "el resumen, sin preámbulo."
    ),
    "en": (
        "You are an assistant that summarizes voice conversations so another "
        "assistant can resume context after reconnecting. The following is a "
        "conversation between a user and an assistant. Summarize it in 3 "
        "concise English sentences, capturing: what the user was doing (book, "
        "radio, etc.), what they last asked for, and any relevant state "
        "(chapter, position, station). Do not invent details that are not in "
        "the transcript. Return only the summary, no preamble."
    ),
    "fr": (
        "Tu es un assistant qui résume les conversations vocales pour qu'un "
        "autre assistant puisse reprendre le contexte lors de la reconnexion. "
        "La transcription suivante est une conversation entre un utilisateur "
        "et un assistant. Résume-la en 3 phrases concises en français, en "
        "capturant : ce que faisait l'utilisateur (livre, radio, etc.), ce "
        "qu'il a demandé en dernier, et tout état pertinent (chapitre, "
        "position, station). N'invente pas de détails qui ne figurent pas "
        "dans la transcription. Donne uniquement le résumé, sans préambule."
    ),
}

# Header prefixing the summary when it's folded into the next session's
# instructions. Same language as the summary itself so the LLM reads
# continuous prose instead of a language switch mid-prompt.
CONTEXT_HEADERS: dict[str, str] = {
    "es": "Contexto de la conversación anterior:",
    "en": "Context from the previous conversation:",
    "fr": "Contexte de la conversation précédente :",
}


def summary_prompt_for(language: str) -> str:
    """Return the summarization system prompt for a language code.

    Falls back to English for unknown codes — safer than forcing a
    language the transcript may not actually be in.
    """
    return SUMMARY_PROMPTS.get(language.lower(), SUMMARY_PROMPTS["en"])


def context_header_for(language: str) -> str:
    """Return the localized "context from previous conversation" header."""
    return CONTEXT_HEADERS.get(language.lower(), CONTEXT_HEADERS["en"])


DEFAULT_MAX_LINES = 60
"""Cap on transcript lines fed to the summarizer. Bounds prompt size on
very long sessions; the user's recent state is at the tail anyway."""

DEFAULT_MAX_OUTPUT_TOKENS = 200
"""Cap on summary length. 3 sentences in Spanish typically fit easily."""


async def summarize_transcript(
    transcript_lines: list[str],
    api_key: str,
    *,
    language: str = "en",
    model: str = SUMMARY_MODEL,
    max_lines: int = DEFAULT_MAX_LINES,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> str | None:
    """Summarize a transcript via gpt-4o-mini chat completion.

    Returns the summary string on success, or `None` if the transcript is
    empty, the API call fails, or the model returns no content. Callers
    should handle `None` gracefully (e.g. fall back to raw tail).

    `language` picks the summarization prompt — the output ends up in the
    next session's instructions, so it must match the persona language the
    reconnecting session will run in. Defaults to English for safety when
    a language isn't supplied.
    """
    if not transcript_lines:
        return None
    if not api_key:
        await logger.awarning("summarize.skipped_no_api_key")
        return None

    transcript = "\n".join(transcript_lines[-max_lines:])

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": summary_prompt_for(language)},
                {"role": "user", "content": transcript},
            ],
            max_tokens=max_output_tokens,
            temperature=0.3,
        )
    except Exception:
        await logger.aexception("summarize.failed")
        return None

    if not response.choices:
        await logger.awarning("summarize.empty_choices")
        return None

    summary = response.choices[0].message.content
    if not summary:
        await logger.awarning("summarize.empty_content")
        return None

    summary = summary.strip()
    await logger.ainfo(
        "summarize.completed",
        input_lines=len(transcript_lines),
        sent_lines=min(len(transcript_lines), max_lines),
        output_chars=len(summary),
        model=model,
        language=language,
    )
    return summary
