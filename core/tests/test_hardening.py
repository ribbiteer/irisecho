# SPDX-License-Identifier: AGPL-3.0-or-later
"""Uploads, the Hugging Face token route, settings and the job list: the server's limits."""

import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

H = {"X-IrisEcho": "1"}
PNG = bytes.fromhex("89504e470d0a1a0a") + bytes(24)
JPEG = bytes.fromhex("ffd8ffe0") + bytes(24)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    from irisecho_core.server import create_app

    with TestClient(create_app(token="t0ken"), base_url="http://127.0.0.1") as c:
        c.get("/")
        yield c


def send(client, data, name="a.png", kind="image/png"):
    return client.post("/api/uploads", files={"file": (name, data, kind)}, headers=H)


def small_limits(monkeypatch):
    from irisecho_core import server

    monkeypatch.setattr(server, "MAX_UPLOAD", 1000)
    monkeypatch.setattr(server, "MULTIPART_SLACK", 1000)
    return server


# --- uploads -----------------------------------------------------------------------------------


def test_upload_type_comes_from_the_bytes_not_the_client(client, tmp_path):
    r = send(client, PNG, name="photo.txt", kind="text/plain")
    assert r.status_code == 200
    body = r.json()
    assert body["id"].endswith(".png") and body["type"] == "image/png" and body["size"] == len(PNG)
    assert (tmp_path / "uploads" / body["id"]).read_bytes() == PNG
    # A JPEG called a PNG is stored as the JPEG it is.
    assert send(client, JPEG, name="x.png", kind="image/png").json()["id"].endswith(".jpg")


@pytest.mark.parametrize(
    ("head", "ext"),
    [
        (b"RIFF" + bytes(4) + b"WEBPVP8 ", ".webp"),
        (b"RIFF" + bytes(4) + b"WAVEfmt ", ".wav"),
        (bytes(3) + b"\x18ftypmp42", ".mp4"),
        (b"fLaC" + bytes(4), ".flac"),
        (b"OggS" + bytes(2), ".ogg"),
        (b"ID3" + bytes(3), ".mp3"),
        (bytes.fromhex("fffb9064"), ".mp3"),
    ],
)
def test_upload_accepts_the_images_and_sounds_the_app_takes(client, head, ext):
    r = send(client, head + bytes(16), kind="application/octet-stream")
    assert r.json()["id"].endswith(ext)


def test_upload_refuses_what_is_not_a_picture_or_a_sound(client):
    for data in (b"just some text", b"<svg></svg>", b"MZ" + bytes(20), b""):
        r = send(client, data)  # the client's word ("image/png") is no help
        assert r.status_code == 415, data
        assert r.json()["error"] == r.json()["detail"] and "PNG" in r.json()["error"]


def test_upload_too_large_is_refused_while_reading(client, monkeypatch):
    small_limits(monkeypatch)
    assert send(client, PNG + bytes(100)).status_code == 200
    r = send(client, PNG + bytes(1500))  # over the cap, within the form's slack
    assert r.status_code == 413 and "larger than" in r.json()["error"]
    assert send(client, PNG + bytes(5000)).status_code == 413


def test_upload_size_announced_is_refused_before_the_body_is_read(client, monkeypatch):
    server = small_limits(monkeypatch)
    read = []
    real = server.Request.form

    def spy(self, *args, **kwargs):
        read.append(1)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(server.Request, "form", spy)
    r = client.post(
        "/api/uploads",
        content=b"x",
        headers={**H, "content-type": "multipart/form-data; boundary=x", "content-length": "99999"},
    )
    assert r.status_code == 413 and read == []


