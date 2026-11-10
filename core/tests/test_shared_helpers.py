# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers the engines and the store share: unpacking a source archive, hashing a file."""

import asyncio
import hashlib
import io
import zipfile

import httpx
import pytest

from irisecho_core.engines import uvenv
from irisecho_core.registry import FileSpec
from irisecho_core.store import Hasher, file_matches, hash_file, sha256_file


def archive(top="Repo-0123abc", files=None) -> bytes:
    """A zip the way codeload.github.com makes one: everything inside one folder."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in (files or {"README.md": "hello", "pkg/mod.py": "x = 1\n"}).items():
            z.writestr(f"{top}/{name}", text)
    return out.getvalue()


def test_extract_source_unpacks_the_single_top_folder_as_dest(tmp_path):
    dest = tmp_path / "src"
    uvenv.extract_source(archive(), dest)
    assert (dest / "README.md").read_text() == "hello"
    assert (dest / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert not (tmp_path / "src.tmp").exists()


def test_extract_source_replaces_what_is_there_and_clears_a_leftover(tmp_path):
    dest = tmp_path / "src"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    (tmp_path / "src.tmp").mkdir()
    (tmp_path / "src.tmp" / "half.txt").write_text("an earlier try")
    uvenv.extract_source(archive(files={"new.txt": "new"}), dest)
    assert sorted(p.name for p in dest.iterdir()) == ["new.txt"]
    assert not (tmp_path / "src.tmp").exists()


def test_extract_source_refuses_an_archive_with_two_top_folders(tmp_path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("a/x", "1")
        z.writestr("b/y", "2")
    with pytest.raises(ValueError):
        uvenv.extract_source(out.getvalue(), tmp_path / "src")


def test_fetch_source_asks_for_the_pinned_commit_and_unpacks_it(tmp_path, monkeypatch):
    asked = []
    real = httpx.AsyncClient

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, content=archive())

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    log = []
    asyncio.run(uvenv.fetch_source("Owner/Repo", "0123456789abcdef", tmp_path / "src", log.append))
    assert asked == ["https://codeload.github.com/Owner/Repo/zip/0123456789abcdef"]
    assert log == ["Downloading Owner/Repo @ 0123456789"]
    assert (tmp_path / "src" / "README.md").read_text() == "hello"


def test_fetch_source_keeps_what_is_there_when_the_download_fails(tmp_path, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(404)), **kw),
    )
    dest = tmp_path / "src"
    dest.mkdir()
    (dest / "keep.txt").write_text("kept")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(uvenv.fetch_source("Owner/Repo", "abc", dest, lambda line: None))
    assert (dest / "keep.txt").read_text() == "kept"


def test_every_worker_engine_picks_its_device_the_same_way():
    from types import SimpleNamespace

    from irisecho_core.engines.worker import WorkerEngine

    for backend, device in (("cuda", "cuda"), ("mps", "mps"), ("cpu", "cpu"), ("other", "cpu")):
        engine = SimpleNamespace(hw=SimpleNamespace(backend=backend))
        assert WorkerEngine.device(engine) == device


def test_hashing_one_loop_gives_the_same_answers(tmp_path):
    data = bytes(range(256)) * 40000  # larger than one read
    path = tmp_path / "f.bin"
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()
    seen = []
    assert sha256_file(path, seen.append) == hashlib.sha256(data).hexdigest()
    assert seen[-1] == len(data) and seen == sorted(seen)

    plain = hashlib.sha256()
    hash_file(path, plain)
    assert plain.hexdigest() == hashlib.sha256(data).hexdigest()

    # The hasher a download resumes with, fed by the same loop.
    spec = FileSpec(
        id="f",
        repo="r",
        revision="v",
        path="a/b.json",
        category="c",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    resumed = Hasher(spec)
    hash_file(path, resumed)
    assert resumed.ok() and file_matches(path, spec)
