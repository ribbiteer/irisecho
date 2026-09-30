# SPDX-License-Identifier: AGPL-3.0-or-later
"""Source files must not contain stray control characters (NUL, backspace, ...)."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (".py", ".ts", ".svelte", ".rs", ".json", ".yaml", ".toml", ".md", ".html", ".css")
ALLOWED = {9, 10, 13}


def tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout
    return [ROOT / p for p in out.decode().split("\0") if p.endswith(TEXT)]


def test_no_control_characters():
    bad = []
    for path in tracked():
        if not path.is_file():
            continue
        data = path.read_bytes()
        if any(b < 32 and b not in ALLOWED for b in data):
            bad.append(str(path.relative_to(ROOT)))
    assert not bad, f"control characters in: {bad}"
