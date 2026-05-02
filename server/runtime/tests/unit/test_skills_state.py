"""Tests for `huxley.skills_state.build_skills_state` — the payload
the runtime emits in response to the PWA's `get_skills_state` request.

Marketplace v2 Phase A is read-only by contract: the builder must
surface installed skills accurately, never expose secret VALUES, and
gracefully degrade when no persona is selected (lazy-boot window).
This file pins those invariants:

1. Lazy-boot (`app=None`) returns the entry-point list with empty
   enabled-state and no secrets.
2. Enabled state is read from `app.persona.skills`; `current_config`
   is the per-skill block from persona.yaml.
3. `secret_keys_set` lists JSON keys present in `values.json` and
   never their values.
4. `secret_required_keys` is derived from `config_schema.properties`
   where `format == "secret"`.
5. Class-level metadata (`config_schema`, `data_schema_version`) is
   read without instantiating the skill.
6. A malformed values.json doesn't crash the builder; the affected
   skill's `secret_keys_set` is `[]`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from huxley.skills_state import build_skills_state

if TYPE_CHECKING:
    from pathlib import Path


class _FakeEntryPoint:
    """Mimics `importlib.metadata.EntryPoint` enough for the builder.

    `dist` carries the package name + version the way real entry-points
    do. `load()` returns a class with the ClassVars the builder reads."""

    def __init__(
        self,
        name: str,
        cls: type,
        package: str,
        version: str,
    ) -> None:
        self.name = name
        self.value = f"{cls.__module__}:{cls.__name__}"
        self._cls = cls
        self.dist = SimpleNamespace(name=package, version=version)

    def load(self) -> type:
        return self._cls


class _FakeMetadata:
    """Stand-in for `importlib.metadata.PackageMetadata` exposing only
    the `.get()` keys the skills_state builder reads."""

    def __init__(
        self,
        summary: str | None = None,
        author: str | None = None,
        author_email: str | None = None,
    ) -> None:
        self._fields = {
            "Summary": summary,
            "Author": author,
            "Author-email": author_email,
        }

    def get(self, key: str) -> str | None:
        return self._fields.get(key)


class _StocksLike:
    """Stand-in for `huxley-skill-stocks` — declares config_schema with
    one secret + one plain enum + one array."""

    config_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "format": "secret"},
            "currency": {"type": "string", "enum": ["USD", "EUR", "GBP"]},
            "watchlist": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["api_key"],
    }
    data_schema_version: ClassVar[int] = 2


class _SearchLike:
    """Stand-in for `huxley-skill-search` — minimal schema, no secrets."""

    config_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "safesearch": {"type": "string", "enum": ["off", "moderate", "strict"]},
        },
    }
    data_schema_version: ClassVar[int] = 1


class _NoSchemaLike:
    """Skills without a config_schema (most pre-T1.14 skills)."""


def _fake_app(persona_dir: Path, skills_block: dict[str, dict[str, Any]]) -> Any:
    """Build a stub object exposing the slice of `Application` the
    builder touches: `persona.data_dir` + `persona.skills`."""
    return SimpleNamespace(
        persona=SimpleNamespace(data_dir=persona_dir, skills=skills_block),
    )


@pytest.fixture
def fake_eps(monkeypatch: pytest.MonkeyPatch) -> list[_FakeEntryPoint]:
    """Replace `huxley.skills_state.entry_points` and the per-package
    `metadata` lookup with deterministic fixtures so tests don't
    depend on what's installed in the active venv."""
    eps = [
        _FakeEntryPoint("stocks", _StocksLike, "huxley-skill-stocks", "0.1.0"),
        _FakeEntryPoint("search", _SearchLike, "huxley-skill-search", "0.1.0"),
        _FakeEntryPoint("plain", _NoSchemaLike, "huxley-skill-plain", "0.1.0"),
    ]
    meta_by_pkg = {
        "huxley-skill-stocks": _FakeMetadata(
            summary="Voice-controlled stock quotes via Alpha Vantage.",
            author_email="Mario Ruiz <mario@example.com>",
        ),
        "huxley-skill-search": _FakeMetadata(
            summary="DuckDuckGo web search, no API key needed.",
            author_email="Mario Ruiz <mario@example.com>",
        ),
        "huxley-skill-plain": _FakeMetadata(
            summary=None,
            author=None,
            author_email=None,
        ),
    }

    def _stub_eps(group: str) -> list[_FakeEntryPoint]:
        assert group == "huxley.skills"
        return eps

    def _stub_metadata(pkg: str) -> _FakeMetadata:
        if pkg in meta_by_pkg:
            return meta_by_pkg[pkg]
        from importlib.metadata import PackageNotFoundError

        raise PackageNotFoundError(pkg)

    monkeypatch.setattr("huxley.skills_state.entry_points", _stub_eps)
    monkeypatch.setattr("huxley.skills_state.metadata", _stub_metadata)
    return eps


