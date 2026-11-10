# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where IrisEcho keeps its data on each platform.

Everything lives under one folder so removing it removes IrisEcho entirely:

    <data>/settings.json    user settings
    <data>/state.db         job history
    <data>/engines/<id>/    each engine's own environment and source
    <data>/models/          downloaded weights (relocatable in settings)
    <data>/outputs/         everything you make (relocatable in settings)
    <data>/logs/

Set IRISECHO_HOME to put it somewhere else (tests use this).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_NAME = "IrisEcho"


def data_dir() -> Path:
    override = os.environ.get("IRISECHO_HOME")
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        root = Path(base) / "irisecho"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# An upload's name: what POST /api/uploads returns, or a copy an agent put in the
# uploads folder itself (AGENTS.md). Nothing else is read from there.
UPLOAD_NAME = re.compile(r"[0-9a-f]{8,64}\.[a-z0-9]{2,5}")


def upload(name, what: str = "file") -> Path:
    """The path of the upload called `name`; ValueError, stating the rule, if it is no upload name.

    It does not check that the file exists. The rule keeps the name inside the uploads folder."""
    if not isinstance(name, str) or not UPLOAD_NAME.fullmatch(name):
        raise ValueError(
            f"Add a {what} first. An upload's name is lowercase hex digits (8 to 64) "
            "and its extension, like 3fa2b1c4.png."
        )
    return sub("uploads") / name


def move_aside(path: Path, why: str) -> Path | None:
    """Rename an unreadable data file to `<name>.bad` (then `.bad2`, ...) and log it.

    The caller carries on with defaults; the person's file is kept, not overwritten by
    the next save. Returns the new path, or None if it could not be moved.
    """
    target = path.with_name(path.name + ".bad")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.bad{n}")
    try:
        path.replace(target)
    except OSError:
        return None
    try:
        with (sub("logs") / "data.log").open("a", encoding="utf-8") as log:
            log.write(f"{path.name} could not be read ({why}); kept as {target.name}.\n")
    except OSError:
        pass
    return target
