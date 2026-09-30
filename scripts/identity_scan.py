#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse to publish anything that says who, or where, the developer is.

Runs as a git hook (pre-commit, commit-msg) and in CI. Standard library only.

Two sets of rules:

* Built-in, generic: home-directory paths, private LAN addresses, default
  Windows hostnames, non-noreply email addresses, access tokens, and metadata
  blocks in PNG/JPEG/WebP files (ComfyUI writes its whole workflow, file paths
  included, into every PNG it saves).
* A private denylist of the developer's own identifying strings, one regex per
  line. It lives OUTSIDE the repo, at $IRISECHO_DENYLIST or
  ~/.config/irisecho/identity-denylist.txt, so the list itself is never
  published. CI reads it from a repository secret.

Findings print as `path:line: rule`, never the matched text, so a CI log can
not leak what it caught. Pass --show locally to see the match.

A line containing `identity-scan: allow` is skipped.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "identity-scan: allow"

EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?!(?:png|jpe?g|webp|gif|svg|ico|icns|json|js|ts|css|html?"
    r"|md|txt|pdf|wav|mp3|mp4)\b)[A-Za-z]{2,}\b"
)
ALLOWED_EMAIL = re.compile(
    r"(?:@users\.noreply\.github\.com"
    r"|^noreply@github\.com"
    r"|^noreply@anthropic\.com"
    r"|^git@github\.com"
    r"|@example\.(?:com|org|net))$",
    re.IGNORECASE,
)
NOREPLY_EMAIL = re.compile(r"@users\.noreply\.github\.com$", re.IGNORECASE)

RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "user-home-path",
        re.compile(
            r"\b[a-z]:[\\/]+(?:users|documents and settings)[\\/]+"
            r"(?!(?:public|default|runneradmin|all users)\b)[^\\/\s\"'<>{}$%]+",
            re.IGNORECASE,
        ),
    ),
    (
        "posix-home-path",
        re.compile(r"(?<![\w.~])/(?:Users|home)/(?!(?:runner|Shared|user|username|you)\b)[\w.-]+"),
    ),
    (
        "private-ipv4",
        re.compile(
            r"\b(?:10(?:\.\d{1,3}){3}"
            r"|192\.168(?:\.\d{1,3}){2}"
            r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
        ),
    ),
    ("windows-hostname", re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Z0-9]{7}\b")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{50,})\b")),
]

# Rules that are also meaningful inside binary files.
BINARY_RULES = {"user-home-path", "posix-home-path", "windows-hostname"}

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".icns",
    ".bmp",
    ".tif",
    ".tiff",
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".mp4",
    ".webm",
    ".mov",
    ".pdf",
    ".zip",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".exe",
    ".dll",
    ".dylib",
    ".so",
}


@dataclass(frozen=True)
class Finding:
    where: str
    line: int
    rule: str
    match: str

    def render(self, show: bool) -> str:
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"{loc}: {self.rule}" + (f"  [{self.match}]" if show else "")


def default_denylist_path() -> Path:
    env = os.environ.get("IRISECHO_DENYLIST")
    if env:
        return Path(env)
    return Path.home() / ".config" / "irisecho" / "identity-denylist.txt"


def load_denylist(path: Path) -> list[tuple[str, re.Pattern[str]]]:
    rules = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pattern = re.compile(line, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(line), re.IGNORECASE)
        rules.append((f"denylist#{n}", pattern))
    return rules


def scan_text(where: str, text: str, denylist) -> Iterator[Finding]:
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for name, pattern in (*RULES, *denylist):
            for m in pattern.finditer(line):
                yield Finding(where, n, name, m.group(0))
        for m in EMAIL.finditer(line):
            if not ALLOWED_EMAIL.search(m.group(0)):
                yield Finding(where, n, "email", m.group(0))


def png_metadata(data: bytes) -> list[str]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return []
    found, i = [], 8
    while i + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[i : i + 8])
        if kind in (b"tEXt", b"zTXt", b"iTXt", b"eXIf"):
            found.append(kind.decode())
        if kind == b"IEND":
            break
        i += 12 + size
    return found


def jpeg_metadata(data: bytes) -> list[str]:
    if not data.startswith(b"\xff\xd8"):
        return []
    found, i = [], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xD9, 0xDA):  # end of image, start of scan
            break
        size = struct.unpack(">H", data[i + 2 : i + 4])[0]
        segment = data[i + 4 : i + 2 + size]
        if marker == 0xE1:
            found.append("Exif" if segment.startswith(b"Exif") else "XMP")
        elif marker == 0xED:
            found.append("IPTC")
        elif marker == 0xFE:
            found.append("comment")
        i += 2 + size
    return found


def webp_metadata(data: bytes) -> list[str]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return []
    found, i = [], 12
    while i + 8 <= len(data):
        kind = data[i : i + 4]
        size = struct.unpack("<I", data[i + 4 : i + 8])[0]
        if kind in (b"EXIF", b"XMP "):
            found.append(kind.decode().strip())
        i += 8 + size + (size & 1)
    return found


