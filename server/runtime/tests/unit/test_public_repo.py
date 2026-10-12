"""The repo is public: no real phone number in a tracked file.

Example numbers must look fake: a run of six equal digits after the country
code (+57 300 000 0001) or a run like 123456 or 987654. Real contacts live in
each persona's gitignored data/contacts.json; the Telegram skill merges them
over persona.yaml (docs/skills/telegram.md).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]  # server/runtime/tests/unit -> repo root
PHONE = re.compile(r"\+\d{1,3}[ -]?\d{2,4}[ -]?\d{3}[ -]?\d{3,4}")
BINARY = (".lock", ".svg", ".glb", ".zip", ".png", ".jpg", ".wav", ".mp3", ".stl", ".3mf", ".pdf")
RUNS = ("123456", "234567", "345678", "456789", "987654", "876543")


def _looks_fake(digits: str) -> bool:
    body = digits[2:]
    return bool(
        re.search(r"(\d)\1{5}", body)
        or any(run in body for run in RUNS)
        or re.search(r"555\d{4}$", digits)
    )


def test_no_real_phone_numbers_in_tracked_files() -> None:
    try:
        listed = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    found = []
    for name in listed.stdout.split():
        if name.endswith(BINARY):
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in PHONE.finditer(text):
            if not _looks_fake(re.sub(r"\D", "", match.group(0))):
                found.append(f"{name}: {match.group(0)[:4]}...")
    assert found == [], (
        "real-looking phone numbers in tracked files (use +57 300 000 0001 style "
        f"examples; real ones go in the persona's data/contacts.json): {found}"
    )
