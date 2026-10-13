# SPDX-License-Identifier: AGPL-3.0-or-later
"""The output file format a voice, clone, music or sound job asks for.

16-bit WAV is what these studios always wrote and stays the default; a job that
does not set `format` sends its worker exactly the request it always did.
The workers write the files (protocol/irisecho_audio.py `write_audio`).
"""

from __future__ import annotations

DEFAULT = "wav16"
EXTENSIONS = {"wav16": "wav", "wav24": "wav", "flac": "flac"}


def pick(params: dict) -> tuple[str, str, dict]:
    """(format, file extension, request additions) for a job.

    The additions are empty for the default format, so the worker request is unchanged.
    """
    fmt = params.get("format") or DEFAULT
    if fmt not in EXTENSIONS:
        raise ValueError(f"Unknown audio format {fmt}; use wav16, wav24 or flac.")
    return fmt, EXTENSIONS[fmt], ({} if fmt == DEFAULT else {"format": fmt})
