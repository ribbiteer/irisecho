# SPDX-License-Identifier: AGPL-3.0-or-later
# Identifying strings are assembled at runtime so this file passes its own scan.
import importlib.util
import re
import struct
import sys
import zlib
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "identity_scan", Path(__file__).parents[1] / "scripts" / "identity_scan.py"
)
scan = importlib.util.module_from_spec(_spec)
sys.modules["identity_scan"] = scan
_spec.loader.exec_module(scan)

SEP = "\\"


def rules_hit(text, denylist=()):
    return {f.rule for f in scan.scan_text("t", text, list(denylist))}


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("C:" + SEP + "Users" + SEP + "alice" + SEP + "models", "user-home-path"),
        ("/Users/" + "alice/Downloads", "posix-home-path"),
        ("/home/" + "alice/.cache", "posix-home-path"),
        ("http://192.168" + ".1.20:8188", "private-ipv4"),
        ("10.0" + ".0.5", "private-ipv4"),
        ("DESKTOP" + "-AB12CD3", "windows-hostname"),
        ("alice" + "@proton.me", "email"),
        ("hf_" + "a" * 34, "hf-token"),
        ("ghp_" + "b" * 36, "github-token"),
    ],
)
def test_generic_rules_catch(text, rule):
    assert rule in rules_hit(text)


@pytest.mark.parametrize(
    "text",
    [
        "C:" + SEP + "Users" + SEP + "<you>" + SEP + "models",
        "C:" + SEP + "Users" + SEP + "Public",
        "%USERPROFILE%" + SEP + ".cache",
        "/home/runner/work",
        "~/.config/irisecho",
        "123+handle" + "@users.noreply.github.com",
        "noreply" + "@anthropic.com",
        "http://127.0.0.1:8188",
        "version 10.1.2",
        "icons/128x128@2x.png",
        "logo@3x.webp",
    ],
)
def test_generic_rules_allow(text):
    assert rules_hit(text) == set()


def test_allow_marker_skips_line():
    assert rules_hit("DESKTOP" + "-AB12CD3  # identity-scan: allow") == set()


def test_denylist(tmp_path):
    f = tmp_path / "deny.txt"
    f.write_text("# comment\n\\bsecretname\\b\nweird[regex\n", encoding="utf-8")
    deny = scan.load_denylist(f)
    assert rules_hit("hello SecretName here", deny) == {"denylist#2"}
    assert rules_hit("a weird[regex b", deny) == {"denylist#3"}
    assert rules_hit("secretnames", deny) == set()


def _png(chunks):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    for kind, data in chunks:
        out += chunk(kind, data)
    return out + chunk(b"IEND", b"")


def test_png_workflow_metadata_blocked():
    data = _png([(b"tEXt", b"workflow\0{}")])
    rules = {f.rule for f in scan.scan_blob("a.png", data, [])}
    assert "media-metadata:tEXt" in rules


def test_clean_png_passes():
    assert list(scan.scan_blob("a.png", _png([]), [])) == []


def test_path_inside_binary_blocked():
    leak = ("C:" + SEP + "Users" + SEP + "alice").encode("utf-16-le")
    rules = {f.rule for f in scan.scan_blob("a.wav", b"RIFF\0\0" + leak, [])}
    assert "user-home-path" in rules


def test_findings_hide_match_by_default():
    f = scan.Finding("x.md", 3, "email", "alice" + "@proton.me")
    assert f.render(show=False) == "x.md:3: email"
    assert re.search("proton", f.render(show=True))


def test_commit_msg_requires_noreply_signoff(tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text("Add thing\n\nSigned-off-by: h <1+h" + "@users.noreply.github.com>\n")
    assert scan.check_commit_msg(msg, []) == ([], [])

    msg.write_text("Add thing\n")
    assert scan.check_commit_msg(msg, [])[1]

    msg.write_text("Add thing\n\nSigned-off-by: h <h" + "@proton.me>\n")
    findings, errors = scan.check_commit_msg(msg, [])
    assert errors and findings


def test_scrub_png_removes_metadata_and_keeps_pixels():
    spec = importlib.util.spec_from_file_location(
        "scrub_media", Path(__file__).parents[1] / "scripts" / "scrub_media.py"
    )
    scrub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scrub)

    dirty = _png([(b"tEXt", b"workflow\0{}"), (b"tIME", b"\0" * 7)])
    clean, dropped = scrub.scrub_png(dirty)
    assert dropped == ["tEXt", "tIME"]
    assert clean == _png([])
    assert list(scan.scan_blob("a.png", clean, [])) == []


# --- home folders as git-bash, Cygwin and WSL spell them (P1-18) ----------------


