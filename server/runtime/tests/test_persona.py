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


class TestResolvePersonaPath:
    """T1.6/T2.3 follow-up: framework no longer hardcodes 'abuelos'."""

    def test_cli_path_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import resolve_persona_path

        explicit = _write_persona(tmp_path / "explicit", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        resolved = resolve_persona_path(cli_path=explicit, env_name="ignored")

        assert resolved == explicit.resolve()

    def test_env_name_resolves_under_personas(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import resolve_persona_path

        target = _write_persona(tmp_path / "personas" / "myagent", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        resolved = resolve_persona_path(env_name="myagent")

        assert resolved == target.resolve()

    def test_autodiscovers_single_persona_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import resolve_persona_path

        target = _write_persona(tmp_path / "personas" / "only", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        resolved = resolve_persona_path()

        assert resolved == target.resolve()

    def test_autodiscovery_picks_named_when_multiple(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import resolve_persona_path

        a = _write_persona(tmp_path / "personas" / "a", VALID_YAML)
        _write_persona(tmp_path / "personas" / "b", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        # Multiple personas: env_name disambiguates.
        resolved = resolve_persona_path(env_name="a")
        assert resolved == a.resolve()

    def test_autodiscovery_fails_when_multiple_and_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import resolve_persona_path

        _write_persona(tmp_path / "personas" / "a", VALID_YAML)
        _write_persona(tmp_path / "personas" / "b", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        # Multiple personas + no env_name = framework refuses to guess.
        with pytest.raises(PersonaError, match="auto-discovered"):
            resolve_persona_path()

    def test_autodiscovery_fails_when_no_personas_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import resolve_persona_path

        # Empty tmp_path — no personas/ directory anywhere up the tree.
        # We need to chdir somewhere that won't have the project's
        # personas/ visible up the tree, so use a deep tmp subdir.
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        # tmp_path lives under /private/var/folders/... and there's no
        # personas/ in that tree, so autodiscovery returns None.
        # If the actual repo's personas/ is reachable from the test's
        # ancestors (it shouldn't be from /tmp), this test would falsely
        # pass — but tmp_path under /tmp is isolated.
        with pytest.raises(PersonaError, match="auto-discovered"):
            resolve_persona_path()


class TestListPersonas:
    """T1.13 — multi-persona enumeration for the runtime / PWA picker."""

    def test_returns_empty_when_no_personas_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import list_personas

        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        assert list_personas() == []

    def test_enumerates_alphabetically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import list_personas

        personas_dir = tmp_path / "personas"
        # Create three personas — write them in non-alphabetical order
        # to verify the list comes back sorted.
        for name in ("zebra", "abuelos", "librarian"):
            body = VALID_YAML.replace("name: TestBot", f"name: {name}")
            _write_persona(personas_dir / name, body)
        monkeypatch.chdir(tmp_path)

        names = [s.name for s in list_personas()]
        assert names == ["abuelos", "librarian", "zebra"]

    def test_skips_invalid_personas_without_failing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import list_personas

        personas_dir = tmp_path / "personas"
        # One valid, one with bad YAML.
        _write_persona(personas_dir / "good", VALID_YAML)
        _write_persona(personas_dir / "broken", "name: : :: invalid")
        monkeypatch.chdir(tmp_path)

        # Broken one is skipped silently (logged as a warning); the
        # valid one still surfaces. The picker should never be empty
        # because of one bad persona.
        names = [s.name for s in list_personas()]
        assert names == ["TestBot"]  # only "good" loaded; uses spec.name

    def test_summary_carries_default_language(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import list_personas

        personas_dir = tmp_path / "personas"
        _write_persona(personas_dir / "abuelos", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        summaries = list_personas()
        assert len(summaries) == 1
        assert summaries[0].language == "es"


class TestPickDefaultPersonaName:
    """T1.13 — boot-time persona resolution rule (locked):
    env > single-persona-autodiscovery > alphabetic-fallback-with-log."""

    def test_env_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from huxley.persona import pick_default_persona_name

        personas_dir = tmp_path / "personas"
        _write_persona(personas_dir / "abuelos", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        # Even though "abuelos" is the only one and would be
        # autodiscovered, the env name passes through verbatim.
        assert pick_default_persona_name(env_name="basicos") == "basicos"

    def test_single_persona_autodiscovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import pick_default_persona_name

        personas_dir = tmp_path / "personas"
        _write_persona(personas_dir / "abuelos", VALID_YAML)
        monkeypatch.chdir(tmp_path)

        assert pick_default_persona_name() == "TestBot"  # spec.name

    def test_multi_persona_alphabetic_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import pick_default_persona_name

        personas_dir = tmp_path / "personas"
        for dirname, name in (
            ("zebra", "Zebra"),
            ("abuelos", "Abuelos"),
            ("librarian", "Librarian"),
        ):
            body = VALID_YAML.replace("name: TestBot", f"name: {name}")
            _write_persona(personas_dir / dirname, body)
        monkeypatch.chdir(tmp_path)

        # No env var, no single-persona autodiscovery → pick
        # alphabetically-first directory (Abuelos's dir is "abuelos").
        # Loud log line is emitted; we don't assert on log contents
        # here — that's verified by inspection during smoke.
        assert pick_default_persona_name() == "Abuelos"

    def test_no_personas_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from huxley.persona import pick_default_persona_name

        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        assert pick_default_persona_name() is None
