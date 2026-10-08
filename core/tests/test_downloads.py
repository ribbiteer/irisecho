# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file can only be downloaded if there is a real hash to check it against."""

import asyncio

import pytest

from irisecho_core.registry import FileSpec


def spec(**kw) -> FileSpec:
    base = dict(id="f", repo="r", revision="v", path="m.safetensors", category="c", size=10)
    base.update(kw)
    return FileSpec(**{"sha256": "", **base})


@pytest.mark.parametrize(
    ("kw", "pinned"),
    [
        ({"sha256": "a" * 64}, True),
        ({"git_sha1": "b" * 40}, True),
        ({"sha256": "*" * 64}, False),  # what Hugging Face shows for a gated file's hash
        ({"sha256": "A" * 64}, False),
        ({"git_sha1": "b" * 39}, False),
        ({}, False),
    ],
)
def test_pinned(kw, pinned):
    assert spec(**kw).pinned is pinned


def test_every_registry_file_has_a_real_hash():
    from irisecho_core import registry

    bad = [s.id for s in registry.default().files.values() if not s.pinned]
    assert not bad, f"run scripts/lock_registry.py (with HF_TOKEN for gated models): {bad}"


def test_an_unpinned_file_fails_at_once_instead_of_downloading(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    from irisecho_core.downloads import DownloadManager
    from irisecho_core.events import EventBus
    from irisecho_core.registry import Registry
    from irisecho_core.store import Store

    bad = spec(sha256="*" * 64)
    store = Store(Registry(licenses={}, files={"f": bad}, groups={}, models={}), tmp_path / "m")

    async def go():
        manager = DownloadManager(store, EventBus())
        (d,) = manager.request([bad])
        return d

    d = asyncio.run(go())
    assert d.state == "error" and "Update IrisEcho" in d.error
    assert d._task is None  # nothing was fetched


def test_changing_the_models_folder_moves_running_downloads_there(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    from irisecho_core import downloads
    from irisecho_core.downloads import DownloadManager
    from irisecho_core.events import EventBus
    from irisecho_core.registry import Registry
    from irisecho_core.store import Store

    f = spec(sha256="a" * 64)
    store = Store(Registry(licenses={}, files={"f": f}, groups={}, models={}), tmp_path / "old")
    gate = None

    async def fetch(self, s, d):
        part = self.store.part_path(s)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"12345")
        await gate.wait()  # the first download is still running when the folder changes
        final = self.store.own_path(s)
        part.replace(final)
        final.write_bytes(b"0123456789")
        d.state = "done"

    monkeypatch.setattr(downloads.DownloadManager, "_fetch", fetch)

    async def go():
        nonlocal gate
        gate = asyncio.Event()
        manager = DownloadManager(store, EventBus())
        manager.request([f])
        await asyncio.sleep(0.05)
        moving = asyncio.create_task(manager.move_to(tmp_path / "new"))
        await asyncio.sleep(0.05)
        gate.set()
        await moving
        await manager.wait([f])
        return manager

    manager = asyncio.run(go())
    assert manager.items["f"].state == "done"
    assert store.locate(f) == tmp_path / "new" / "c" / "m.safetensors"
    assert not list((tmp_path / "old").rglob("*.part"))  # the partial file was not left behind
