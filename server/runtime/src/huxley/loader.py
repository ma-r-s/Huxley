"""Skill discovery via Python entry points.

Each installed skill package declares an entry point in its `pyproject.toml`:

    [project.entry-points."huxley.skills"]
    audiobooks = "huxley_skill_audiobooks:AudiobooksSkill"

`discover_skills(["audiobooks", "system"])` returns the skill classes named
in the persona, raising if any name isn't installed. Entry-point loading
keeps the framework agnostic to which skills exist; `app.py` never imports
a concrete skill class.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huxley_sdk import Skill

ENTRY_POINT_GROUP = "huxley.skills"


class SkillNotInstalledError(Exception):
    """Raised when a persona references a skill name with no matching entry point."""


def available_skill_names() -> list[str]:
    """All skill names currently installed in the active environment."""
    return sorted(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))


def discover_skills(enabled: list[str]) -> dict[str, type[Skill]]:
    """Resolve `enabled` skill names to their classes via entry points.

    Returns a dict keyed by the persona's requested order. Raises
    `SkillNotInstalledError` if any name has no matching entry point —
    fail fast at startup rather than discover later that a tool is missing.
    """
    found: dict[str, type[Skill]] = {}
    eps = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
    missing: list[str] = []
    for name in enabled:
        ep = eps.get(name)
        if ep is None:
            missing.append(name)
            continue
        found[name] = ep.load()
    if missing:
        installed = ", ".join(available_skill_names()) or "(none)"
        msg = (
            f"Skill(s) not installed: {', '.join(missing)}. "
            f"Installed skills: {installed}. "
            f"Install with `uv add huxley-skill-<name>`."
        )
        raise SkillNotInstalledError(msg)
    return found
