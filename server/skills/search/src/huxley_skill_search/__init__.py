"""Huxley web-search skill — DuckDuckGo via the `ddgs` package.

The persona enables this skill so the LLM can fetch current information
the model doesn't already know (today's weather, sports results, prices,
recent events). One tool: `search_the_web(query, max_results)`.
"""

from __future__ import annotations

from huxley_skill_search.skill import SearchSkill

__all__ = ["SearchSkill"]