@pytest.mark.parametrize(
    "text",
    [
        "/c/" + "Users/alice/models",
        "cd /C/" + "Users/alice",
        "/mnt/c/" + "Users/alice/.cache",
        "/mnt/d/" + "home/alice",
        "/cygdrive/c/" + "Users/alice",
        "/c/" + "users/alice",
        '"/c/' + 'Users/alice.b-c"',
    ],
)
def test_shell_spellings_of_a_home_folder_are_caught(text):
    assert "shell-home-path" in rules_hit(text)


@pytest.mark.parametrize(
    "text",
    [
        "/c/" + "Users/<you>/models",
        "/c/" + "Users/you/models",
        "/mnt/c/" + "Users/username",
        "/c/" + "Users/Public/Documents",
        "/c/" + "Users/runneradmin/.cargo",
        "/mnt/c/" + "Users/$USER",
        "https://example.com/api/" + "users/alice",
        "/mnt/data/" + "models",
        "src/c/" + "Users/alice",
    ],
)
def test_shell_spellings_keep_the_placeholders(text):
    assert rules_hit(text) == set()


def test_shell_home_path_inside_binary_blocked():
    leak = ("/c/" + "Users/alice").encode()
    rules = {f.rule for f in scan.scan_blob("a.ttf", b"\0\1\0\0" + leak + b"\0", [])}
    assert "shell-home-path" in rules


# --- no quiet bypasses (P2-43) -------------------------------------------------

SIGNED = "\n\nSigned-off-by: h <1+h" + "@users.noreply.github.com>\n"


def test_a_finished_commit_message_is_scanned_whole(tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text("Add thing\n\n# seen in /c/" + "Users/alice/project" + SIGNED, encoding="utf-8")
    # In the editor (the commit-msg hook) a # line is a comment git drops.
    assert scan.check_commit_msg(msg, []) == ([], [])
    # In a finished commit, as CI reads it, the line is published text.
    findings, errors = scan.check_commit_msg(msg, [], full=True)
    assert {f.rule for f in findings} == {"shell-home-path"} and not errors
    assert scan.main(["--commit-msg", str(msg)]) == 0
    assert scan.main(["--commit-msg", str(msg), "--full-message"]) == 1


def test_the_denylist_can_be_required(tmp_path, capsys):
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see\n", encoding="utf-8")
    missing = tmp_path / "none.txt"
    # Locally and on forks: a warning, generic rules only.
    assert scan.main(["--denylist", str(missing), str(clean)]) == 0
    assert "no private denylist" in capsys.readouterr().err
    # CI on the main repository passes --require-denylist: missing or empty is an error.
    assert scan.main(["--require-denylist", "--denylist", str(missing), str(clean)]) == 2
    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n\n", encoding="utf-8")
    assert scan.main(["--require-denylist", "--denylist", str(empty), str(clean)]) == 2
    real = tmp_path / "deny.txt"
    real.write_text("secretname\n", encoding="utf-8")
    assert scan.main(["--require-denylist", "--denylist", str(real), str(clean)]) == 0
    clean.write_text("by SecretName\n", encoding="utf-8")
    assert scan.main(["--require-denylist", "--denylist", str(real), str(clean)]) == 1


def _riff(kind, chunks):
    body = kind + b"".join(
        name + struct.pack("<I", len(data)) + data + b"\0" * (len(data) & 1)
        for name, data in chunks
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _box(kind, body=b""):
    return struct.pack(">I", 8 + len(body)) + kind + body


def _vorbis(comments):
    vendor = b"test encoder"
    out = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for c in comments:
        out += struct.pack("<I", len(c)) + c
    return out


def _flac(comments):
    info = bytes([0]) + (34).to_bytes(3, "big") + b"\0" * 34
    tags = _vorbis(comments)
    return b"fLaC" + info + bytes([0x80 | 4]) + len(tags).to_bytes(3, "big") + tags


def _ogg(comments):
    page = b"OggS" + b"\0" * 22 + b"\1\x1e" + b"\1vorbis" + b"\0" * 23
    return page + b"OggS" + b"\0" * 22 + b"\1\x40" + b"\3vorbis" + _vorbis(comments)


FMT = (b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))
PCM = (b"data", b"\0\0\0\0")
FTYP = _box(b"ftyp", b"isom\0\0\0\0isom")
MVHD = _box(b"mvhd", b"\0" * 100)
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\0" * 200

MEDIA_WITH_METADATA = [
    ("a.wav", _riff(b"WAVE", [FMT, (b"LIST", b"INFOIART\4\0\0\0abc\0"), PCM]), "LIST"),
    ("a.wav", _riff(b"WAVE", [FMT, PCM, (b"bext", b"\0" * 8)]), "bext"),
    ("a.wav", _riff(b"WAVE", [FMT, PCM, (b"id3 ", b"ID3\4\0\0\0\0\0\0")]), "ID3"),
    ("a.mp3", b"ID3\4\0\0\0\0\0\0" + MP3_FRAME, "ID3v2"),
    ("a.mp3", MP3_FRAME + b"TAG" + b"\0" * 125, "ID3v1"),
    ("a.flac", _flac([b"ARTIST=abc"]), "VorbisComment"),
    ("a.ogg", _ogg([b"ARTIST=abc"]), "VorbisComment"),
    ("a.mp4", FTYP + _box(b"moov", MVHD + _box(b"udta", _box(b"meta", b"\0" * 4))), "udta"),
    ("a.mov", FTYP + _box(b"moov", MVHD + _box(b"trak", _box(b"meta", b"\0" * 4))), "meta"),
    ("a.m4a", FTYP + _box(b"moov", MVHD) + _box(b"uuid", b"\0" * 16), "uuid"),
    ("a.pdf", PDF + b"2 0 obj\n<< /Producer (abc) >>\nendobj\n", "Info"),
    (
        "a.pdf",
        PDF
        + b"2 0 obj\n<< >>\nstream\n"
        + zlib.compress(b"<< /Author (abc) >>")
        + b"\nendstream\n",
        "Info",
    ),
    ("a.pdf", PDF + b"2 0 obj\n<< /Type /Metadata >>\nstream\n<x:xmpmeta/>\nendstream\n", "XMP"),
    ("a.pdf", PDF + b"trailer\n<< /Encrypt 3 0 R >>\n", "encrypted"),
    ("a.ico", b"\0\0\1\0\1\0" + b"\0" * 16 + _png([(b"tEXt", b"Author\0abc")]), "tEXt"),
    ("a.icns", b"icns\0\0\0\0ic07\0\0\0\0" + _png([(b"iTXt", b"x\0\0\0\0\0y")]), "iTXt"),
]


@pytest.mark.parametrize(("name", "data", "kind"), MEDIA_WITH_METADATA)
def test_media_metadata_blocked(name, data, kind):
    rules = {f.rule for f in scan.scan_blob(name, data, [])}
    assert f"media-metadata:{kind}" in rules


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("a.wav", _riff(b"WAVE", [FMT, PCM])),
        ("a.mp3", MP3_FRAME),
        ("a.flac", _flac([])),
        ("a.ogg", _ogg([])),
        ("a.mp4", FTYP + _box(b"moov", MVHD + _box(b"trak", _box(b"mdia"))) + _box(b"mdat")),
        ("a.pdf", PDF),
        ("a.ico", b"\0\0\1\0\1\0" + b"\0" * 16 + _png([])),
    ],
)
def test_clean_media_passes(name, data):
    assert list(scan.scan_blob(name, data, [])) == []


