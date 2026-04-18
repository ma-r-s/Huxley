"""News + weather skill for Huxley.

Persona-agnostic: returns structured JSON, the LLM narrates per its
persona's tone (slow/warm vs terse/bullets). Optional PlaySound chime
for personas that want a sonic intro to news playback (configured via
`start_sound` key in the persona's `skills.news` block).

Data sources (no API key for either):
- Weather: Open-Meteo (api.open-meteo.com) — JSON, lat/lng based.
- News: Google News RSS (news.google.com/rss) — country + language
  filtered, plus topic-specific feeds for category filtering.
"""

from __future__ import annotations

from huxley_skill_news.skill import NewsSkill

__all__ = ["NewsSkill"]
