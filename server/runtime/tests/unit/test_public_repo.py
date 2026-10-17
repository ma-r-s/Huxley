"""The repo is public: no real phone number in a tracked file.

Example numbers must look fake: a run of six equal digits after the country
code (+57 300 000 0001) or a run like 123456 or 987654. Real contacts live in
each persona's gitignored data/contacts.json; the Telegram skill merges them
over persona.yaml (docs/skills/telegram.md).

Also caught since 2026-10-17, when each had leaked: a Colombian mobile
without the "+" (573..., 3xx xxx xxxx), one spelled out in Spanish words (a
docstring held his real number read aloud), and a Telegram user or chat id
(a watch test held the owner's). The history was purged the same day.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]  # server/runtime/tests/unit -> repo root
PHONE = re.compile(r"\+\d{1,3}[ -]?\d{2,4}[ -]?\d{3}[ -]?\d{3,4}")
BINARY = (".lock", ".svg", ".glb", ".zip", ".png", ".jpg", ".wav", ".mp3", ".stl", ".3mf", ".pdf")
# Generated: build hashes that look like mobiles.
SKIP = (".tsbuildinfo",)
BARE = re.compile(r"(?<![\d.])(?:\+?57[ -]?)?3\d{2}[ -]?\d{3}[ -]?\d{4}(?![\d.])")
IDS = re.compile(r"(?i)\b(?:user_id|chat_id|peer_id|telegram_id)\b[\"']?\s*[=:]\s*[\"']?(\d{6,})")
_WORDS = {
    "cero": "0",
    "uno": "1",
    "dos": "2",
    "tres": "3",
    "cuatro": "4",
    "cinco": "5",
    "seis": "6",
    "siete": "7",
    "ocho": "8",
    "nueve": "9",
}
_DIGIT = "|".join(_WORDS)
SPELLED = re.compile(rf"\b(?:(?:{_DIGIT})[\s,]+){{6,}}(?:{_DIGIT})\b")
RUNS = ("123456", "234567", "345678", "456789", "987654", "876543")


def _looks_fake(digits: str) -> bool:
    body = digits[2:]
    return bool(
        re.search(r"(\d)\1{5}", body)
        or any(run in body for run in RUNS)
        or re.search(r"555\d{4}$", digits)
    )


def _plain_fake(digits: str) -> bool:
    """For numbers without a "+": the same test, on the national part."""
    body = digits[2:] if len(digits) == 12 and digits.startswith("57") else digits
    return bool(re.search(r"(\d)\1{4}", body) or any(run in body for run in RUNS))


def leaks(text: str) -> list[str]:
    """What in `text` looks like a real number or id, masked."""
    found = []
    for match in BARE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if not _plain_fake(digits):
            found.append(f"mobile {digits[:3]}...")
    for match in IDS.finditer(text):
        if not _plain_fake(match.group(1)):
            found.append(f"telegram id {match.group(1)[:3]}...")
    for match in SPELLED.finditer(text):
        digits = "".join(_WORDS[w] for w in re.findall(r"[a-z]+", match.group(0)))
        if not _plain_fake(digits):
            found.append(f"spelled number {digits[:3]}...")
    return found


def _tracked() -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [n for n in listed.stdout.split() if not n.endswith(BINARY + SKIP)]


def test_no_bare_spelled_numbers_or_telegram_ids_in_tracked_files() -> None:
    found = []
    for name in _tracked():
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += [f"{name}: {what}" for what in leaks(text)]
    assert found == [], f"real-looking numbers or ids in tracked files: {found}"


# Built from pieces so no real-looking number sits in this file.
_SAMPLE = "31" + "47" + "92" + "58" + "06"


@pytest.mark.parametrize(
    "text",
    [
        f"call {_SAMPLE} now",
        f"+57{_SAMPLE}",
        f"57 {_SAMPLE[:3]} {_SAMPLE[3:6]} {_SAMPLE[6:]}",
        "user_id=" + "73" + "91" + "45" + "28" + "60",
        " ".join(["tres", "uno", "cuatro,", "siete", "nueve", "dos,", "cinco", "ocho", "cero"]),
    ],
)
def test_the_guard_catches_each_leak_shape(text: str) -> None:
    assert leaks(text)


@pytest.mark.parametrize(
    "text",
    ["+573000000073", "3000000076", "user_id=1000000081", "tres cero cero, cero cero cero, cero"],
)
def test_the_guard_passes_fakes(text: str) -> None:
    assert leaks(text) == []


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