def test_lazy_boot_returns_empty_persona_with_skills_listed(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    out = build_skills_state(None)
    assert out["persona"] is None
    assert {s["name"] for s in out["skills"]} == {"stocks", "search", "plain"}
    for skill in out["skills"]:
        assert skill["enabled"] is False
        assert skill["current_config"] == {}
        assert skill["secret_keys_set"] == []


def test_enabled_reflects_persona_skills_block(
    fake_eps: list[_FakeEntryPoint],
    tmp_path: Path,
) -> None:
    app = _fake_app(
        tmp_path,
        skills_block={"stocks": {"watchlist": ["AAPL"], "currency": "USD"}},
    )
    out = build_skills_state(app)
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stocks"]["enabled"] is True
    assert by_name["stocks"]["current_config"] == {
        "watchlist": ["AAPL"],
        "currency": "USD",
    }
    assert by_name["search"]["enabled"] is False
    assert by_name["search"]["current_config"] == {}


def test_secret_keys_set_lists_keys_not_values(
    fake_eps: list[_FakeEntryPoint],
    tmp_path: Path,
) -> None:
    secrets_dir = tmp_path / "secrets" / "stocks"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "values.json").write_text(
        json.dumps({"api_key": "sk-VERYSECRET", "extra": "alsosecret"}),
        encoding="utf-8",
    )
    app = _fake_app(tmp_path, skills_block={"stocks": {}})
    out = build_skills_state(app)
    stocks = next(s for s in out["skills"] if s["name"] == "stocks")
    assert stocks["secret_keys_set"] == ["api_key", "extra"]
    # The secret VALUE must never appear anywhere in the payload.
    serialized = json.dumps(out)
    assert "VERYSECRET" not in serialized
    assert "alsosecret" not in serialized


def test_secret_required_keys_pulls_from_config_schema(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    out = build_skills_state(None)
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stocks"]["secret_required_keys"] == ["api_key"]
    assert by_name["search"]["secret_required_keys"] == []
    assert by_name["plain"]["secret_required_keys"] == []


def test_class_level_metadata_read_without_instantiation(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    out = build_skills_state(None)
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stocks"]["data_schema_version"] == 2
    assert by_name["search"]["data_schema_version"] == 1
    assert by_name["plain"]["data_schema_version"] == 1
    assert by_name["plain"]["config_schema"] is None


def test_malformed_values_json_does_not_crash(
    fake_eps: list[_FakeEntryPoint],
    tmp_path: Path,
) -> None:
    secrets_dir = tmp_path / "secrets" / "stocks"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "values.json").write_text("{not valid json", encoding="utf-8")
    app = _fake_app(tmp_path, skills_block={"stocks": {}})
    out = build_skills_state(app)
    stocks = next(s for s in out["skills"] if s["name"] == "stocks")
    assert stocks["secret_keys_set"] == []
    # The other skills still render cleanly.
    assert {s["name"] for s in out["skills"]} == {"stocks", "search", "plain"}


def test_package_metadata_round_trips_from_entry_point(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    out = build_skills_state(None)
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stocks"]["package"] == "huxley-skill-stocks"
    assert by_name["stocks"]["version"] == "0.1.0"
    assert by_name["search"]["package"] == "huxley-skill-search"


def test_persona_field_is_directory_basename(
    fake_eps: list[_FakeEntryPoint],
    tmp_path: Path,
) -> None:
    """The on-wire `persona` field is the directory basename (canonical
    id), not `PersonaSpec.name` (display label) — same lock-down as
    hello extras `current_persona`. See decisions.md 2026-05-01."""
    persona_dir = tmp_path / "abuelos" / "data"
    persona_dir.mkdir(parents=True)
    app = _fake_app(persona_dir, skills_block={})
    out = build_skills_state(app)
    assert out["persona"] == "abuelos"


def test_skills_listed_in_sorted_name_order(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    out = build_skills_state(None)
    names = [s["name"] for s in out["skills"]]
    assert names == sorted(names)


def test_description_pulled_from_pypi_summary(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    out = build_skills_state(None)
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stocks"]["description"] == "Voice-controlled stock quotes via Alpha Vantage."
    assert by_name["search"]["description"] == "DuckDuckGo web search, no API key needed."
    assert by_name["plain"]["description"] is None


def test_author_parsed_from_author_email_field(
    fake_eps: list[_FakeEntryPoint],
) -> None:
    """`Author-email` is the modern field; we extract just the name
    portion so the email itself never reaches the wire."""
    out = build_skills_state(None)
    by_name = {s["name"]: s for s in out["skills"]}
    assert by_name["stocks"]["author"] == "Mario Ruiz"
    # Author-email NEVER appears anywhere in the payload
    serialized = json.dumps(out)
    assert "@example.com" not in serialized
    # Plain has no author at all
    assert by_name["plain"]["author"] is None


def test_author_falls_back_to_author_field_when_email_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eps = [_FakeEntryPoint("legacy", _NoSchemaLike, "legacy-pkg", "1.0.0")]
    monkeypatch.setattr(
        "huxley.skills_state.entry_points",
        lambda group: eps if group == "huxley.skills" else [],
    )
    monkeypatch.setattr(
        "huxley.skills_state.metadata",
        lambda pkg: _FakeMetadata(summary=None, author="Jane Doe"),
    )
    out = build_skills_state(None)
    assert out["skills"][0]["author"] == "Jane Doe"


def test_author_email_with_no_name_part_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `Author-email` is just an email (no name), surface None
    rather than leak the email address."""
    eps = [_FakeEntryPoint("anon", _NoSchemaLike, "anon-pkg", "1.0.0")]
    monkeypatch.setattr(
        "huxley.skills_state.entry_points",
        lambda group: eps if group == "huxley.skills" else [],
    )
    monkeypatch.setattr(
        "huxley.skills_state.metadata",
        lambda pkg: _FakeMetadata(author_email="bare@example.com"),
    )
    out = build_skills_state(None)
    assert out["skills"][0]["author"] is None
    serialized = json.dumps(out)
    assert "bare@example.com" not in serialized
