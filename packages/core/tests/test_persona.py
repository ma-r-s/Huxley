"""Tests for the persona YAML loader."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime by fixture param annotations

import pytest

from huxley.persona import PersonaError, load_persona


def _write_persona(dir_: Path, body: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "persona.yaml").write_text(body, encoding="utf-8")
    (dir_ / "data").mkdir(exist_ok=True)
    return dir_


VALID_YAML = """\
version: 1
name: TestBot
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Eres un asistente de prueba.
constraints:
  - never_say_no
skills:
  audiobooks:
    library: audiobooks
  system: {}
"""


class TestLoadPersona:
    def test_loads_valid_persona(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, VALID_YAML)
        spec = load_persona(tmp_path)
        assert spec.name == "TestBot"
        assert spec.voice == "coral"
        assert spec.language_code == "es"
        assert "Eres un asistente" in spec.system_prompt
        assert spec.constraints == ["never_say_no"]
        assert set(spec.skills.keys()) == {"audiobooks", "system"}

    def test_data_dir_resolves_to_absolute_path(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, VALID_YAML)
        spec = load_persona(tmp_path)
        assert spec.data_dir == (tmp_path / "data").resolve()
        assert spec.data_dir.is_absolute()

    def test_composes_constraints_into_system_prompt(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, VALID_YAML)
        spec = load_persona(tmp_path)
        full = spec.system_prompt_with_constraints
        assert spec.system_prompt in full
        assert "nunca" in full.lower()  # the never_say_no snippet is Spanish

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PersonaError, match="persona.yaml not found"):
            load_persona(tmp_path / "does-not-exist")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, "version: 1\nname: : ::\n")
        with pytest.raises(PersonaError, match="Invalid YAML"):
            load_persona(tmp_path)

    def test_version_mismatch_raises(self, tmp_path: Path) -> None:
        body = VALID_YAML.replace("version: 1", "version: 99")
        _write_persona(tmp_path, body)
        with pytest.raises(PersonaError, match="version 99"):
            load_persona(tmp_path)

    def test_unknown_constraint_raises(self, tmp_path: Path) -> None:
        body = VALID_YAML.replace("- never_say_no", "- not_a_real_constraint")
        _write_persona(tmp_path, body)
        with pytest.raises(PersonaError, match="not_a_real_constraint"):
            load_persona(tmp_path)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        body = VALID_YAML.replace("name: TestBot\n", "")
        _write_persona(tmp_path, body)
        with pytest.raises(PersonaError, match="Invalid persona spec"):
            load_persona(tmp_path)

    def test_extra_top_level_field_raises(self, tmp_path: Path) -> None:
        body = VALID_YAML + "typo_field: oops\n"
        _write_persona(tmp_path, body)
        with pytest.raises(PersonaError, match="Invalid persona spec"):
            load_persona(tmp_path)
