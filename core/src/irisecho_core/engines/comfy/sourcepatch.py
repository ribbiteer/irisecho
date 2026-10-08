# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply an upstream ComfyUI change, as a git diff, to the pinned ComfyUI source.

Strict on purpose: each file must be exactly the version the diff was made
against (its git blob hash, from the diff's `index` line), every hunk must
match without fuzz, and the result must hash to the diff's new version.
Anything else is an error, so a ComfyUI bump cannot half-apply a patch.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(RuntimeError):
    pass


@dataclass
class FilePatch:
    path: str
    old_blob: str
    new_blob: str
    hunks: list[tuple[int, list[str]]] = field(default_factory=list)


def blob_sha1(data: bytes) -> str:
    """The id git gives a file's content."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def parse(diff: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: list[str] | None = None
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            current, hunk = None, None
            continue
        if line.startswith("index "):
            old, new = line.split()[1].split("..")
            current = FilePatch(path="", old_blob=old, new_blob=new)
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            current.path = line[4:].removeprefix("b/")
            continue
        m = HUNK.match(line)
        if m:
            hunk = []
            current.hunks.append((int(m.group(1)), hunk))
            continue
        if hunk is not None and line[:1] in (" ", "+", "-"):
            hunk.append(line)
        elif hunk is not None and line.startswith("\\"):
            raise PatchError("diffs without a final newline are not supported")
    for f in files:
        if not f.path or not f.hunks:
            raise PatchError("malformed diff")
    return files


def apply_one(text: str, patch: FilePatch) -> str:
    lines = text.split("\n")
    out: list[str] = []
    pos = 0  # index into lines
    for start, body in patch.hunks:
        at = start - 1
        if at < pos:
            raise PatchError(f"{patch.path}: overlapping hunks")
        out.extend(lines[pos:at])
        pos = at
        for line in body:
            tag, content = line[0], line[1:]
            if tag == "+":
                out.append(content)
                continue
            if pos >= len(lines) or lines[pos] != content:
                raise PatchError(f"{patch.path}: hunk at line {start} does not match")
            if tag == " ":
                out.append(content)
            pos += 1
    out.extend(lines[pos:])
    return "\n".join(out)


def state(root: Path, diff: str) -> str:
    """'applied', 'pristine' (every file is the diff's base) or 'unknown'."""
    patches = parse(diff)
    blobs = [blob_sha1((root / p.path).read_bytes()) for p in patches]
    if all(b.startswith(p.new_blob) for b, p in zip(blobs, patches, strict=True)):
        return "applied"
    if all(b.startswith(p.old_blob) for b, p in zip(blobs, patches, strict=True)):
        return "pristine"
    return "unknown"


def apply(root: Path, diff: str) -> bool:
    """Apply the diff under root. Returns False if it was already applied."""
    patches = parse(diff)
    now = state(root, diff)
    if now == "applied":
        return False
    if now != "pristine":
        raise PatchError(
            "The ComfyUI files this patch changes are not the version it was made for."
        )
    results = {}
    for p in patches:
        new = apply_one((root / p.path).read_bytes().decode("utf-8"), p).encode("utf-8")
        if not blob_sha1(new).startswith(p.new_blob):
            raise PatchError(f"{p.path}: the patched file is not the expected version")
        results[p.path] = new
    for rel, data in results.items():  # all checked first: nothing is half-written
        (root / rel).write_bytes(data)
    return True
