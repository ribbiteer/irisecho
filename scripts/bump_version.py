#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Set IrisEcho's version everywhere it is recorded, in one step.

    uv run python scripts/bump_version.py 0.1.3
    uv run python scripts/bump_version.py --check

The version lives in the Python package, the desktop shell and both npm
packages, and in their lock files. With --check, exits non-zero if they
disagree instead of changing anything.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
LOCK_ROOT = r'"": \{\n      "name": "[^"]+",\n      "version": "([^"]+)"'

# (file, pattern with one group around the version). Only that text is replaced,
# so each file keeps its exact formatting.
PLACES = [
    ("core/pyproject.toml", r'(?m)^version = "([^"]+)"'),
    ("core/src/irisecho_core/__init__.py", r'(?m)^__version__ = "([^"]+)"'),
    ("desktop/src-tauri/Cargo.toml", r'(?m)^version = "([^"]+)"'),
    ("desktop/src-tauri/Cargo.lock", r'name = "irisecho"\nversion = "([^"]+)"'),
    ("desktop/src-tauri/tauri.conf.json", r'(?m)^  "version": "([^"]+)"'),
    ("ui/package.json", r'(?m)^  "version": "([^"]+)"'),
    ("desktop/package.json", r'(?m)^  "version": "([^"]+)"'),
    ("ui/package-lock.json", r'(?m)^  "version": "([^"]+)"'),
    ("ui/package-lock.json", LOCK_ROOT),
    ("desktop/package-lock.json", r'(?m)^  "version": "([^"]+)"'),
    ("desktop/package-lock.json", LOCK_ROOT),
]


def found() -> dict[str, str]:
    out = {}
    for i, (rel, pattern) in enumerate(PLACES):
        m = re.search(pattern, (ROOT / rel).read_text(encoding="utf-8"))
        out[f"{rel} ({i})"] = m.group(1) if m else "(missing)"
    return out


def write(version: str) -> None:
    for rel, pattern in PLACES:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        m = re.search(pattern, text)
        if not m:
            sys.exit(f"no version found in {rel}")
        new = text[: m.start(1)] + version + text[m.end(1) :]
        p.write_text(new, encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check or not args.version:
        versions = found()
        for where, v in versions.items():
            print(f"{v:10} {where}")
        return 0 if len(set(versions.values())) == 1 else 1
    if not VERSION.match(args.version):
        sys.exit("give a version like 0.1.3")
    write(args.version)
    # uv.lock records the core's version too; let uv rewrite it rather than editing it here.
    subprocess.run(["uv", "lock", "-q"], cwd=ROOT, check=True)
    versions = found()
    if set(versions.values()) != {args.version}:
        sys.exit(f"some files did not take the new version: {versions}")
    print(f"IrisEcho is now {args.version} in {len(versions)} places, plus uv.lock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
