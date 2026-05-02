"""Tests for the persona.yaml round-trip helpers (Marketplace v2 Phase B).

The round-trip is load-bearing because persona.yaml files are
hand-edited and comment-rich. A naive PyYAML load+dump would strip
all comments — which would erase author intent (system_prompt notes,
constraint explanations, skill notes) on every PWA write.

These tests pin the contract:
1. Comments at every level (top, sibling, inline) survive a
   load → save round-trip with no edits.
2. `set_skill_enabled(True)` adds the skill if absent + idempotent
   if present.
3. `set_skill_enabled(False)` removes the skill but leaves the
   surrounding block (and its comments) intact.
4. `set_skill_config` replaces the skill's block + auto-enables.
5. Atomic write: the file on disk is either the old content or the
   new content — never partial.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from huxley.persona_yaml import (
    load_persona_yaml,
    save_persona_yaml,
    set_skill_config,
    set_skill_enabled,
)

if TYPE_CHECKING:
    from pathlib import Path

# Sample persona.yaml with comments at every level — the round-trip
# preservation contract has to hold for ALL of these.
_SAMPLE_PERSONA = """\
# Top-level comment — describes the persona's purpose.
version: 1
name: "Test Abuelo"
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota

# This block defines the system prompt; multi-line for readability.
system_prompt: |
  Eres un asistente cariñoso.
  Responde en español.

# Behavioral constraints the persona honors.
constraints:
  - never_say_no
  - confirm_destructive

# ── Skills ─────────────────────────────────────────────
# Each entry below maps a skill's entry-point key to its config.
skills:
  audiobooks:
    # The library lives at this path relative to the persona's data dir.
    library_path: ./library
  news:
    country: CO
    feed_language: es

# UI strings sent to the PWA / ESP32 client.
ui_strings:
  listening: "Escuchando"
  ready: "Listo"
"""


def test_load_save_round_trip_preserves_top_level_comments(tmp_path: Path) -> None:
    """Top-level standalone comments and blank lines survive load+save."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    save_persona_yaml(path, data)
    written = path.read_text(encoding="utf-8")
    assert "# Top-level comment" in written
    assert "# This block defines the system prompt" in written
    assert "# Behavioral constraints" in written
    assert "# UI strings sent to the PWA" in written


def test_load_save_round_trip_preserves_inline_comments(tmp_path: Path) -> None:
    """Inline comments next to specific values aren't dropped."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    save_persona_yaml(path, data)
    written = path.read_text(encoding="utf-8")
    assert "# The library lives at this path" in written
    assert "# Each entry below maps" in written


def test_set_skill_enabled_adds_new_skill(tmp_path: Path) -> None:
    """Enabling an absent skill adds it to the skills: block."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    set_skill_enabled(data, "stocks", enabled=True)
    save_persona_yaml(path, data)
    written = path.read_text(encoding="utf-8")
    # Verify by re-loading and inspecting the parsed structure
    reloaded = load_persona_yaml(path)
    assert "stocks" in reloaded["skills"]
    # The original skills + comments are still there
    assert "audiobooks" in reloaded["skills"]
    assert "# Each entry below maps" in written


def test_set_skill_enabled_with_default_config_adds_block(
    tmp_path: Path,
) -> None:
    """When enabling, supplied default_config becomes the skill's
    initial block."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    set_skill_enabled(
        data,
        "stocks",
        enabled=True,
        default_config={"currency": "USD", "watchlist": ["AAPL"]},
    )
    save_persona_yaml(path, data)
    reloaded = load_persona_yaml(path)
    stocks = reloaded["skills"]["stocks"]
    assert stocks["currency"] == "USD"
    assert list(stocks["watchlist"]) == ["AAPL"]


def test_set_skill_enabled_idempotent_on_already_enabled(tmp_path: Path) -> None:
    """Enabling a skill that's already in the block is a no-op:
    config + comments preserved."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    # audiobooks is already present with library_path
    set_skill_enabled(data, "audiobooks", enabled=True)
    save_persona_yaml(path, data)
    written = path.read_text(encoding="utf-8")
    assert "library_path: ./library" in written
    # The inline comment on library_path survives the no-op
    assert "# The library lives at this path" in written


def test_set_skill_enabled_false_removes_skill(tmp_path: Path) -> None:
    """Disabling removes the skill from skills:; surrounding
    block stays intact."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    set_skill_enabled(data, "audiobooks", enabled=False)
    save_persona_yaml(path, data)
    reloaded = load_persona_yaml(path)
    assert "audiobooks" not in reloaded["skills"]
    # news survives
    assert "news" in reloaded["skills"]
    # Skills section's leading comment is preserved
    written = path.read_text(encoding="utf-8")
    assert "# ── Skills" in written


def test_set_skill_enabled_false_when_absent_is_noop(tmp_path: Path) -> None:
    """Disabling a skill that wasn't enabled doesn't crash."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    set_skill_enabled(data, "telegram", enabled=False)  # not in sample
    save_persona_yaml(path, data)
    reloaded = load_persona_yaml(path)
    assert "telegram" not in reloaded["skills"]
    assert "audiobooks" in reloaded["skills"]


def test_set_skill_config_replaces_block_and_auto_enables(tmp_path: Path) -> None:
    """Writing config for a skill that was disabled enables it; the
    new block is the EXACT contents passed (replace, not merge)."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    set_skill_config(data, "search", {"safesearch": "moderate"})
    save_persona_yaml(path, data)
    reloaded = load_persona_yaml(path)
    assert dict(reloaded["skills"]["search"]) == {"safesearch": "moderate"}


def test_set_skill_config_replaces_existing_block(tmp_path: Path) -> None:
    """Writing config for a skill that's already enabled replaces
    its block wholesale — keys removed from the new config DON'T
    silently survive in the YAML."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    # news currently has country + feed_language; new config drops country
    set_skill_config(data, "news", {"feed_language": "en"})
    save_persona_yaml(path, data)
    reloaded = load_persona_yaml(path)
    news = dict(reloaded["skills"]["news"])
    assert news == {"feed_language": "en"}
    assert "country" not in news


def test_save_is_atomic(tmp_path: Path) -> None:
    """The temp-file + os.replace pattern means the on-disk file is
    never partially written. No leftover .tmp files after a save."""
    path = tmp_path / "persona.yaml"
    path.write_text(_SAMPLE_PERSONA, encoding="utf-8")
    data = load_persona_yaml(path)
    save_persona_yaml(path, data)
    # No `.persona.yaml.<random>.tmp` should remain in the dir
    leftover = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftover == []


def test_load_handles_missing_skills_block(tmp_path: Path) -> None:
    """A persona without a `skills:` block (lazy-init persona) still
    accepts set_skill_enabled — the helper creates the block on
    demand."""
    path = tmp_path / "persona.yaml"
    path.write_text(
        "version: 1\nname: Empty\nvoice: alloy\nlanguage_code: en\n"
        "transcription_language: en\ntimezone: UTC\nsystem_prompt: hi\n",
        encoding="utf-8",
    )
    data = load_persona_yaml(path)
    set_skill_enabled(data, "system", enabled=True)
    save_persona_yaml(path, data)
    reloaded = load_persona_yaml(path)
    assert "system" in reloaded["skills"]