def test_upload_that_never_says_its_size_is_cut_off(client, monkeypatch):
    small_limits(monkeypatch)

    def body():
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n\r\n'
        yield PNG
        for _ in range(100):  # 100 x 100 bytes against a 2000-byte allowance
            yield bytes(100)
        yield b"\r\n--x--\r\n"

    r = client.post(
        "/api/uploads",
        content=body(),
        headers={**H, "content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 413


def test_upload_without_a_file_is_a_400(client):
    r = client.post("/api/uploads", data={"other": "x"}, headers=H)
    assert r.status_code == 400 and "error" in r.json()


def test_old_unused_uploads_are_removed_and_the_rest_kept(client, tmp_path, monkeypatch):
    from irisecho_core import app as app_module

    core = client.app.state.core
    folder = tmp_path / "uploads"
    folder.mkdir(exist_ok=True)
    long_ago = time.time() - app_module.UPLOAD_KEEP - 3600

    def put(name):
        path = folder / name
        path.write_bytes(PNG)
        return path

    stale, used, queued = put("aaaaaaaa11.png"), put("bbbbbbbb22.png"), put("cccccccc33.png")
    fresh, other = put("dddddddd44.png"), put("notes.txt")
    base = {"model": "z-image-turbo", "kind": "image", "created": time.time()}
    core.db.insert({**base, "id": "j1", "params": {"image1": used.name}, "status": "done"})
    core.db.insert({**base, "id": "j2", "params": {"image1": queued.name}, "status": "queued"})

    # Age is the later of a file's modified and created times; the files here were just made.
    assert core.prune_uploads(every=0) == 0 and stale.exists()

    real = Path.stat

    def aged(self, *args, **kwargs):
        info = real(self, *args, **kwargs)
        if self.parent == folder and self.name != fresh.name:
            return os.stat_result((*info[:7], long_ago, long_ago, long_ago))
        return info

    monkeypatch.setattr(Path, "stat", aged)
    assert core.prune_uploads(every=0) == 1
    monkeypatch.undo()
    assert not stale.exists()
    # A month, not a week: an agent stages files well before the jobs that use them.
    assert app_module.UPLOAD_KEEP == 30 * 24 * 3600
    # Each file removed is in the core log by name, size and age; the kept ones are not.
    logged = (tmp_path / "logs" / "core.log").read_text(encoding="utf-8")
    assert f"Removed the upload {stale.name} ({len(PNG)} bytes, 30 days old)" in logged
    assert used.name not in logged and fresh.name not in logged
    # Used by a finished job, used by a queued one, new, and not an upload name at all.
    assert used.exists() and queued.exists() and fresh.exists() and other.exists()


def test_pruning_runs_at_most_once_in_a_while(client, tmp_path):
    core = client.app.state.core
    core.prune_uploads()
    (tmp_path / "uploads" / "eeeeeeee55.png").write_bytes(PNG)
    assert core.prune_uploads() == 0


# --- hugging face token ------------------------------------------------------------------------


@pytest.fixture
def hf(client, monkeypatch):
    """The token route with Hugging Face and the keychain faked."""
    from irisecho_core import credentials

    state = {"answer": httpx.Response(200, json={"name": "someone"}), "saved": []}
    real = httpx.AsyncClient

    def handler(request):
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(credentials, "available", lambda: True)
    monkeypatch.setattr(credentials, "store", lambda name, value: state["saved"].append(name))
    state["put"] = lambda: client.put(
        "/api/credentials/huggingface", json={"token": "hf_fake"}, headers=H
    )
    return state


def test_token_is_checked_and_saved(hf):
    r = hf["put"]()
    assert r.status_code == 200 and r.json() == {"ok": True, "account": "someone"}
    assert hf["saved"] == ["huggingface"]


def test_token_offline_says_so(hf):
    hf["answer"] = httpx.ConnectError("no route")
    r = hf["put"]()
    assert r.status_code == 503 and "internet connection" in r.json()["error"]
    hf["answer"] = httpx.ReadTimeout("slow")
    assert hf["put"]().status_code == 503
    assert hf["saved"] == []


def test_token_refused_or_hugging_face_down(hf):
    hf["answer"] = httpx.Response(401)
    r = hf["put"]()
    assert r.status_code == 400 and "did not accept" in r.json()["error"]
    hf["answer"] = httpx.Response(503)
    r = hf["put"]()
    assert r.status_code == 502 and "Try again later" in r.json()["error"]


def test_token_without_a_keychain_says_so(hf, monkeypatch):
    from irisecho_core import credentials

    monkeypatch.setattr(credentials, "available", lambda: False)
    r = hf["put"]()
    assert r.status_code == 501 and "HF_TOKEN" in r.json()["error"]


def test_token_that_the_keychain_refuses_to_keep_says_so(hf, monkeypatch):
    from irisecho_core import credentials

    class Broken:
        def set_password(self, *args):
            raise RuntimeError("locked")

    monkeypatch.undo()  # the real store, over a keychain that fails
    monkeypatch.setattr(credentials, "available", lambda: True)
    monkeypatch.setattr(credentials, "_keyring", lambda: Broken())
    real = httpx.AsyncClient
    ok = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=ok))
    r = hf["put"]()
    assert r.status_code == 501 and "secure storage" in r.json()["error"]


# --- settings and the job list -----------------------------------------------------------------


def test_settings_are_checked_before_any_is_changed(client, tmp_path, monkeypatch):
    core = client.app.state.core
    applied = []

    async def set_gpu(uuid):
        applied.append(uuid)

    monkeypatch.setattr(core, "set_gpu", set_gpu)
    a_file = tmp_path / "not-a-folder"
    a_file.write_text("x")
    body = {"gpu": "GPU-x", "advanced": True, "outputs_dir": str(a_file)}
    assert client.patch("/api/settings", json=body, headers=H).status_code == 400
    assert applied == [] and client.get("/api/settings").json()["advanced"] is False
    monkeypatch.undo()
    # An unknown card is refused by the same call, again before anything changes.
    r = client.patch("/api/settings", json={"gpu": "GPU-nope", "advanced": True}, headers=H)
    assert r.status_code == 400 and "not found" in r.json()["error"]
    assert client.get("/api/settings").json()["advanced"] is False


def test_job_list_limit_is_bounded(client):
    for bad in ("-1", "0", "501", "100000"):
        r = client.get(f"/api/jobs?limit={bad}")
        assert r.status_code == 400 and "between 1 and 500" in r.json()["error"], bad
    assert client.get("/api/jobs?limit=500").status_code == 200
    assert client.get("/api/jobs?limit=1").status_code == 200
    assert client.get("/api/jobs").status_code == 200
