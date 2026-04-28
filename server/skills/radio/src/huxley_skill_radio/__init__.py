"""Internet radio playback skill for Huxley.

Persona-agnostic. Streams from a curated list of HTTP/Icecast URLs (no
discovery layer in v1 — stations come from the persona's `skills.radio.stations`
config). Same `AudioStream` machinery the audiobooks skill uses, minus
position tracking (radio is live, "where I left off" is meaningless).
"""

from __future__ import annotations

from huxley_skill_radio.skill import RadioSkill

__all__ = ["RadioSkill"]
