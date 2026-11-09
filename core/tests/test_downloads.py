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


# --- fetching, with the host played by httpx.MockTransport --------------------

DATA = bytes(range(256)) * 40


def _host(monkeypatch, handler, token=None):
    import httpx

    from irisecho_core import downloads

    real = httpx.AsyncClient
    monkeypatch.setattr(
        downloads.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(downloads, "RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(downloads.credentials, "get", lambda *a: token)


def _serve(tmp_path, monkeypatch, handler, part=None, data=DATA, sha=None, token=None):
    """Run one download to its end. Returns (download, store, spec)."""
    import hashlib

    from irisecho_core.downloads import DownloadManager
    from irisecho_core.events import EventBus
    from irisecho_core.registry import Registry
    from irisecho_core.store import Store

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    _host(monkeypatch, handler, token)
    f = spec(sha256=sha or hashlib.sha256(data).hexdigest(), size=len(data))
    store = Store(Registry(licenses={}, files={"f": f}, groups={}, models={}), tmp_path / "m")
    if part is not None:
        p = store.part_path(f)
        p.parent.mkdir(parents=True)
        p.write_bytes(part)

    async def go():
        manager = DownloadManager(store, EventBus())
        manager.request([f])
        await manager.wait([f])
        return manager.items["f"]

    return asyncio.run(go()), store, f


def _ranged(requests):
    """A host that serves DATA and honours Range."""
    import httpx

    def handler(request):
        requests.append(request)
        start = request.headers.get("range")
        if start:
            offset = int(start.removeprefix("bytes=").removesuffix("-"))
            if offset >= len(DATA):
                return httpx.Response(416)
            return httpx.Response(206, content=DATA[offset:])
        return httpx.Response(200, content=DATA)

    return handler


def test_a_download_is_hash_checked_then_renamed(tmp_path, monkeypatch):
    d, store, f = _serve(tmp_path, monkeypatch, _ranged([]))
    assert d.state == "done"
    assert store.own_path(f).read_bytes() == DATA
    assert not store.part_path(f).exists()


def test_a_partial_file_is_resumed(tmp_path, monkeypatch):
    requests = []
    d, store, f = _serve(tmp_path, monkeypatch, _ranged(requests), part=DATA[:1000])
    assert d.state == "done" and store.own_path(f).read_bytes() == DATA
    assert [r.headers.get("range") for r in requests] == ["bytes=1000-"]


def test_a_416_answer_starts_over(tmp_path, monkeypatch):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        if request.headers.get("range"):
            return httpx.Response(416)
        return httpx.Response(200, content=DATA)

    d, store, f = _serve(tmp_path, monkeypatch, handler, part=b"x" * 1000)
    assert d.state == "done" and store.own_path(f).read_bytes() == DATA
    assert [r.headers.get("range") for r in requests] == ["bytes=1000-", None]


def test_a_full_size_part_goes_straight_to_the_hash_check(tmp_path, monkeypatch):
    requests = []
    d, store, f = _serve(tmp_path, monkeypatch, _ranged(requests), part=DATA)
    assert d.state == "done" and store.own_path(f).read_bytes() == DATA
    assert requests == [] and not store.part_path(f).exists()


def test_a_full_size_part_with_the_wrong_bytes_is_discarded(tmp_path, monkeypatch):
    requests = []
    d, store, f = _serve(tmp_path, monkeypatch, _ranged(requests), part=b"x" * len(DATA))
    assert d.state == "error" and "hash" in d.error
    assert requests == []
    assert not store.part_path(f).exists() and not store.own_path(f).exists()


def test_a_hash_mismatch_discards_the_file(tmp_path, monkeypatch):
    d, store, f = _serve(tmp_path, monkeypatch, _ranged([]), sha="0" * 64)
    assert d.state == "error" and "did not match" in d.error
    assert not store.part_path(f).exists() and not store.own_path(f).exists()


def test_a_dropped_connection_is_retried(tmp_path, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(request.headers.get("range"))
        if len(calls) == 1:
            raise httpx.ConnectError("connection dropped")
        return _ranged([])(request)

    d, store, f = _serve(tmp_path, monkeypatch, handler)
    assert d.state == "done" and len(calls) == 2


def test_a_stream_that_ends_early_is_resumed_from_what_arrived(tmp_path, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(request.headers.get("range"))
        if len(calls) == 1:
            return httpx.Response(200, content=DATA[:3000])  # the host hung up
        return _ranged([])(request)

    d, store, f = _serve(tmp_path, monkeypatch, handler)
    assert d.state == "done" and store.own_path(f).read_bytes() == DATA
    assert calls == [None, "bytes=3000-"]


def test_a_5xx_is_retried_a_few_times_then_reported(tmp_path, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    d, store, f = _serve(tmp_path, monkeypatch, handler)
    assert d.state == "error" and "503" in d.error
    assert len(calls) == 4  # the first try and three more


def test_a_5xx_that_clears_up_finishes(tmp_path, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502) if len(calls) < 3 else _ranged([])(request)

    d, store, f = _serve(tmp_path, monkeypatch, handler)
    assert d.state == "done"


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_refusal_is_not_retried(tmp_path, monkeypatch, code):
    import httpx

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(code)

    d, _, _ = _serve(tmp_path, monkeypatch, handler)
    assert len(calls) == 1
    assert d.state == {401: "needs_token", 403: "needs_access", 404: "error"}[code]


def test_a_401_without_a_token_asks_for_one(tmp_path, monkeypatch):
    import httpx

    d, _, _ = _serve(tmp_path, monkeypatch, lambda r: httpx.Response(401))
    assert d.state == "needs_token" and "needs a Hugging Face account token" in d.error


def test_a_401_with_a_token_says_it_was_not_accepted(tmp_path, monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(401)

    d, _, _ = _serve(tmp_path, monkeypatch, handler, token="tok")
    assert seen == ["Bearer tok"]
    assert d.state == "needs_token" and "did not accept" in d.error


def test_not_enough_free_space_is_said_before_any_request(tmp_path, monkeypatch):
    import collections

    from irisecho_core import downloads

    requests = []
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(downloads.shutil, "disk_usage", lambda p: usage(10, 9, 5))
    d, store, f = _serve(tmp_path, monkeypatch, _ranged(requests), part=DATA[:1000])
    assert d.state == "error" and "Not enough free space" in d.error
    assert requests == []
    assert store.part_path(f).read_bytes() == DATA[:1000]  # what arrived is kept


def test_cancel_works_during_a_backoff_wait(tmp_path, monkeypatch):
    import httpx

    from irisecho_core import downloads
    from irisecho_core.downloads import DownloadManager
    from irisecho_core.events import EventBus
    from irisecho_core.registry import Registry
    from irisecho_core.store import Store

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    _host(monkeypatch, lambda r: httpx.Response(503))
    monkeypatch.setattr(downloads, "RETRY_DELAYS", (60.0,))
    f = spec(sha256="a" * 64, size=len(DATA))
    store = Store(Registry(licenses={}, files={"f": f}, groups={}, models={}), tmp_path / "m")

    async def go():
        manager = DownloadManager(store, EventBus())
        manager.request([f])
        await asyncio.sleep(0.2)  # now waiting out the backoff
        await asyncio.wait_for(manager.cancel_and_wait("f"), 5)
        return manager.items["f"]

    assert asyncio.run(go()).state == "cancelled"


def test_deleting_a_model_while_it_downloads_waits_for_the_download(tmp_path, monkeypatch):
    from irisecho_core import downloads
    from irisecho_core.app import App

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    app = App()
    specs = app.registry.model_files(app.model("kokoro"), app.hw)
    assert specs
    never = None

    async def fetch(self, s, d):
        part = self.store.part_path(s)
        part.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "wb") as f:  # an open handle blocks the delete on Windows
            f.write(b"12345")
            await never.wait()

    monkeypatch.setattr(downloads.DownloadManager, "_fetch", fetch)

    async def go():
        nonlocal never
        never = asyncio.Event()
        app.bus.bind(asyncio.get_running_loop())
        app.downloads.request(specs)
        await asyncio.sleep(0.1)
        tasks = [d._task for d in app.downloads.items.values()]
        freed = await app.remove_model("kokoro")
        return tasks, freed

    tasks, freed = asyncio.run(go())
    assert all(t.done() for t in tasks)
    assert freed > 0
    assert not list((tmp_path / "home").rglob("*.part"))
    assert app.downloads.items == {}


def test_a_model_file_in_use_is_reported_not_a_crash(tmp_path, monkeypatch):
    from irisecho_core.app import App, FileInUse

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    app = App()
    first = app.registry.model_files(app.model("kokoro"), app.hw)[0]
    part = app.store.part_path(first)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"123")

    def busy(spec):
        raise PermissionError("in use")

    monkeypatch.setattr(app.store, "remove", busy)
    with pytest.raises(FileInUse, match="in use by another program"):
        asyncio.run(app.remove_model("kokoro"))