def test_a_name_inside_a_packed_pdf_stream_is_found():
    packed = zlib.compress(("made in /c/" + "Users/alice/docs").encode())
    data = PDF + b"2 0 obj\n<< >>\nstream\n" + packed + b"\nendstream\n"
    assert "shell-home-path" in {f.rule for f in scan.scan_blob("a.pdf", data, [])}


def test_media_that_cannot_be_checked_is_refused_until_reviewed(monkeypatch):
    for name in ("docs/demo.gif", "docs/clip.webm", "scan.tiff"):
        assert {f.rule for f in scan.scan_blob(name, b"GIF89a\0", [])} == {"media-unchecked"}
    monkeypatch.setattr(scan, "REVIEWED_MEDIA", frozenset({"docs/demo.gif"}))
    assert list(scan.scan_blob("docs/demo.gif", b"GIF89a\0", [])) == []
    assert list(scan.scan_blob("docs/other.gif", b"GIF89a\0", []))


def test_every_tracked_media_type_is_checked_or_refused():
    """A media type the scan neither reads nor refuses would pass unseen."""
    checked = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".icns", ".wav", ".mp3", ".flac"}
    checked |= {".ogg", ".opus", ".mp4", ".m4a", ".m4v", ".mov", ".pdf"}
    no_metadata = {".ttf", ".otf", ".woff", ".woff2", ".bmp"}  # fonts name their makers by design
    binaries = {".zip", ".exe", ".dll", ".dylib", ".so"}
    known = checked | no_metadata | binaries | scan.UNCHECKED_MEDIA
    assert scan.BINARY_SUFFIXES <= known, scan.BINARY_SUFFIXES - known
    assert scan.UNCHECKED_MEDIA <= scan.BINARY_SUFFIXES
