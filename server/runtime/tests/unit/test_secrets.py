"""Tests for `JsonFileSecrets` — the per-skill secrets store backing the
SDK's `SkillSecrets` Protocol.

The on-disk shape and read semantics are pinned in
`docs/skill-marketplace.md` § Secrets storage layout. These tests are
the regression net for that contract — when T1.14 ships and skills
start adopting `ctx.secrets.set/get`, the round-trip must match what
the spec promises.
"""

from __future__ import annotations

import asyncio
import json
import stat
from typing import TYPE_CHECKING

import pytest

from huxley.storage.secrets import JsonFileSecrets

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_get_missing_key_returns_none(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path)
    assert await secrets.get("api_key") is None


@pytest.mark.asyncio
async def test_get_missing_file_returns_none(tmp_path: Path) -> None:
    # Directory exists, values.json doesn't.
    secrets = JsonFileSecrets(tmp_path)
    assert await secrets.keys() == []
    assert await secrets.get("api_key") is None


@pytest.mark.asyncio
async def test_get_missing_dir_returns_none(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path / "does_not_exist")
    assert await secrets.get("api_key") is None
    assert await secrets.keys() == []


@pytest.mark.asyncio
async def test_set_creates_file_with_secure_perms(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path / "skill_x")
    await secrets.set("api_key", "sk-abc123")
    values_path = tmp_path / "skill_x" / "values.json"
    assert values_path.exists()
    # 0o700 dir, 0o600 file. (Skip the assertion on platforms where
    # chmod silently no-ops; the with-suppress is intentional.)
    dir_mode = stat.S_IMODE((tmp_path / "skill_x").stat().st_mode)
    file_mode = stat.S_IMODE(values_path.stat().st_mode)
    assert dir_mode == 0o700
    assert file_mode == 0o600
    assert json.loads(values_path.read_text()) == {"api_key": "sk-abc123"}


@pytest.mark.asyncio
async def test_set_then_get_round_trip(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path)
    await secrets.set("api_key", "sk-abc123")
    await secrets.set("client_id", "8a3c")
    assert await secrets.get("api_key") == "sk-abc123"
    assert await secrets.get("client_id") == "8a3c"
    assert sorted(await secrets.keys()) == ["api_key", "client_id"]


@pytest.mark.asyncio
async def test_set_overwrites(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path)
    await secrets.set("api_key", "first")
    await secrets.set("api_key", "second")
    assert await secrets.get("api_key") == "second"


@pytest.mark.asyncio
async def test_delete_removes_key(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path)
    await secrets.set("api_key", "sk-abc")
    await secrets.delete("api_key")
    assert await secrets.get("api_key") is None


@pytest.mark.asyncio
async def test_delete_missing_key_is_noop(tmp_path: Path) -> None:
    secrets = JsonFileSecrets(tmp_path)
    await secrets.delete("nonexistent")  # must not raise
    assert await secrets.keys() == []


@pytest.mark.asyncio
async def test_malformed_json_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "values.json").write_text("{not valid json")
    secrets = JsonFileSecrets(tmp_path)
    assert await secrets.get("api_key") is None
    assert await secrets.keys() == []


@pytest.mark.asyncio
async def test_non_dict_json_returns_empty(tmp_path: Path) -> None:
    # Array, scalar, null all coerce to "no creds" rather than crash.
    (tmp_path / "values.json").write_text(json.dumps(["api_key", "value"]))
    secrets = JsonFileSecrets(tmp_path)
    assert await secrets.get("api_key") is None


@pytest.mark.asyncio
async def test_nested_dict_value_is_json_encoded(tmp_path: Path) -> None:
    # OAuth-blob convention from docs/skill-marketplace.md § Secrets
    # storage layout: a hand-edited values.json with a nested literal
    # dict gets json.dumps-encoded on read so callers can json.loads it
    # back. Matches the round-trip with set("oauth_state", json.dumps(d)).
    (tmp_path / "values.json").write_text(
        json.dumps(
            {
                "api_key": "sk-abc",
                "oauth_state": {"access_token": "xyz", "expires_at": 1735689600},
                "scopes": ["read", "write"],
            }
        )
    )
    secrets = JsonFileSecrets(tmp_path)
    assert await secrets.get("api_key") == "sk-abc"
    raw_oauth = await secrets.get("oauth_state")
    assert raw_oauth is not None
    assert json.loads(raw_oauth) == {
        "access_token": "xyz",
        "expires_at": 1735689600,
    }
    raw_scopes = await secrets.get("scopes")
    assert raw_scopes is not None
    assert json.loads(raw_scopes) == ["read", "write"]


@pytest.mark.asyncio
async def test_nested_value_set_via_string_round_trips(tmp_path: Path) -> None:
    # The set/get round-trip for nested data uses json.dumps + json.loads
    # at the call site. Verify the on-disk bytes match what a hand-
    # editor would have written.
    secrets = JsonFileSecrets(tmp_path)
    blob = {"access_token": "abc", "expires_at": 100}
    await secrets.set("oauth_state", json.dumps(blob))
    raw = await secrets.get("oauth_state")
    assert raw is not None
    assert json.loads(raw) == blob


@pytest.mark.asyncio
async def test_concurrent_writes_dont_tear(tmp_path: Path) -> None:
    # Two coroutines racing to write different keys must both land —
    # the asyncio.Lock around set/delete is the correctness guarantee.
    secrets = JsonFileSecrets(tmp_path)
    await asyncio.gather(
        *(secrets.set(f"k{i}", f"v{i}") for i in range(20)),
    )
    keys = await secrets.keys()
    assert len(keys) == 20
    for i in range(20):
        assert await secrets.get(f"k{i}") == f"v{i}"


@pytest.mark.asyncio
async def test_existing_dir_perms_get_locked_down_on_write(tmp_path: Path) -> None:
    # If the secrets dir was created loose (e.g., by a tarball extract
    # with default umask), the first set() must lock it back to 0o700.
    secrets_dir = tmp_path / "skill_y"
    secrets_dir.mkdir(mode=0o755)
    secrets = JsonFileSecrets(secrets_dir)
    await secrets.set("api_key", "sk-abc")
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700
