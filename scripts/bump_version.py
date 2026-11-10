#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Set IrisEcho's version everywhere it is recorded, in one step.

    uv run python scripts/bump_version.py 0.1.3
    uv run python scripts/bump_version.py --check
    uv run python scripts/bump_version.py --check --tag v0.1.3

The version lives in the Python package, the desktop shell and both npm
packages, and in their lock files. With --check, exits non-zero if they
disagree (uv.lock included) instead of changing anything; with --tag as well,
also unless they all are the version that tag names, which is how the release
build refuses a tag pushed on the wrong commit.
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


# uv.lock records the core's version as well. `uv lock` rewrites it after a bump;
# it is only read here.
UV_LOCK = ("uv.lock", r'name = "irisecho-core"\nversion = "([^"]+)"')


def found(root: Path = ROOT) -> dict[str, str]:
    out = {}
    for i, (rel, pattern) in enumerate([*PLACES, UV_LOCK]):
        path = root / rel
        m = re.search(pattern, path.read_text(encoding="utf-8")) if path.is_file() else None
        out[f"{rel} ({i})"] = m.group(1) if m else "(missing)"
    return out


def write(version: str, root: Path = ROOT) -> None:
    """Put `version` in every place, or in none: every file is read and every
    pattern found before the first file is written."""
    texts: dict[str, str] = {}
    missing = []
    for rel, pattern in PLACES:
        if rel not in texts:
            try:
                texts[rel] = (root / rel).read_text(encoding="utf-8")
            except OSError:
                missing.append(rel)
                continue
        m = re.search(pattern, texts[rel])
        if not m:
            missing.append(rel)
            continue
        texts[rel] = texts[rel][: m.start(1)] + version + texts[rel][m.end(1) :]
    if missing:
        sys.exit(f"no version found in {', '.join(dict.fromkeys(missing))}; nothing was changed")
    for rel, text in texts.items():
        (root / rel).write_text(text, encoding="utf-8", newline="\n")


def check(tag: str | None = None, root: Path = ROOT) -> int:
    versions = found(root)
    for where, v in versions.items():
        print(f"{v:10} {where}")
    seen = set(versions.values())
    if len(seen) != 1 or "(missing)" in seen:
        print("the version is not the same everywhere", file=sys.stderr)
        return 1
    if tag is not None and tag != f"v{seen.pop()}":
        print(f"the tag {tag} does not name that version", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tag", help="with --check: the release tag (vX.Y.Z) the version must match")
    args = ap.parse_args(argv)
    if args.check or not args.version:
        return check(args.tag)
    if not VERSION.match(args.version):
        sys.exit("give a version like 0.1.3")
    write(args.version)
    # uv.lock records the core's version too; let uv rewrite it rather than editing it here.
    subprocess.run(["uv", "lock", "-q"], cwd=ROOT, check=True, stdin=subprocess.DEVNULL)
    versions = found()
    if set(versions.values()) != {args.version}:
        sys.exit(f"some files did not take the new version: {versions}")
    print(f"IrisEcho is now {args.version} in {len(PLACES)} places, plus uv.lock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
