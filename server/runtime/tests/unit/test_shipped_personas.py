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
