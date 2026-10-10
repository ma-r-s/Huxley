"""`huxley` loads `.env` from the directory it was started in.

Regression: the bare `load_dotenv()` walked up from the package's own
directory, so in a source checkout every instance picked up the repo's
`server/runtime/.env` (persona, API key) regardless of its working
directory. docs/architecture.md's one-directory-per-human layout relies
on the cwd's `.env` winning.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

import huxley.__main__ as entry

if TYPE_CHECKING:
    from pathlib import Path


def test_dotenv_comes_from_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("HUXLEY_PERSONA=from_cwd\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    seen: dict[str, str | None] = {}

    class _StopAfterEnvError(Exception):
        pass

    def fake_settings() -> None:
        seen["persona"] = os.environ.get("HUXLEY_PERSONA")
        raise _StopAfterEnvError

    monkeypatch.setattr(entry, "Settings", fake_settings)
    # load_dotenv writes straight into os.environ, outside monkeypatch's
    # bookkeeping, so save and restore by hand (monkeypatch.delenv after
    # the fact would "restore" the leaked value at teardown).
    prev = os.environ.pop("HUXLEY_PERSONA", None)
    try:
        with pytest.raises(SystemExit):
            entry.main()
    finally:
        os.environ.pop("HUXLEY_PERSONA", None)
        if prev is not None:
            os.environ["HUXLEY_PERSONA"] = prev

    assert seen["persona"] == "from_cwd"
