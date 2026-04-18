"""Persona spec — YAML-driven declaration of who the agent is.

A persona is `personas/<name>/persona.yaml` plus a `data/` dir. At
startup Huxley picks one — precedence: CLI path > `HUXLEY_PERSONA` env
var > autodiscovery (the only persona under `./personas/`, if exactly
one is present). Parses it into a `PersonaSpec` and uses it to drive
the system prompt, voice, skill list, per-skill config, and storage
location.

Keep this file small: schema definition + file loader + prompt
composition. Anything richer (multi-language constraints, persona
inheritance) waits for a second real persona to force the design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from huxley.constraints import compose as compose_constraints


class PersonaError(Exception):
    """Raised when a persona.yaml can't be loaded or parsed."""


class PersonaSpec(BaseModel):
    """Parsed `persona.yaml`.

    `version` is load-bearing: future schema changes bump this so old
    personas fail loudly instead of silently drifting. Path fields in
    `skills.<name>` config are resolved relative to `data_dir` by the
    framework at context-build time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    name: str
    voice: str
    language_code: str
    transcription_language: str
    timezone: str
    system_prompt: str
    constraints: list[str] = Field(default_factory=list)
    skills: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # UI status strings sent to the client over WebSocket. Keys: listening,
    # too_short, sent, responding, ready. Defaults to English if omitted so
    # the framework boots without persona config in tests.
    ui_strings: dict[str, str] = Field(default_factory=dict)

    # Populated by `load_persona` from the persona file's parent directory.
    # Not user-settable in YAML (the loader injects it); tests constructing
    # `PersonaSpec` directly must pass it explicitly.
    data_dir: Path

    @property
    def system_prompt_with_constraints(self) -> str:
        """Prompt body + composed constraint snippets, joined with blank lines."""
        if not self.constraints:
            return self.system_prompt
        return f"{self.system_prompt}\n\n{compose_constraints(self.constraints)}"


SUPPORTED_VERSION = 1


def _find_personas_dir() -> Path | None:
    """Walk up from CWD looking for a `./personas/` directory.

    Returns the first match (so the server can run from any subdirectory
    of the repo, e.g. `packages/core/`). Returns `None` if no `personas/`
    directory exists between CWD and filesystem root.
    """
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / "personas"
        if candidate.is_dir():
            return candidate
    return None


def _find_named_persona(name: str) -> Path:
    """Locate `./personas/<name>/` by walking up from CWD.

    Falls back to `./personas/<name>` (relative to CWD) so the error
    message is readable when the directory really doesn't exist.
    """
    personas_dir = _find_personas_dir()
    if personas_dir is not None:
        candidate = personas_dir / name
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "personas" / name


def _autodiscover_persona() -> Path | None:
    """Return the only persona dir under `./personas/`, or None.

    When `HUXLEY_PERSONA` isn't set and no CLI path is given, the
    framework can still pick the right persona if exactly one exists in
    the local `./personas/` directory. If zero or multiple are present,
    returns None — caller raises a clear error pointing at the env var.
    """
    personas_dir = _find_personas_dir()
    if personas_dir is None:
        return None
    persona_dirs = [
        d for d in personas_dir.iterdir() if d.is_dir() and (d / "persona.yaml").is_file()
    ]
    if len(persona_dirs) == 1:
        return persona_dirs[0]
    return None


def resolve_persona_path(
    cli_path: Path | None = None,
    env_name: str | None = None,
) -> Path:
    """Resolve the persona directory to load.

    Precedence: CLI path > `HUXLEY_PERSONA` env var > autodiscovery
    (single persona under `./personas/`). If autodiscovery cannot pick
    a single persona, raises `PersonaError` with a message pointing at
    the env var — the framework refuses to guess.
    """
    if cli_path is not None:
        return cli_path.resolve()
    if env_name is not None:
        return _find_named_persona(env_name).resolve()
    discovered = _autodiscover_persona()
    if discovered is not None:
        return discovered.resolve()
    msg = (
        "no persona could be auto-discovered. Set HUXLEY_PERSONA to a "
        "directory name under ./personas/, or run with exactly one "
        "persona present under ./personas/."
    )
    raise PersonaError(msg)


def load_persona(path: Path | None = None) -> PersonaSpec:
    """Load and validate `persona.yaml` under `path`.

    `path` is the persona directory (containing `persona.yaml` and `data/`).
    Raises `PersonaError` with a readable message on any IO / parse /
    validation / version-mismatch failure — fail fast at startup.
    """
    root = path.resolve() if path is not None else resolve_persona_path()
    yaml_path = root / "persona.yaml"
    data_dir = (root / "data").resolve()

    try:
        raw = yaml_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        msg = f"persona.yaml not found at {yaml_path}"
        raise PersonaError(msg) from exc
    except OSError as exc:
        msg = f"Cannot read {yaml_path}: {exc}"
        raise PersonaError(msg) from exc

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        msg = f"Invalid YAML in {yaml_path}: {exc}"
        raise PersonaError(msg) from exc

    if not isinstance(parsed, dict):
        msg = f"{yaml_path} must contain a YAML mapping at the top level"
        raise PersonaError(msg)

    version = parsed.get("version", 1)
    if version != SUPPORTED_VERSION:
        msg = (
            f"{yaml_path} declares version {version}; this Huxley build "
            f"supports version {SUPPORTED_VERSION}."
        )
        raise PersonaError(msg)

    try:
        spec = PersonaSpec.model_validate({**parsed, "data_dir": data_dir})
    except Exception as exc:
        msg = f"Invalid persona spec in {yaml_path}: {exc}"
        raise PersonaError(msg) from exc

    # Eagerly validate constraint names so typos fail at load, not at connect.
    try:
        _ = spec.system_prompt_with_constraints
    except Exception as exc:
        msg = f"Invalid persona spec in {yaml_path}: {exc}"
        raise PersonaError(msg) from exc

    return spec
