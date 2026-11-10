#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record the exact package versions of an engine environment that works.

    uv run python scripts/freeze_engine.py kokoro path/to/engines/kokoro/.venv

Writes core/src/irisecho_core/engines/<package>/constraints.txt. Installing the
engine then resolves against those versions, so a new release on PyPI cannot
change what a new install gets. Run it on an environment you have just used
successfully, after changing an engine's packages.

Packages installed from a URL are left out (the engine pins those itself), and
build tags such as +cu130 are dropped so the same file serves every CUDA
build and the CPU one.

ACE-Step and Stable Audio are installed from their projects' own uv.lock, which
already fixes every version. Each also makes one pip call next to the lock; for
those two (LOCKED) the file holds what that call can reach, the packages it
names and everything they depend on, and nothing else, so the lock stays the
one record of the rest on every platform it covers.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
ENGINES = ROOT / "core" / "src" / "irisecho_core" / "engines"
PACKAGES = {
    "kokoro": "kokoro",
    "chatterbox": "chatterbox",
    "prompt-writer": "promptwriter",
    "comfy": "comfy",
    "ace-step": "acestep",
    "stable-audio": "stableaudio",
}
# Engines synced from an upstream uv.lock: the packages their extra pip call names.
LOCKED = {
    "ace-step": ["soundfile"],
    "stable-audio": ["torch", "torchaudio"],
}
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;+]+)(\+\S+)?$")
TREE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*) v\S+")


def uv() -> str:
    from uv import find_uv_bin

    return find_uv_bin()


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def reachable(python: Path, roots: list[str]) -> set[str]:
    """The named packages and everything they depend on, as installed."""
    args = [uv(), "pip", "tree", "--python", str(python)]
    for root in roots:
        args += ["--package", root]
    out = subprocess.run(
        args, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL
    ).stdout
    names = {canonical(m.group(1)) for line in out.splitlines() if (m := TREE.search(line))}
    missing = {canonical(r) for r in roots} - names
    if missing:
        sys.exit(f"not installed in that environment: {', '.join(sorted(missing))}")
    return names


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in PACKAGES:
        sys.exit(f"usage: freeze_engine.py {{{','.join(PACKAGES)}}} path/to/.venv")
    engine, venv = sys.argv[1], Path(sys.argv[2])
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    out = subprocess.run(
        [uv(), "pip", "freeze", "--python", str(python)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    only = reachable(python, LOCKED[engine]) if engine in LOCKED else None
    pins = []
    for line in out.splitlines():
        m = PIN.match(line.strip())
        if m and (only is None or canonical(m.group(1)) in only):
            pins.append(f"{m.group(1).lower().replace('_', '-')}=={m.group(2)}")
    if not pins:
        sys.exit(f"no packages found in {venv}")
    target = ENGINES / PACKAGES[engine] / "constraints.txt"
    if only is None:
        what = f"# The {engine} engine's packages, exactly"
    else:
        what = (
            f"# What the {engine} engine installs next to its project's uv.lock\n"
            f"# ({', '.join(LOCKED[engine])}, with dependencies), exactly"
        )
    header = (
        f"{what} as in an environment known to work.\n"
        "# Written by scripts/freeze_engine.py; do not edit by hand.\n"
    )
    target.write_text(header + "\n".join(sorted(pins)) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {target.relative_to(ROOT)} ({len(pins)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
