"""Guards on the personas shipped in server/personas/.

Real use (2026-10-12): the abuelos prompt's example music ("pon algo de
Los Panchos", "pon boleros") became the model's guess whenever it
misheard a request; a different band's request searched the example
genre and played the example artist. Prompts and tool descriptions keep
placeholders, never example artists or genres.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PERSONAS = Path(__file__).resolve().parents[3] / "personas"
LEAKED_EXAMPLES = ("panchos", "bolero", "bésame")


def _prompts(persona: dict[str, Any]) -> dict[str, str]:
    prompts = {"default": str(persona.get("system_prompt") or "")}
    for lang, block in (persona.get("i18n") or {}).items():
        if isinstance(block, dict) and block.get("system_prompt"):
            prompts[lang] = str(block["system_prompt"])
    return prompts


@pytest.mark.parametrize(
    "path", sorted(PERSONAS.glob("*/persona.yaml")), ids=lambda p: p.parent.name
)
def test_prompts_name_no_example_music(path: Path) -> None:
    persona = yaml.safe_load(path.read_text(encoding="utf-8"))
    for lang, prompt in _prompts(persona).items():
        lowered = prompt.lower()
        for noun in LEAKED_EXAMPLES:
            assert noun not in lowered, (path.parent.name, lang, noun)


def test_shipped_personas_are_found() -> None:
    assert (PERSONAS / "abuelos" / "persona.yaml").is_file()


# The reminder confirmations the abuelos prompt sends to
# `acknowledge_reminder`. A press made only of stop words keeps paused
# media stopped (T1.30), so a stop word that is also a confirmation ("ya")
# turned "ya, me la tome" into "stop the audiobook" (2026-10-12 review).
REMINDER_CONFIRMATIONS = ("listo", "ya", "hecho")


def _stop_word_lists(persona: dict[str, Any]) -> dict[str, set[str]]:
    lists = {}
    blocks = {"default": persona.get("ui_strings") or {}}
    for lang, block in (persona.get("i18n") or {}).items():
        if isinstance(block, dict):
            blocks[lang] = block.get("ui_strings") or {}
    for lang, strings in blocks.items():
        if isinstance(strings, dict) and strings.get("content_stop_words"):
            lists[lang] = set(str(strings["content_stop_words"]).split())
    return lists


def test_reminder_confirmations_are_not_stop_words() -> None:
    persona = yaml.safe_load((PERSONAS / "abuelos" / "persona.yaml").read_text(encoding="utf-8"))
    lists = _stop_word_lists(persona)
    assert lists, "abuelos defines content_stop_words"
    for lang, words in lists.items():
        assert not words & set(REMINDER_CONFIRMATIONS), (lang, words & set(REMINDER_CONFIRMATIONS))


# Disabling a skill must take its instructions with it: abuelos dropped
# the local audiobooks skill for Spotify's audiobooks (2026-10-12, T1.39),
# and a prompt still telling the model to call `play_audiobook` would send
# it after a tool that no longer exists.
def _tools_by_skill() -> dict[str, set[str]]:
    from importlib.metadata import entry_points

    found: dict[str, set[str]] = {}
    for ep in entry_points(group="huxley.skills"):
        try:
            found[ep.name] = {tool.name for tool in ep.load()().tools}
        except Exception:  # a skill that can't list tools unconfigured is not checked
            continue
    return found


@pytest.mark.parametrize(
    "path", sorted(PERSONAS.glob("*/persona.yaml")), ids=lambda p: p.parent.name
)
def test_prompts_name_no_tool_of_a_skill_they_do_not_enable(path: Path) -> None:
    persona = yaml.safe_load(path.read_text(encoding="utf-8"))
    enabled = set((persona.get("skills") or {}).keys())
    tools = _tools_by_skill()
    assert "audiobooks" in tools  # the check below is live, not vacuous
    for lang, prompt in _prompts(persona).items():
        for skill, names in tools.items():
            if skill in enabled:
                continue
            for name in names:
                assert f"`{name}`" not in prompt, (path.parent.name, lang, skill, name)
