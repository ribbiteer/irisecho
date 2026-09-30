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
