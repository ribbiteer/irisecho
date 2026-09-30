#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time setup for a fresh clone: hooks, commit identity check, denylist.

Run from the repo root:  python scripts/dev_setup.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from identity_scan import NOREPLY_EMAIL, default_denylist_path  # noqa: E402

DENYLIST_TEMPLATE = """\
# Private identity denylist for the IrisEcho identity scan.
# This file must stay OUTSIDE the repository. One regex per line,
# matched case-insensitively. Lines starting with # are comments.
#
# Add anything that points at you: real name, other handles, email local
# parts, machine names, drive/folder names, private project names.
#
# \\bmy-real-name\\b
# my-other-handle
"""


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip()


def main() -> int:
    if not Path(".git").exists():
        print("Run this from the repository root.", file=sys.stderr)
        return 1

    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], check=True)
    print("hooks: core.hooksPath -> .githooks")

    email = git("config", "user.email")
    if NOREPLY_EMAIL.search(email or ""):
        print("identity: commit email is a GitHub noreply address")
    else:
        print(
            "identity: set a repo-local noreply identity before committing:\n"
            '  git config user.name "<github-handle>"\n'
            '  git config user.email "<id>+<github-handle>@users.noreply.github.com"\n'
            "  (the exact address is under GitHub > Settings > Emails)"
        )

    path = default_denylist_path()
    if path.exists():
        n = sum(
            1
            for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip() and not x.lstrip().startswith("#")
        )
        print(f"denylist: {path} ({n} rule(s))")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DENYLIST_TEMPLATE, encoding="utf-8")
        print(f"denylist: created template at {path}; add your own strings")

    try:
        repo = Path.cwd().resolve()
        path.resolve().relative_to(repo)
        print("denylist: ERROR, it is inside the repository; move it out", file=sys.stderr)
        return 1
    except ValueError:
        pass

    if not re.search(r"\+0000$", git("var", "GIT_AUTHOR_IDENT")):
        print("timezone: commits will carry your UTC offset; set TZ=UTC in your shell to hide it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
