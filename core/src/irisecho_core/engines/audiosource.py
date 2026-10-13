# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recording a music or sound job starts from: the uploaded file and the part of it to change.

Both are optional; a job without them is the plain text-to-audio job it always was.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from irisecho_core import paths

# An upload's name, or a copy an agent put in the uploads folder itself (AGENTS.md).
NAME = re.compile(r"[0-9a-f]{8,64}\.[a-z0-9]{2,5}")
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}


def upload_path(name, what: str = "recording") -> Path:
    """The uploaded audio file called `name`, or a message saying what to do."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(f"Add a {what} first.")
    if Path(name).suffix not in AUDIO_EXTENSIONS:
        raise ValueError(f"The {what} must be a WAV, MP3, FLAC or OGG file.")
    path = paths.sub("uploads") / name
    if not path.is_file():
        raise ValueError(f"That {what} is no longer available; add it again.")
    return path


def seconds(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{label} must be a number of seconds.")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"{label} must be a number of seconds.") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a number of seconds.")
    return number


def region(value, longest: float = 600.0) -> tuple[float, float]:
    """(start, end) in seconds from a two-number list; the end must come after the start."""
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("Give the part to change as a start and an end, in seconds.")
    start, end = seconds(value[0], "The start"), seconds(value[1], "The end")
    if start < 0:
        raise ValueError("The start of the part to change cannot be before the beginning.")
    if end <= start:
        raise ValueError("The end of the part to change must come after its start.")
    if end > longest:
        raise ValueError(f"The part to change cannot reach past {longest:g} seconds.")
    return round(start, 3), round(end, 3)
