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

SUMMARY_PROMPT = (
    "Eres un asistente que resume conversaciones de voz para que otro asistente "
    "pueda retomar el contexto al reconectarse. La siguiente transcripción es "
    "una conversación entre un usuario y un asistente. Resúmela en 3 frases "
    "concisas en español, capturando: qué estaba haciendo el usuario (libro, "
    "radio, etc.), qué pidió por última vez, y cualquier estado relevante "
    "(capítulo, posición, emisora). No inventes detalles que no estén en la "
    "transcripción. Devuelve solo el resumen, sin preámbulo."
)

DEFAULT_MAX_LINES = 60
"""Cap on transcript lines fed to the summarizer. Bounds prompt size on
very long sessions; the user's recent state is at the tail anyway."""

DEFAULT_MAX_OUTPUT_TOKENS = 200
"""Cap on summary length. 3 sentences in Spanish typically fit easily."""


async def summarize_transcript(
    transcript_lines: list[str],
    api_key: str,
    *,
    model: str = SUMMARY_MODEL,
    max_lines: int = DEFAULT_MAX_LINES,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> str | None:
    """Summarize a transcript via gpt-4o-mini chat completion.

    Returns the summary string on success, or `None` if the transcript is
    empty, the API call fails, or the model returns no content. Callers
    should handle `None` gracefully (e.g. fall back to raw tail).
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
                {"role": "system", "content": SUMMARY_PROMPT},
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
    )
    return summary