def is_binary(path: str, data: bytes) -> bool:
    return Path(path).suffix.lower() in BINARY_SUFFIXES or b"\0" in data[:8192]


def scan_blob(where: str, data: bytes, denylist) -> Iterator[Finding]:
    yield from scan_text(where + " (path)", where, denylist)
    if not is_binary(where, data):
        yield from scan_text(where, data.decode("utf-8", errors="replace"), denylist)
        return
    for kind in png_metadata(data) + jpeg_metadata(data) + webp_metadata(data):
        yield Finding(where, 0, f"media-metadata:{kind}", kind)
    # Paths and names hide in metadata as UTF-8 or UTF-16.
    for text in (data.decode("latin-1"), data.decode("utf-16-le", errors="ignore")):
        for name, pattern in RULES + denylist:
            if name in BINARY_RULES or name.startswith("denylist#"):
                for m in pattern.finditer(text):
                    yield Finding(where, 0, name, m.group(0))


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def staged_blobs() -> Iterator[tuple[str, bytes]]:
    names = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").split(b"\0")
    for raw in filter(None, names):
        name = raw.decode()
        yield name, git("show", f":{name}")


def tracked_blobs() -> Iterator[tuple[str, bytes]]:
    for raw in filter(None, git("ls-files", "-z").split(b"\0")):
        name = raw.decode()
        path = Path(name)
        if path.is_file():
            yield name, path.read_bytes()


def path_blobs(paths: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    for p in map(Path, paths):
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            yield f.as_posix(), f.read_bytes()


def check_ident() -> tuple[list[str], list[str]]:
    """Author/committer must use a GitHub noreply address. Returns (errors, warnings)."""
    errors, warnings = [], []
    for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        ident = git("var", var).decode().strip()
        m = re.match(r"^(.*) <([^>]*)> \d+ ([+-]\d{4})$", ident)
        if not m:
            errors.append(f"{var}: could not parse git identity")
            continue
        _, email, tz = m.groups()
        if not NOREPLY_EMAIL.search(email):
            errors.append(
                f"{var}: email is not a GitHub noreply address. Run: "
                'git config user.email "<id>+<handle>@users.noreply.github.com"'
            )
        if tz != "+0000":
            warnings.append(f"{var}: timestamp carries a UTC offset; set TZ=UTC to hide it")
    return errors, warnings


def check_commit_msg(path: Path, denylist) -> tuple[list[Finding], list[str]]:
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    )
    errors = []
    signoffs = re.findall(r"^Signed-off-by: .* <([^>]+)>\s*$", text, re.MULTILINE)
    if not signoffs:
        errors.append("commit message has no Signed-off-by line (use git commit -s)")
    elif not all(NOREPLY_EMAIL.search(e) for e in signoffs):
        errors.append("Signed-off-by must use a GitHub noreply address")
    return list(scan_text("commit message", text, denylist)), errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--staged", action="store_true", help="scan files staged for commit")
    src.add_argument("--all", action="store_true", help="scan every tracked file")
    src.add_argument("--stdin", action="store_true", help="scan text piped on stdin")
    src.add_argument("--commit-msg", metavar="FILE", help="check a commit message file")
    ap.add_argument("paths", nargs="*", help="files or directories to scan")
    ap.add_argument("--check-ident", action="store_true", help="also check git author identity")
    ap.add_argument("--denylist", type=Path, default=None, help="private denylist file")
    ap.add_argument("--require-denylist", action="store_true", help="fail if it is missing")
    ap.add_argument("--show", action="store_true", help="print matched text (local use only)")
    args = ap.parse_args(argv)

    denylist_path = args.denylist or default_denylist_path()
    denylist = []
    if denylist_path.is_file():
        denylist = load_denylist(denylist_path)
    elif args.require_denylist:
        print(f"identity-scan: denylist not found at {denylist_path}", file=sys.stderr)
        return 2
    else:
        print("identity-scan: warning: no private denylist, generic rules only", file=sys.stderr)

    findings: list[Finding] = []
    errors: list[str] = []
    warnings: list[str] = []

    if args.check_ident:
        e, w = check_ident()
        errors += e
        warnings += w
    if args.commit_msg:
        f, e = check_commit_msg(Path(args.commit_msg), denylist)
        findings += f
        errors += e
    elif args.stdin:
        findings += scan_text("stdin", sys.stdin.read(), denylist)
    else:
        if args.staged:
            blobs = staged_blobs()
        elif args.all:
            blobs = tracked_blobs()
        else:
            blobs = path_blobs(args.paths or ["."])
        for name, data in blobs:
            findings += scan_blob(name, data, denylist)

    for w in warnings:
        print(f"identity-scan: warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"identity-scan: {e}", file=sys.stderr)
    for f in dict.fromkeys(findings):
        print(f.render(args.show), file=sys.stderr)
    if findings or errors:
        print(
            f"identity-scan: blocked ({len(findings)} finding(s), {len(errors)} error(s)). "
            f"Mark a deliberate line with '{ALLOW_MARKER}'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
