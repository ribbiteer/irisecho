# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guide for scripts and coding agents that want to use this IrisEcho.

The text ships inside the package (AGENTS.md). `irisecho agents` prints it, the
app serves it at /agents.md, and every start writes a copy into the data folder
with this installation's own command line filled in, so an agent pointed at the
folder finds out how to drive the program without being told.
"""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

from irisecho_core import paths

NAME = "AGENTS.md"


def cli_command() -> str:
    """How to run the command line of this installation, ready to paste into a shell."""
    launcher = Path(sys.executable).with_name(
        "irisecho.exe" if sys.platform == "win32" else "irisecho"
    )
    if launcher.is_file():
        return f'"{launcher}"' if " " in str(launcher) else str(launcher)
    return f'"{sys.executable}" -m irisecho_core'


def text() -> str:
    template = (Path(__file__).parent / NAME).read_text(encoding="utf-8")
    return Template(template).safe_substitute(cli=cli_command(), data=paths.data_dir())


def write() -> Path:
    """Put this installation's copy of the guide in the data folder."""
    path = paths.data_dir() / NAME
    path.write_text(text(), encoding="utf-8")
    return path
