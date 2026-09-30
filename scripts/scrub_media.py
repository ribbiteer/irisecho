#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strip metadata from PNG files in place. Standard library only.

ComfyUI writes the full prompt and workflow graph (model paths included) into
tEXt chunks of every PNG it saves. This removes all text, EXIF and timestamp
chunks and keeps the pixels byte-for-byte.

    python scripts/scrub_media.py docs/assets/*.png
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def scrub_png(data: bytes) -> tuple[bytes, list[str]]:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    out, dropped, i = [PNG_SIGNATURE], [], 8
    while i + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[i : i + 8])
        end = i + 12 + size
        if kind in DROP:
            dropped.append(kind.decode())
        else:
            out.append(data[i:end])
        i = end
        if kind == b"IEND":
            break
    return b"".join(out), dropped


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    status = 0
    for name in argv:
        path = Path(name)
        try:
            clean, dropped = scrub_png(path.read_bytes())
        except ValueError as e:
            print(f"{name}: skipped ({e}); convert it to PNG first", file=sys.stderr)
            status = 1
            continue
        if dropped:
            path.write_bytes(clean)
            print(f"{name}: removed {', '.join(dropped)}")
        else:
            print(f"{name}: clean")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
