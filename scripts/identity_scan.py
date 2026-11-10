#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse to publish anything that says who, or where, the developer is.

Runs as a git hook (pre-commit, commit-msg) and in CI. Standard library only.

Two sets of rules:

* Built-in, generic: home-directory paths, private LAN addresses, default
  Windows hostnames, non-noreply email addresses, access tokens, and metadata
  blocks in media files: PNG/JPEG/WebP (ComfyUI writes its whole workflow, file
  paths included, into every PNG it saves), the PNGs inside .ico/.icns, WAV,
  MP3, FLAC, Ogg, MP4/MOV and PDF. Media types whose metadata this script can
  not read (UNCHECKED_MEDIA) are refused unless the file is listed in
  REVIEWED_MEDIA after a look by hand.
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
import zlib
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
        # The same folders as git-bash, Cygwin and WSL spell them: /c/Users/<name>,
        # /cygdrive/c/Users/<name>, /mnt/c/Users/<name>. The drive letter is why the
        # rule above does not see them.
        "shell-home-path",
        re.compile(
            r"(?<![\w.~])/(?:mnt/|cygdrive/)?[A-Za-z]/(?:[Uu]sers|home|Documents and Settings)/"
            r"(?!(?i:public|default|runneradmin|all users|runner|shared|user|username|you)\b)"
            r"[\w.-]+",
        ),
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
BINARY_RULES = {"user-home-path", "posix-home-path", "shell-home-path", "windows-hostname"}

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
    if not data.startswith(PNG_MAGIC):
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


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def icon_metadata(data: bytes) -> list[str]:
    """Metadata of the PNGs stored inside a .ico or .icns file."""
    is_ico = data[:4] == b"\x00\x00\x01\x00"
    if not (is_ico or data[:4] == b"icns"):
        return []
    found, i = [], data.find(PNG_MAGIC)
    while i != -1:
        found += png_metadata(data[i:])
        i = data.find(PNG_MAGIC, i + 8)
    return found


def wav_metadata(data: bytes) -> list[str]:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return []
    names = {
        b"LIST": "LIST",
        b"bext": "bext",
        b"iXML": "iXML",
        b"axml": "axml",
        b"cart": "cart",
        b"_PMX": "XMP",
        b"id3 ": "ID3",
        b"ID3 ": "ID3",
    }
    found, i = [], 12
    while i + 8 <= len(data):
        kind = data[i : i + 4]
        size = struct.unpack("<I", data[i + 4 : i + 8])[0]
        if kind in names:
            found.append(names[kind])
        i += 8 + size + (size & 1)
    return found


def mp3_metadata(path: str, data: bytes) -> list[str]:
    found = []
    if data[:3] == b"ID3":
        found.append("ID3v2")
    if Path(path).suffix.lower() == ".mp3":
        if data[-128:-125] == b"TAG":
            found.append("ID3v1")
        if b"APETAGEX" in data[:4096] or b"APETAGEX" in data[-4096:]:
            found.append("APE")
    return found


def _vorbis_comments(data: bytes, start: int) -> int:
    """How many user comments a Vorbis comment block holds (the vendor string is not one)."""
    if start + 4 > len(data):
        return 0
    vendor = struct.unpack("<I", data[start : start + 4])[0]
    count = data[start + 4 + vendor : start + 8 + vendor]
    return struct.unpack("<I", count)[0] if len(count) == 4 else 0


def flac_metadata(data: bytes) -> list[str]:
    if data[:4] != b"fLaC":
        return []
    found, i = [], 4
    while i + 4 <= len(data):
        kind, size = data[i] & 0x7F, int.from_bytes(data[i + 1 : i + 4], "big")
        if kind == 2:
            found.append("application")
        elif kind == 4 and _vorbis_comments(data, i + 4):
            found.append("VorbisComment")
        elif kind == 6:
            found.append("picture")
        if data[i] & 0x80:  # last metadata block
            break
        i += 4 + size
    return found


def ogg_metadata(data: bytes) -> list[str]:
    if data[:4] != b"OggS":
        return []
    head = data[: 1 << 16]
    for marker in (b"\x03vorbis", b"OpusTags"):
        at = head.find(marker)
        if at != -1 and _vorbis_comments(head, at + len(marker)):
            return ["VorbisComment"]
    return []


MP4_FIRST = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide")
MP4_CONTAINERS = (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"moof", b"traf")


def mp4_metadata(data: bytes) -> list[str]:
    """Tag boxes (`udta`, `meta`, XMP in `uuid`) of an MP4, MOV or M4A file."""
    if data[4:8] not in MP4_FIRST:
        return []
    found: list[str] = []

    def walk(start: int, end: int) -> None:
        i = start
        while i + 8 <= end:
            size, kind = struct.unpack(">I4s", data[i : i + 8])
            head = 8
            if size == 1 and i + 16 <= end:
                size, head = struct.unpack(">Q", data[i + 8 : i + 16])[0], 16
            elif size == 0:
                size = end - i
            if size < head or i + size > end:
                return
            if kind in (b"udta", b"meta", b"uuid"):
                found.append(kind.decode())
            elif kind in MP4_CONTAINERS:
                walk(i + head, i + size)
            i += size

    walk(0, len(data))
    return found


