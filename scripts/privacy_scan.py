"""Scan the commits about to be pushed for real phone numbers and ids.

The public-repo test (server/runtime/tests/unit/test_public_repo.py) reads
only today's files; a number added in one commit and removed in the next
still reaches GitHub (2026-10-17: his real number sat in an unpushed
docstring, and the history had to be rewritten). Run before every push:

    uv run python scripts/privacy_scan.py              # origin/main..HEAD
    uv run python scripts/privacy_scan.py A..B         # any range

Exit 1 with the commits and masked hits when something looks real.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "public_repo_guard", ROOT / "server/runtime/tests/unit/test_public_repo.py"
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def scan(rev_range: str) -> list[str]:
    out = subprocess.run(
        ["git", "log", "-p", "--format=COMMIT %h%n%B", rev_range],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
    ).stdout
    found: list[str] = []
    commit = "?"
    skip = False
    for line in out.splitlines():
        if line.startswith("COMMIT "):
            commit = line.split()[1]
            continue
        if line.startswith("+++ "):
            skip = line.endswith(guard.BINARY + guard.SKIP)
            continue
        if skip or line.startswith(("---", "-", " ", "@@", "diff ", "index ")):
            continue
        text = line[1:] if line.startswith("+") else line  # added lines and messages
        hits = list(guard.leaks(text))
        for m in guard.PHONE.finditer(text):
            if not guard._looks_fake(re.sub(r"\D", "", m.group(0))):
                hits.append(f"phone {m.group(0)[:4]}...")
        found += [f"{commit}: {hit}" for hit in hits]
    return found


def main() -> int:
    rev_range = sys.argv[1] if len(sys.argv) > 1 else "origin/main..HEAD"
    found = scan(rev_range)
    if found:
        print(f"privacy_scan: {len(found)} real-looking number(s) in {rev_range}:")
        print("\n".join(f"  {f}" for f in found))
        return 1
    print(f"privacy_scan: {rev_range} clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
