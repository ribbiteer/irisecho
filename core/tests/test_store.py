# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib

from irisecho_core.registry import FileSpec
from irisecho_core.store import file_matches


def spec(data: bytes, **kw) -> FileSpec:
    base = dict(
        id="f", repo="r", revision="v", path="a/b.json", category="c", size=len(data), sha256=""
    )
    base.update(kw)
    return FileSpec(**base)


def test_sha256_match(tmp_path):
    data = b"hello weights"
    p = tmp_path / "f"
    p.write_bytes(data)
    assert file_matches(p, spec(data, sha256=hashlib.sha256(data).hexdigest()))
    assert not file_matches(p, spec(data, sha256="0" * 64))


def test_git_blob_sha1_match(tmp_path):
    data = b'{"a": 1}\n'
    p = tmp_path / "f"
    p.write_bytes(data)
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    assert file_matches(p, spec(data, git_sha1=blob))
    assert not file_matches(p, spec(data, git_sha1="0" * 40))


def test_layout_keeps_subfolders():
    s = FileSpec(
        id="g/x/model.safetensors",
        repo="r",
        revision="v",
        path="x/model.safetensors",
        category="stable-audio-3/small-sfx",
        size=1,
        sha256="",
        subdir="x",
        keep_dirs=True,
    )
    assert s.layout == "small-sfx/x/model.safetensors"


def _registry_with(tmp_path, data: bytes):
    from irisecho_core.registry import Registry

    s = spec(data, id="f", category="diffusion_models", sha256=hashlib.sha256(data).hexdigest())
    return Registry(licenses={}, files={"f": s}, groups={}, models={}), s


def test_scan_finds_a_file_when_the_parent_folder_is_linked(tmp_path, monkeypatch):
    """Pointing at a folder well above `models` still finds the file, and reports progress."""
    from irisecho_core.store import Store

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    data = b"weights" * 100
    deep = tmp_path / "portable" / "ComfyUI" / "models" / "diffusion_models"
    deep.mkdir(parents=True)
    (deep / "renamed.safetensors").write_bytes(data)
    junk = tmp_path / "portable" / "python_embeded" / "Lib"
    junk.mkdir(parents=True)
    (junk / "same_size.bin").write_bytes(data)  # skipped: not where weights live
    reg, s = _registry_with(tmp_path, data)
    store = Store(reg, tmp_path / "own", [str(tmp_path / "portable")])
    events = []
    found = store.scan_links(lambda *a: events.append(a))
    assert found == {"f": str(deep / "renamed.safetensors")}
    assert store.status(s).state == "linked"
    assert events and events[-1][3] == events[-1][4] == len(data)  # overall done == total


def _two_file_store(tmp_path, monkeypatch):
    """Two registry files of one size, both present in a linked folder under other names."""
    from irisecho_core.registry import Registry
    from irisecho_core.store import Store

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    blobs = {"high": b"h" * 700, "low": b"l" * 700}
    folder = tmp_path / "models" / "diffusion_models"
    folder.mkdir(parents=True)
    files = {}
    for name, data in blobs.items():
        (folder / f"my-{name}.gguf").write_bytes(data)
        files[name] = spec(
            data,
            id=name,
            path=f"{name}.gguf",
            category="diffusion_models",
            sha256=hashlib.sha256(data).hexdigest(),
        )
    reg = Registry(licenses={}, files=files, groups={}, models={})
    return Store(reg, tmp_path / "own", [str(tmp_path / "models")]), folder


def test_each_link_is_saved_and_announced_as_it_is_confirmed(tmp_path, monkeypatch):
    import json

    store, _ = _two_file_store(tmp_path, monkeypatch)
    seen = []

    def on_link(file_id):
        # Already on disk and usable at this point, with the scan still going.
        saved = json.loads(store.index_path.read_text(encoding="utf-8"))
        seen.append((file_id, sorted(saved), store.status(store.registry.files[file_id]).state))

    store.scan_links(on_link=on_link)
    assert seen == [("high", ["high"], "linked"), ("low", ["high", "low"], "linked")]


def test_scan_reads_each_candidate_once(tmp_path, monkeypatch):
    from irisecho_core import store as store_module

    store, _ = _two_file_store(tmp_path, monkeypatch)
    read = []
    real = store_module.sha256_file
    monkeypatch.setattr(
        store_module, "sha256_file", lambda p, progress=None: read.append(p.name) or real(p)
    )
    assert len(store.scan_links()) == 2
    assert sorted(read) == ["my-high.gguf", "my-low.gguf"]


def test_scan_skips_a_file_it_cannot_read(tmp_path, monkeypatch):
    from irisecho_core import store as store_module

    store, _ = _two_file_store(tmp_path, monkeypatch)
    real = store_module.sha256_file

    def flaky(path, progress=None):
        if path.name == "my-high.gguf":
            raise PermissionError("in use")
        return real(path, progress)

    monkeypatch.setattr(store_module, "sha256_file", flaky)
    assert sorted(store.scan_links()) == ["low"]


def test_a_folder_added_during_a_scan_is_still_owed_a_scan(tmp_path, monkeypatch):
    store, _ = _two_file_store(tmp_path, monkeypatch)
    later = tmp_path / "later"
    later.mkdir()
    store.scan_links(on_link=lambda _id: store.linked_dirs.append(later))
    assert store.needs_scan()
    store.scan_links()
    assert not store.needs_scan()


def test_scan_leaves_different_files_of_the_same_size_alone(tmp_path, monkeypatch):
    from irisecho_core.store import Store

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    data = b"weights" * 100
    folder = tmp_path / "models"
    folder.mkdir()
    (folder / "other.safetensors").write_bytes(b"x" * len(data))
    reg, s = _registry_with(tmp_path, data)
    store = Store(reg, tmp_path / "own", [str(folder)])
    assert store.scan_links() == {}
    assert store.status(s).state == "missing"
