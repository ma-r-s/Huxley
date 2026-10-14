"""Huxley web-search skill: `ddgs` metasearch, Google News headlines, page excerpts.

The persona enables this skill so the LLM can look up anything that may
have changed since its training (offices, results, prices, events,
schedules). One tool: `search_the_web(query, max_results)`.
"""

from __future__ import annotations

from huxley_skill_search.skill import SearchSkill

__all__ = ["SearchSkill"]
