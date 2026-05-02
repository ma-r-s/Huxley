"""ruamel.yaml round-trip for persona.yaml writes (Marketplace v2 Phase B).

When the PWA's Skills panel toggles a skill or edits its config, the
runtime mutates the active persona's `persona.yaml`. The file is
hand-edited and comment-rich (system_prompt notes, constraint
explanations, skill notes) — a naive PyYAML write would obliterate
all of that.

This module wraps ruamel.yaml's round-trip mode with helpers for the
two operations Phase B needs: enabling/disabling a skill (toggling
the presence of `skills.<name>` in the YAML) and replacing a skill's
config block (everything under `skills.<name>:` except secrets,
which never live in YAML — they're at `<persona>/data/secrets/<skill>/
values.json`).

Atomic write semantics: write to a temp file in the same directory,
fsync, then `os.replace` so a crash mid-write can't leave a half-
serialized YAML that fails to parse on next boot. The pattern mirrors
JsonFileSecrets's atomic write.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _yaml() -> YAML:
    """Configured ruamel.yaml instance preserving comments + style.

    `preserve_quotes=True` keeps explicit `"value"` quotes the user
    wrote (some persona authors quote multi-word strings); `width=120`
    matches the existing persona.yaml line width so re-saves don't
    reflow long lines unnecessarily."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 120
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def load_persona_yaml(path: Path) -> Any:
    """Read persona.yaml preserving comments + ordering + quote style.

    Returns ruamel's `CommentedMap` (a dict-like with attached
    metadata). Mutate it in place via the helpers below; don't try to
    reconstruct it from a plain dict — that loses the round-trip
    metadata."""
    yaml = _yaml()
    with path.open("r", encoding="utf-8") as f:
        return yaml.load(f)


def save_persona_yaml(path: Path, data: Any) -> None:
    """Write persona.yaml atomically.

    `data` MUST be a ruamel-loaded structure (CommentedMap) — passing
    a plain dict serializes correctly but strips comments. Atomic via
    temp-file + os.replace so a crash mid-write can't leave a
    truncated file that fails to parse on the next boot."""
    yaml = _yaml()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_path).replace(path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            Path(tmp_path).unlink()
        raise


def set_skill_enabled(
    data: Any,
    skill_name: str,
    enabled: bool,
    *,
    default_config: dict[str, Any] | None = None,
) -> None:
    """Toggle a skill's presence in the YAML's `skills:` block.

    On enable: if the skill is already present, no-op (preserves any
    existing config). Otherwise add it with `default_config` (or an
    empty mapping if not provided).

    On disable: remove the skill's entry from `skills:` if present.
    The skill's data + secrets dir on disk is NOT touched — disabling
    a skill is reversible by re-enabling it; the persisted state
    survives until the operator deletes it manually.

    Mutates `data` in place. Caller is responsible for serializing
    via `save_persona_yaml`.
    """
    skills = data.get("skills")
    if skills is None:
        skills = CommentedMap()
        data["skills"] = skills
    if enabled:
        if skill_name in skills:
            return
        block = CommentedMap()
        if default_config:
            for k, v in default_config.items():
                block[k] = v
        skills[skill_name] = block
    elif skill_name in skills:
        del skills[skill_name]


def set_skill_config(
    data: Any,
    skill_name: str,
    config: dict[str, Any],
) -> None:
    """Replace a skill's config block.

    Auto-enables the skill if it isn't already present (a config
    write implies the skill should be enabled). Replaces the block
    wholesale rather than merging — Phase B's UX edits the full
    block at once, so partial-merge semantics would surprise users
    (deleted-on-form key would silently survive).

    Comments inside the skill's block are NOT preserved by this
    operation — replacing the CommentedMap drops them. If users want
    inline comments, they must hand-edit the YAML; the PWA workflow
    is config-shaped.
    """
    skills = data.get("skills")
    if skills is None:
        skills = CommentedMap()
        data["skills"] = skills
    new_block = CommentedMap()
    for k, v in config.items():
        new_block[k] = v
    skills[skill_name] = new_block