PDF_INFO = re.compile(rb"/(?:Author|Creator|Producer|Company|Manager|LastModifiedBy)\b")
PDF_XMP = re.compile(rb"<x:xmpmeta|<\?xpacket|/Type\s*/Metadata\b")
PDF_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def pdf_texts(data: bytes) -> list[bytes]:
    """A PDF's bytes and each of its compressed streams unpacked: the document
    information can sit inside an object stream, where a plain search misses it."""
    if not data.startswith(b"%PDF-"):
        return []
    texts = [data]
    for m in PDF_STREAM.finditer(data):
        try:
            texts.append(zlib.decompressobj().decompress(m.group(1)))
        except zlib.error:
            continue
    return texts


def pdf_metadata(data: bytes) -> list[str]:
    texts = pdf_texts(data)
    if not texts:
        return []
    found = []
    if re.search(rb"/Encrypt\b", data):
        found.append("encrypted")  # nothing inside can be checked
    if any(PDF_INFO.search(t) for t in texts):
        found.append("Info")
    if any(PDF_XMP.search(t) for t in texts):
        found.append("XMP")
    return found


# Media whose metadata this script cannot read with the standard library. A file
# of one of these types is refused unless its path is in REVIEWED_MEDIA, added
# there after its metadata was looked at by hand.
UNCHECKED_MEDIA = {
    ".gif",
    ".tif",
    ".tiff",
    ".webm",
    ".mkv",
    ".avi",
    ".wmv",
    ".wma",
    ".aif",
    ".aiff",
    ".aac",
    ".heic",
    ".avif",
    ".psd",
}
REVIEWED_MEDIA: frozenset[str] = frozenset()
BINARY_SUFFIXES |= UNCHECKED_MEDIA | {".m4a", ".m4v", ".opus"}


def media_metadata(path: str, data: bytes) -> list[str]:
    return (
        png_metadata(data)
        + jpeg_metadata(data)
        + webp_metadata(data)
        + icon_metadata(data)
        + wav_metadata(data)
        + mp3_metadata(path, data)
        + flac_metadata(data)
        + ogg_metadata(data)
        + mp4_metadata(data)
        + pdf_metadata(data)
    )


def is_binary(path: str, data: bytes) -> bool:
    return Path(path).suffix.lower() in BINARY_SUFFIXES or b"\0" in data[:8192]


def scan_blob(where: str, data: bytes, denylist) -> Iterator[Finding]:
    yield from scan_text(where + " (path)", where, denylist)
    if not is_binary(where, data):
        yield from scan_text(where, data.decode("utf-8", errors="replace"), denylist)
        return
    for kind in media_metadata(where, data):
        yield Finding(where, 0, f"media-metadata:{kind}", kind)
    suffix = Path(where).suffix.lower()
    if suffix in UNCHECKED_MEDIA and Path(where).as_posix() not in REVIEWED_MEDIA:
        yield Finding(where, 0, "media-unchecked", suffix)
    # Paths and names hide in metadata as UTF-8 or UTF-16 (in a PDF, inside packed streams too).
    texts = [data.decode("latin-1"), data.decode("utf-16-le", errors="ignore")]
    texts += [t.decode("latin-1") for t in pdf_texts(data)[1:]]
    for text in texts:
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


def check_commit_msg(path: Path, denylist, full: bool = False) -> tuple[list[Finding], list[str]]:
    """Check a commit message. In the commit-msg hook, lines starting with `#` are the
    editor's comments and git drops them; in a finished commit (`full`, as CI reads
    it) they are part of the published message and are scanned like any other line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if full or not line.startswith("#"))
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
    ap.add_argument(
        "--full-message",
        action="store_true",
        help="with --commit-msg: the file is a finished commit's message, so scan '#' lines too",
    )
    ap.add_argument(
        "--require-denylist",
        action="store_true",
        help="fail if the private denylist is missing or empty (CI on the main repository)",
    )
    ap.add_argument("--show", action="store_true", help="print matched text (local use only)")
    args = ap.parse_args(argv)

    denylist_path = args.denylist or default_denylist_path()
    denylist = []
    if denylist_path.is_file():
        denylist = load_denylist(denylist_path)
    if args.require_denylist and not denylist:
        print("identity-scan: the private denylist is missing or empty", file=sys.stderr)
        return 2
    if not denylist_path.is_file():
        print("identity-scan: warning: no private denylist, generic rules only", file=sys.stderr)

    findings: list[Finding] = []
    errors: list[str] = []
    warnings: list[str] = []

    if args.check_ident:
        e, w = check_ident()
        errors += e
        warnings += w
    if args.commit_msg:
        f, e = check_commit_msg(Path(args.commit_msg), denylist, full=args.full_message)
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
