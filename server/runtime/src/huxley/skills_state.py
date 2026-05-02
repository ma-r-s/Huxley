"""Build the `skills_state` wire payload for the PWA Skills panel.

The PWA's DeviceSheet → Skills section needs a structured view of every
skill the operator could enable on the active persona — what's installed,
what's currently turned on, what config + secrets each skill expects.
This module assembles that view from three sources:

1. **Entry points** (`huxley.skills` group) — every skill discoverable in
   the active venv. Class-level metadata (`config_schema`,
   `data_schema_version`) is read without instantiation.
2. **Active persona's `skills:` block** — reflects which skills are
   currently enabled and with what config. Read from `app.persona.skills`
   if a persona is loaded; treated as empty otherwise (lazy-boot window).
3. **Per-persona secrets dir** — `<persona>/data/secrets/<skill>/values.json`.
   We surface the **keys present**, never the values, so the UI can show
   "api_key set" / "api_key not set" without leaking secrets over the wire.

Read-only by design — Phase A of marketplace v2. Phase B layers writes
on top via separate `set_skill_*` frames; this module stays untouched.
"""

from __future__ import annotations

import contextlib
import json
import re
from importlib.metadata import (
    PackageNotFoundError,
    distribution,
    entry_points,
    metadata,
)
from typing import TYPE_CHECKING, Any

from huxley.loader import ENTRY_POINT_GROUP

if TYPE_CHECKING:
    from pathlib import Path

    from huxley.app import Application

# `Author-email` is the dominant author-identification field in modern
# pyproject.toml metadata (PEP 621). It typically arrives as
# ``"Mario Ruiz <marioalejandroruizsarmiento@gmail.com>"``; we extract
# just the name for display. Falls back to the raw `Author` field if
# email isn't set.
_AUTHOR_NAME_RE = re.compile(r"^\s*([^<]+?)\s*<")


def build_skills_state(app: Application | None) -> dict[str, Any]:
    """Build the `skills_state` payload for one PWA connection.

    `app is None` covers the lazy-boot window where the server is up
    but no persona has been selected yet — clients still get the list
    of installed skills (so the Marketplace tab works) but `enabled`
    is False everywhere and `current_config` / `secret_keys_set` are
    empty.
    """
    persona_id: str | None = None
    enabled_block: dict[str, dict[str, Any]] = {}
    secrets_root: Path | None = None
    if app is not None:
        persona_id = app.persona.data_dir.parent.name
        enabled_block = dict(app.persona.skills)
        secrets_root = app.persona.data_dir / "secrets"

    skills: list[dict[str, Any]] = []
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        skills.append(_build_one(ep, enabled_block, secrets_root))

    return {"persona": persona_id, "skills": skills}


def _build_one(
    ep: Any,
    enabled_block: dict[str, dict[str, Any]],
    secrets_root: Path | None,
) -> dict[str, Any]:
    name = ep.name
    package, version = _package_metadata(ep)
    description, author = _description_and_author(package)
    config_schema, data_schema_version = _class_metadata(ep)
    enabled = name in enabled_block
    current_config = dict(enabled_block.get(name, {}))
    secret_keys_set = _secret_keys(secrets_root, name)
    secret_required_keys = _required_secret_keys(config_schema)
    return {
        "name": name,
        "package": package,
        "version": version,
        "description": description,
        "author": author,
        "enabled": enabled,
        "config_schema": config_schema,
        "data_schema_version": data_schema_version,
        "current_config": current_config,
        "secret_keys_set": secret_keys_set,
        "secret_required_keys": secret_required_keys,
    }


def _package_metadata(ep: Any) -> tuple[str | None, str | None]:
    """Look up the dist that owns this entry point so the UI can show
    the PyPI package name + version.

    `entry_point.dist` is set when entry-points were enumerated through
    `importlib.metadata`; on some packaging shapes (editable installs,
    older importlib backports) the attribute is missing. Fall back to
    matching by module name when needed."""
    dist_name: str | None = None
    version: str | None = None
    dist = getattr(ep, "dist", None)
    if dist is not None:
        dist_name = getattr(dist, "name", None) or getattr(dist, "metadata", {}).get("Name")
        version = getattr(dist, "version", None)
    if dist_name and version:
        return dist_name, version
    # Fallback: derive from `value` ("module.path:Class") and resolve
    # the dist via importlib.metadata.distribution. Rare path.
    module = ep.value.split(":", 1)[0].split(".", 1)[0]
    candidate = module.replace("_", "-")
    with contextlib.suppress(PackageNotFoundError):
        d = distribution(candidate)
        return d.name, d.version
    return dist_name, version


def _description_and_author(
    package_name: str | None,
) -> tuple[str | None, str | None]:
    """Pull the PyPI ``Summary`` (description) + parsed author name
    from package metadata. Both fall back to ``None`` for packages
    whose metadata can't be located, which the PWA renders as ``—``.

    The author parser handles modern ``Author-email`` shape
    (``"Name <email>"``); falls back to the raw ``Author`` field when
    email isn't set. The email itself is **not** surfaced — only the
    parsed display name — so the wire frame doesn't leak contact info."""
    if not package_name:
        return None, None
    try:
        m = metadata(package_name)
    except PackageNotFoundError:
        return None, None
    summary = m.get("Summary")
    author_email = m.get("Author-email")
    author_plain = m.get("Author")
    # `Author-email` shape ``"Name <email>"``: extract just the name.
    # Email-only with no name part → ``None`` (we don't surface raw
    # contact info on the wire).
    author: str | None = None
    if author_email:
        match = _AUTHOR_NAME_RE.match(author_email)
        author = match.group(1) if match else None
    if author is None and author_plain:
        author = author_plain.strip() or None
    return summary, author


def _class_metadata(ep: Any) -> tuple[dict[str, Any] | None, int]:
    """Load the entry point's class and read its class-level metadata.

    `config_schema` and `data_schema_version` are documented as
    `ClassVar` on the Skill protocol; both are readable without
    instantiating the skill (no `setup()` call, no side effects).
    Failures load returning `(None, 1)` so a single broken skill
    doesn't prevent the panel from rendering the others."""
    try:
        cls = ep.load()
    except Exception:
        return None, 1
    schema = getattr(cls, "config_schema", None)
    version = getattr(cls, "data_schema_version", 1)
    schema_norm = schema if isinstance(schema, dict) else None
    version_norm = version if isinstance(version, int) else 1
    return schema_norm, version_norm


def _secret_keys(secrets_root: Path | None, skill_name: str) -> list[str]:
    """Return the JSON keys present in this skill's values.json (no values).

    Missing dir / missing file / unreadable file / malformed JSON all
    return `[]`. The UI surfaces "Set ✓" per key based on this list."""
    if secrets_root is None:
        return []
    path = secrets_root / skill_name / "values.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    return sorted(k for k in parsed if isinstance(k, str))


def _required_secret_keys(schema: dict[str, Any] | None) -> list[str]:
    """Walk the config_schema and pull out properties whose `format` is
    `"secret"`. The PWA renders these as masked-input fields with
    "Set ✓" / "Not set" affordances, distinct from plain config fields.

    Only the top level is inspected — nested objects with secret-typed
    leaves are out of scope until v2.x."""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    out: list[str] = []
    for key, spec in props.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("format") == "secret":
            out.append(key)
    return sorted(out)
