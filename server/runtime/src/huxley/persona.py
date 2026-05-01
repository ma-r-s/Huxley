"""Persona spec — YAML-driven declaration of who the agent is.

A persona is `personas/<name>/persona.yaml` plus a `data/` dir. At
startup Huxley picks one — precedence: CLI path > `HUXLEY_PERSONA` env
var > autodiscovery (the only persona under `./personas/`, if exactly
one is present). Parses it into a `PersonaSpec` and uses it to drive
the system prompt, voice, skill list, per-skill config, and storage
location.

A persona may declare translations via an `i18n:` block keyed by ISO
language code (e.g. `en`, `fr`). Each entry overrides `system_prompt`,
`transcription_language`, and/or `ui_strings` for that language. Per-
skill config supports the same pattern via a nested `i18n:` block. At
session-connect time the client selects a language; the framework calls
`PersonaSpec.resolve(language)` to collapse overrides into a frozen
`ResolvedPersona` with the language-specific fields the rest of the
framework consumes.

Keep this file focused on: schema definition + file loader + language
resolution. Anything richer (constraint translations, cross-persona
inheritance) waits for a real use case to force the design.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field

from huxley.constraints import compose as compose_constraints

logger = structlog.get_logger()


class PersonaError(Exception):
    """Raised when a persona.yaml can't be loaded or parsed."""


class LanguageOverride(BaseModel):
    """Per-language overrides inside the persona's `i18n:` block.

    All fields optional — omit a field to inherit from the persona's
    default (top-level) value. `ui_strings` is merged key-by-key onto the
    default dict (missing keys fall through).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    transcription_language: str | None = None
    system_prompt: str | None = None
    ui_strings: dict[str, str] = Field(default_factory=dict)


class PersonaSpec(BaseModel):
    """Parsed `persona.yaml`.

    `version` is load-bearing: future schema changes bump this so old
    personas fail loudly instead of silently drifting. Path fields in
    `skills.<name>` config are resolved relative to `data_dir` by the
    framework at context-build time.

    The top-level `language_code`, `system_prompt`, `transcription_language`,
    and `ui_strings` define the DEFAULT language. The optional `i18n` block
    adds translations keyed by language code. Use `resolve(language)` to
    collapse overrides into a `ResolvedPersona`.
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
    # UI status strings sent to the client over WebSocket for the default
    # language. Keys: listening, too_short, sent, responding, ready.
    # Defaults to empty (framework falls back to English) so tests can boot
    # without persona config. Per-language variants live in `i18n`.
    ui_strings: dict[str, str] = Field(default_factory=dict)

    # Optional per-language overrides. Key = language code (e.g. "en",
    # "fr"). The default language (`self.language_code`) is NOT a valid
    # key here — its fields live at the top level.
    i18n: dict[str, LanguageOverride] = Field(default_factory=dict)

    # Populated by `load_persona` from the persona file's parent directory.
    # Not user-settable in YAML (the loader injects it); tests constructing
    # `PersonaSpec` directly must pass it explicitly.
    data_dir: Path

    @property
    def supported_languages(self) -> list[str]:
        """All language codes this persona supports, default first."""
        return [self.language_code, *self.i18n.keys()]

    @property
    def system_prompt_with_constraints(self) -> str:
        """Default-language prompt + composed constraint snippets.

        Shortcut for callers (tests, diagnostics) that just want the
        default system prompt. Per-session rendering should go through
        `resolve(language).system_prompt_with_constraints` so the active
        language wins.
        """
        return self.resolve().system_prompt_with_constraints

    def resolve(self, language: str | None = None) -> ResolvedPersona:
        """Collapse persona + per-language overrides for `language`.

        `language=None` or a language the persona does not support falls
        back to the default (`self.language_code`). Callers should check
        `supported_languages` first if they need to reject unsupported
        codes instead of falling back.
        """
        requested = (language or self.language_code).lower()
        if requested == self.language_code.lower():
            active = self.language_code
            override: LanguageOverride | None = None
        elif requested in self.i18n:
            active = requested
            override = self.i18n[requested]
        else:
            # Unsupported language — fall back to default silently. The
            # caller is expected to validate via `supported_languages` if
            # it wants stricter behavior.
            active = self.language_code
            override = None

        if override is None:
            system_prompt = self.system_prompt
            transcription_language = self.transcription_language
            ui_strings = dict(self.ui_strings)
        else:
            system_prompt = override.system_prompt or self.system_prompt
            transcription_language = override.transcription_language or active
            ui_strings = {**self.ui_strings, **override.ui_strings}

        resolved_skills = _resolve_skills(self.skills, active)

        return ResolvedPersona(
            name=self.name,
            voice=self.voice,
            language_code=active,
            transcription_language=transcription_language,
            timezone=self.timezone,
            system_prompt=system_prompt,
            constraints=tuple(self.constraints),
            skills=resolved_skills,
            ui_strings=ui_strings,
            data_dir=self.data_dir,
            supported_languages=tuple(self.supported_languages),
        )


