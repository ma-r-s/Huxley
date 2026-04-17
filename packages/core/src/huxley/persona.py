"""Persona spec — YAML-driven declaration of who the agent is.

A persona is `personas/<name>/persona.yaml` plus a `data/` dir. At
startup Huxley picks one (CLI > `HUXLEY_PERSONA` env var > default
`./personas/abuelos`), parses it into a `PersonaSpec`, and uses it to
drive the system prompt, voice, skill list, per-skill config, and
storage location.

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


def _default_persona_root() -> Path:
    return Path.cwd() / "personas" / "abuelos"


def resolve_persona_path(
    cli_path: Path | None = None,
    env_name: str | None = None,
) -> Path:
    """Resolve the persona directory to load.

    Precedence: CLI path > `HUXLEY_PERSONA` env var > default
    `./personas/abuelos`. `env_name` is just a name (e.g. `abuelos`);
    it's joined under `./personas/`.
    """
    if cli_path is not None:
        return cli_path.resolve()
    if env_name:
        return (Path.cwd() / "personas" / env_name).resolve()
    return _default_persona_root().resolve()


def load_persona(path: Path | None = None) -> PersonaSpec:
    """Load and validate `persona.yaml` under `path`.

    `path` is the persona directory (containing `persona.yaml` and `data/`).
    Raises `PersonaError` with a readable message on any IO / parse /
    validation / version-mismatch failure — fail fast at startup.
    """
    root = path.resolve() if path is not None else _default_persona_root().resolve()
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
