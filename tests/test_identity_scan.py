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