def _resolve_skills(raw: dict[str, dict[str, Any]], language: str) -> dict[str, dict[str, Any]]:
    """Merge per-skill `i18n:<lang>` overrides into each skill's config.

    The framework strips the nested `i18n` block before handing the config
    to the skill. Language-aware keys inside `i18n.<language>` are merged
    shallowly on top. A `_language` sentinel is injected so a skill that
    never explicitly reads ``ctx.language`` can still discover the active
    language from its config if it wants to. Keys outside `i18n` pass
    through unchanged.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for name, cfg in raw.items():
        merged: dict[str, Any] = {k: v for k, v in cfg.items() if k != "i18n"}
        i18n_block = cfg.get("i18n")
        if isinstance(i18n_block, dict):
            override = i18n_block.get(language)
            if isinstance(override, dict):
                merged.update(override)
        merged.setdefault("_language", language)
        resolved[name] = merged
    return resolved


@dataclass(frozen=True)
class ResolvedPersona:
    """Persona view with i18n overrides collapsed for a specific language.

    Built by `PersonaSpec.resolve(language)` at session-connect time. The
    framework (provider, coordinator) consumes this rather than the raw
    `PersonaSpec` so every language-dependent field is already decided.
    """

    name: str
    voice: str
    language_code: str
    transcription_language: str
    timezone: str
    system_prompt: str
    constraints: tuple[str, ...]
    skills: dict[str, dict[str, Any]]
    ui_strings: dict[str, str]
    data_dir: Path
    supported_languages: tuple[str, ...]

    @property
    def system_prompt_with_constraints(self) -> str:
        """Prompt body + composed constraint snippets, joined with blank lines."""
        if not self.constraints:
            return self.system_prompt
        return f"{self.system_prompt}\n\n{compose_constraints(list(self.constraints))}"


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

    # Eagerly validate constraint names + every declared language resolves
    # cleanly so typos fail at load, not at session connect.
    try:
        _ = spec.resolve().system_prompt_with_constraints
        for lang in spec.i18n:
            _ = spec.resolve(lang).system_prompt_with_constraints
    except Exception as exc:
        msg = f"Invalid persona spec in {yaml_path}: {exc}"
        raise PersonaError(msg) from exc

    return spec


# ── Multi-persona enumeration (T1.13) ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PersonaSummary:
    """Lightweight metadata for a persona — used by the runtime to
    enumerate all available personas (e.g. for the PWA picker) without
    exposing every internal `PersonaSpec` field. The `name` is the
    directory basename and is the stable identifier the wire protocol
    uses; `language` is the persona's default `language_code` (overrides
    are not summarized here)."""

    name: str
    display_name: str
    language: str


def list_personas() -> list[PersonaSummary]:
    """Enumerate every loadable persona under `./personas/`, alphabetically.

    Walks up from CWD using the same `_find_personas_dir` logic
    `load_persona` uses, so this works from any subdirectory of a
    deployment. Skips directories whose `persona.yaml` is missing or
    invalid (logs a warning per skipped dir; does not fail enumeration —
    the runtime still returns the personas that DO load so the picker
    isn't entirely empty when one persona is broken).

    Empty list when no `./personas/` directory exists between CWD and
    filesystem root.
    """
    personas_dir = _find_personas_dir()
    if personas_dir is None:
        return []
    summaries: list[PersonaSummary] = []
    for d in sorted(personas_dir.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        if not (d / "persona.yaml").is_file():
            continue
        try:
            spec = load_persona(d)
        except PersonaError as exc:
            logger.warning(
                "persona.skipped_invalid",
                dir=str(d),
                error=str(exc),
            )
            continue
        summaries.append(
            PersonaSummary(
                name=spec.name,
                # No `display_name` field on PersonaSpec yet — return the
                # name as the display value. Add an optional field later
                # if a user-facing label distinct from the directory id
                # becomes useful.
                display_name=spec.name,
                language=spec.language_code,
            )
        )
    return summaries


def pick_default_persona_name(env_name: str | None = None) -> str | None:
    """Pick which persona to load when the client doesn't specify one.

    Precedence:

    1. `env_name` (typically `HUXLEY_PERSONA`) — passed through; the
       caller resolves it via `_find_named_persona` + `load_persona`.
    2. Single persona under `./personas/` — autodiscovered.
    3. Multiple personas + no env var — pick **alphabetically first**
       and log loudly. Refusing to start would force the env var on
       multi-persona installs, exactly the deployment artifact T1.13
       retires; the PWA picker lets the user change immediately.
    4. No personas → `None` (caller decides whether to error out).
    """
    if env_name is not None:
        return env_name
    summaries = list_personas()
    if not summaries:
        return None
    if len(summaries) == 1:
        return summaries[0].name
    chosen = summaries[0].name  # list_personas returns alphabetically sorted
    logger.warning(
        "persona.default_picked_alphabetically",
        chosen=chosen,
        available=[s.name for s in summaries],
        note="set HUXLEY_PERSONA to choose explicitly",
    )
    return chosen
