# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refusals instead of clamps, one error shape, and files that cannot be read."""

import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

from irisecho_core import controls, loras, metadata, paths, presets, registry, settings
from irisecho_core.controls import ControlError

H = {"X-IrisEcho": "1"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(home):
    from irisecho_core.server import create_app

    with TestClient(create_app(token="t0ken"), base_url="http://127.0.0.1") as c:
        c.get("/")
        yield c


def model(model_id):
    return registry.default().models[model_id]


def job_row(**fields):
    row = {
        "id": "abc123abc123",
        "model": "z-image-turbo",
        "kind": "image",
        "params": {},
        "status": "done",
        "progress": None,
        "message": None,
        "error": None,
        "created": time.time(),
        "started": None,
        "finished": time.time(),
        "outputs": [],
    }
    return {**row, **fields}


def assert_shape(r, status):
    """Every error: {"error": text} plus the old `detail` alias, and nothing else by accident."""
    assert r.status_code == status
    body = r.json()
    assert isinstance(body["error"], str) and body["error"]
    assert body["detail"] == body["error"]
    return body


# --- request bodies ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["{not json", "", "[1, 2]", '"text"', "7", "null"])
def test_a_body_that_is_not_a_json_object_is_a_400_with_a_message(client, raw):
    for path in ("/api/jobs", "/api/settings/linked-dirs", "/api/lan"):
        r = client.post(path, content=raw, headers={**H, "Content-Type": "application/json"})
        assert "JSON" in assert_shape(r, 400)["error"]
    r = client.patch(
        "/api/settings", content=raw, headers={**H, "Content-Type": "application/json"}
    )
    assert_shape(r, 400)


def test_a_missing_model_is_a_400_and_an_unknown_one_is_its_own_404(client):
    r = client.post("/api/jobs", json={"params": {}}, headers=H)
    assert "model is required" in assert_shape(r, 400)["error"]
    r = client.post("/api/jobs", json={"model": ["x"]}, headers=H)
    assert_shape(r, 400)
    r = client.post("/api/jobs", json={"model": "nope"}, headers=H)
    assert assert_shape(r, 404)["error"] == "unknown model nope"
    r = client.put("/api/presets", json={"values": {}}, headers=H)
    assert_shape(r, 400)
    r = client.put("/api/presets", json={"model": "nope", "name": "a"}, headers=H)
    assert assert_shape(r, 404)["error"] == "unknown model nope"


def test_every_error_path_has_the_one_shape(client):
    # HTTPException, unknown route, validation, NotReady (with needs), ControlError, auth.
    assert_shape(client.get("/api/jobs/none"), 404)
    assert_shape(client.get("/api/nothing-here"), 404)
    assert_shape(client.get("/api/jobs?limit=abc"), 422)
    r = client.post("/api/jobs", json={"model": "kokoro", "params": {"text": "hi"}}, headers=H)
    assert "download" in assert_shape(r, 409)["needs"]
    core = client.app.state.core
    core.model_status = lambda m: {"needs": []}
    r = client.post(
        "/api/jobs", json={"model": "z-image-turbo", "params": {"steps": 99999}}, headers=H
    )
    assert_shape(r, 400)
    r = client.post("/api/jobs", json={"model": "z-image-turbo", "params": [1]}, headers=H)
    assert_shape(r, 400)
    client.cookies.clear()
    assert_shape(client.get("/api/bootstrap"), 401)
    client.get("/")
    assert_shape(client.post("/api/settings/rescan"), 403)
    assert_shape(client.get("/api/system", headers={"Host": "evil.example"}), 403)


def test_the_error_message_is_what_the_old_scripts_read_too(client):
    body = client.get("/api/jobs/none").json()
    assert body["error"] == body["detail"] == "no such job"


# --- cancel, delete, favorite -----------------------------------------------------------------


def test_cancel_delete_and_favorite_say_why_they_did_not_work(client):
    core = client.app.state.core
    for call in (
        lambda: client.post("/api/jobs/none/cancel", json={}, headers=H),
        lambda: client.post("/api/jobs/none/favorite", json={"favorite": True}, headers=H),
        lambda: client.delete("/api/jobs/none", headers=H),
    ):
        assert assert_shape(call(), 404)["error"] == "no such job"

    core.db.insert(job_row())
    body = assert_shape(client.post("/api/jobs/abc123abc123/cancel", json={}, headers=H), 409)
    assert body["status"] == "done" and "finished" in body["error"]

    r = client.post("/api/jobs/abc123abc123/favorite", json={"favorite": True}, headers=H)
    assert r.json() == {"ok": True} and core.db.get("abc123abc123")["favorite"]

    core.db.insert(job_row(id="run000run000", status="running"))
    body = assert_shape(client.delete("/api/jobs/run000run000", headers=H), 409)
    assert body["status"] == "running" and "Cancel" in body["error"]

    r = client.delete("/api/jobs/abc123abc123", headers=H)
    assert r.json() == {"ok": True} and core.db.get("abc123abc123") is None


def test_a_queued_job_cancels_with_ok_true(client):
    core = client.app.state.core
    core.db.insert(job_row(id="que000que000", status="queued"))
    r = client.post("/api/jobs/que000que000/cancel", json={}, headers=H)
    assert r.json() == {"ok": True}
    assert core.db.get("que000que000")["status"] == "cancelled"


def test_deleting_a_job_whose_file_is_open_keeps_the_row_and_names_the_file(
    client, home, monkeypatch
):
    from pathlib import Path

    core = client.app.state.core
    files = [home / "one.png", home / "two.png", home / "three.png"]
    for f in files:
        f.write_bytes(b"x")
    core.db.insert(job_row(outputs=[{"path": str(f), "type": "image"} for f in files]))
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "two.png":
            raise PermissionError("in use")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    body = assert_shape(client.delete("/api/jobs/abc123abc123", headers=H), 409)
    assert "two.png is in use by another program" in body["error"]
    assert core.db.get("abc123abc123") is not None  # the row stays, so it can be tried again
    assert not files[0].exists() and files[1].exists() and not files[2].exists()  # the rest went
    monkeypatch.setattr(Path, "unlink", real)
    assert client.delete("/api/jobs/abc123abc123", headers=H).json() == {"ok": True}
    assert not files[1].exists()


# --- takes, durations, loop: refused with the range -------------------------------------------


def check(model_id, **params):
    return controls.check(model(model_id), params)[0]


@pytest.mark.parametrize(
    ("model_id", "params", "message"),
    [
        ("ace-step", {"takes": 10}, "takes must be 1 to 4 for this model (got 10)."),
        ("ace-step", {"takes": 0}, "takes must be 1 to 4 for this model (got 0)."),
        ("ace-step-xl", {"duration": 5}, "duration in seconds must be 10 to 600 for this model"),
        ("ace-step-xl-sft", {"duration": 601}, "10 to 600 for this model (got 601)"),
        ("yue2", {"duration": 301}, "duration in seconds must be 10 to 300 for this model"),
        ("minimax-music3", {"duration": 9.5}, "10 to 300 for this model (got 9.5)"),
        ("sa3-medium", {"duration": 999}, "10 to 300 for this model (got 999)"),
        ("sa3-medium", {"takes": 5}, "takes must be 1 to 4"),
        ("sa3-sfx", {"duration": 31}, "duration in seconds must be 0.5 to 30 for this model"),
        ("sa3-sfx", {"duration": 0.1}, "0.5 to 30 for this model (got 0.1)"),
        ("sa3-sfx", {"takes": 9}, "takes must be 1 to 4 for this model (got 9)."),
        ("chatterbox", {"takes": 5}, "takes must be 1 to 4 for this model (got 5)."),
        ("chatterbox-turbo", {"takes": "many"}, "takes must be a number."),
        ("wan22-t2v", {"seconds": 6}, "seconds must be 2 to 5 for this model (got 6)."),
        ("wan22-i2v", {"seconds": 1}, "seconds must be 2 to 5 for this model (got 1)."),
        ("wan22-i2v-1022", {"seconds": 9}, "seconds must be 2 to 5"),
        ("wan22-camera", {"seconds": 9}, "seconds must be 2 to 5"),
        ("hunyuan15-i2v", {"seconds": 9}, "seconds must be 2 to 5"),
        ("ltx25", {"seconds": 9}, "seconds must be 2 to 5"),
        ("ltx25", {"seconds": 2.5}, "seconds must be a whole number, 2 to 5."),
        ("ace-step", {"loop": True}, "loop needs bpm"),
        ("yue2", {"loop": True, "bpm": 0}, "loop needs bpm"),
        ("sa3-medium", {"loop": "yes"}, "loop needs bpm"),
    ],
)
def test_out_of_range_values_are_refused_with_the_range(model_id, params, message):
    with pytest.raises(ControlError) as e:
        check(model_id, **params)
    assert message in str(e.value)


def test_in_range_values_are_unchanged():
    assert check("ace-step", takes=4, duration=600, bpm=90, loop=True) == {
        "takes": 4,
        "duration": 600,
        "bpm": 90,
        "loop": True,
    }
    assert check("yue2", takes="2", duration=300.0) == {"takes": 2, "duration": 300.0}
    assert check("sa3-sfx", takes=1, duration=0.5) == {"takes": 1, "duration": 0.5}
    assert check("wan22-t2v", seconds=5, prompt="x") == {"seconds": 5, "prompt": "x"}
    assert check("ltx25", seconds=2.0) == {"seconds": 2}
    assert check("ace-step", loop=False) == {"loop": False}
    assert check("chatterbox", takes=None) == {"takes": None}  # left out means the default


def test_the_server_answers_400_before_anything_is_queued(client):
    core = client.app.state.core
    core.model_status = lambda m: {"needs": []}
    r = client.post("/api/jobs", json={"model": "sa3-sfx", "params": {"takes": 10}}, headers=H)
    assert "takes must be 1 to 4 for this model (got 10)." == assert_shape(r, 400)["error"]
    r = client.post(
        "/api/jobs", json={"model": "ace-step", "params": {"prompt": "x", "loop": True}}, headers=H
    )
    assert "loop needs bpm" in assert_shape(r, 400)["error"]
    assert core.db.list() == []


def test_defaults_are_declared_in_the_registry_not_in_engines():
    assert controls.declared(model("sa3-sfx").settings, "takes") == 3
    assert controls.declared(model("ace-step").settings, "takes") == 1
    assert controls.declared(model("sa3-sfx").settings, "duration") == 3
    assert controls.declared(model("wan22-t2v").settings, "seconds") == 3
    assert model("sa3-medium").settings["limits"]["duration"]["max"] == 300


def test_every_music_sfx_clone_and_video_model_declares_its_limits():
    for m in registry.default().models.values():
        names = set(m.settings.get("limits") or {})
        if m.kind in ("music", "sfx"):
            assert {"takes", "duration"} <= names, m.id
        elif m.kind == "clone":
            assert "takes" in names, m.id
        elif m.kind == "video":
            assert "seconds" in names, m.id


# --- edit params that are not what they should be ---------------------------------------------


def test_odd_edit_params_are_control_errors_not_crashes():
    edit = model("qwen-image-21-edit")
    assert controls.check(edit, {"image_url": "x", "prompt": "p"})[1]  # unknown, reported
    with pytest.raises(ControlError, match="up to 3 pictures"):
        controls.check(model("qwen-edit"), {"image4": "x"})
    for box in ("left", 5, {"a": 1}, [], [[0.1, 0.1, 0.5, 0.5], 3], [0.5]):
        with pytest.raises(ControlError, match="box takes four numbers"):
            controls.check(edit, {"image1": "x", "box": box})


# --- LoRA items --------------------------------------------------------------------------------


def test_lora_items_with_unknown_keys_are_refused():
    flux = model("flux-dev")
    with pytest.raises(ControlError, match="weight is not part of an add-on"):
        controls.check(flux, {"loras": [{"file": "x.safetensors", "weight": 0.5}]})
    wan = model("wan22-t2v")
    with pytest.raises(ControlError, match="is not part of an add-on"):
        controls.check(wan, {"loras": [{"high": "h.safetensors", "file": "f.safetensors"}]})
    out, _ = controls.check(flux, {"loras": [{"file": "x.safetensors", "strength": 0.5}]})
    assert out["loras"] == [{"file": "x.safetensors", "strength": 0.5}]


@pytest.mark.parametrize(
    "name",
    [
        "C:x.safetensors",
        "/x.safetensors",
        "..\\x.safetensors",
        "../x.safetensors",
        "\\x.safetensors",
    ],
)
def test_lora_names_that_leave_the_folder_are_refused(tmp_path, name):
    root = tmp_path / "loras"
    root.mkdir()
    (tmp_path / "x.safetensors").write_bytes(b"")
    with pytest.raises(loras.LoraError, match="cannot be used"):
        loras.resolve([root], name, "flux")


# --- unreadable data files ---------------------------------------------------------------------


def test_a_corrupt_settings_file_is_kept_aside_and_defaults_are_used(home):
    path = home / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    loaded = settings.load()
    assert loaded == settings.Settings()
    assert not path.exists() and (home / "settings.json.bad").read_text() == "{ this is not json"
    assert "settings.json" in (home / "logs" / "data.log").read_text(encoding="utf-8")
    settings.save(loaded)
    assert json.loads(path.read_text(encoding="utf-8"))["strip_metadata"] is True
    assert (home / "settings.json.bad").exists()  # the save did not touch it
    path.write_text("[1]", encoding="utf-8")  # not an object
    settings.load()
    assert (home / "settings.json.bad2").read_text() == "[1]"  # the first one is not overwritten


def test_the_app_starts_with_a_corrupt_settings_file(home):
    (home / "settings.json").write_bytes(b"\xff\xfe\x00bad")
    from irisecho_core.app import App

    assert App().settings.strip_metadata is True
    assert (home / "settings.json.bad").exists()


def test_a_corrupt_presets_file_is_kept_aside_not_wiped(home):
    (home / "presets.json").write_text('{"flux-dev": {', encoding="utf-8")
    assert presets.load() == {}
    assert (home / "presets.json.bad").read_text() == '{"flux-dev": {'
    data = presets.put(model("flux-dev"), "Soft", {"steps": 30})
    assert data == {"flux-dev": {"Soft": {"steps": 30}}}
    assert (home / "presets.json.bad").read_text() == '{"flux-dev": {'  # still there after a save


def test_missing_presets_and_settings_files_are_not_errors(home):
    assert presets.load() == {} and settings.load() == settings.Settings()
    assert not list(home.glob("*.bad*"))
    assert paths.move_aside(home / "nothing.json", "x") is None


# --- the registry loader -----------------------------------------------------------------------


def load_with(tmp_path, edit):
    raw = yaml.safe_load((registry.HERE / "models.yaml").read_text(encoding="utf-8"))
    edit(raw)
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return registry.load(path, registry.HERE / "lock.json")


def test_the_loader_refuses_a_mistyped_when_key(tmp_path):
    def edit(raw):
        raw["models"]["kokoro"]["variants"][0]["when"] = {"backed": "cuda"}

    with pytest.raises(ValueError, match="kokoro.*backed"):
        load_with(tmp_path, edit)


def test_the_loader_refuses_an_unknown_kind_or_engine(tmp_path):
    with pytest.raises(ValueError, match="unknown kind musik"):
        load_with(tmp_path, lambda raw: raw["models"]["kokoro"].update(kind="musik"))
    with pytest.raises(ValueError, match="unknown engine comfi"):
        load_with(tmp_path, lambda raw: raw["models"]["kokoro"].update(engine="comfi"))


def test_the_loader_refuses_bad_limits(tmp_path):
    def limits(value):
        return lambda raw: raw["models"]["sa3-sfx"]["settings"].update(limits=value)

    with pytest.raises(ValueError, match="unknown entry tkaes"):
        load_with(tmp_path, limits({"tkaes": {"min": 1, "max": 4, "default": 1}}))
    with pytest.raises(ValueError, match="min <= default <= max"):
        load_with(tmp_path, limits({"takes": {"min": 1, "max": 4, "default": 9}}))
    with pytest.raises(ValueError, match="needs a number"):
        load_with(tmp_path, limits({"takes": {"min": 1, "max": 4}}))


def test_the_shipped_registry_uses_real_hardware_fields_and_engines():
    from irisecho_core.engines.acestep import AceStepEngine
    from irisecho_core.engines.chatterbox import ChatterboxEngine
    from irisecho_core.engines.comfy import ComfyEngine
    from irisecho_core.engines.kokoro import KokoroEngine
    from irisecho_core.engines.promptwriter import PromptWriterEngine
    from irisecho_core.engines.stableaudio import StableAudioEngine

    ids = {
        e.id
        for e in (
            AceStepEngine,
            ChatterboxEngine,
            ComfyEngine,
            KokoroEngine,
            PromptWriterEngine,
            StableAudioEngine,
        )
    }
    assert ids == set(registry.ENGINES)


# --- settings in pictures ----------------------------------------------------------------------


def test_only_declared_controls_and_base_params_are_embedded(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(p)
    flux = model("flux-dev")
    params = {
        "prompt": "a fox",
        "aspect": "1:1",
        "steps": 20,
        "id": "my-own-label",
        "owner": "someone",
        "path": "C:/private/thing",
        "image1": "upload.png",
        "loras": [{"file": "style.safetensors", "strength": 0.5}],
    }
    metadata.embed(p, flux, params, 7, None)
    found = metadata.read(p)["params"]
    assert found == {
        "prompt": "a fox",
        "aspect": "1:1",
        "steps": 20,
        "loras": [{"file": "style.safetensors", "strength": 0.5}],
        "seed": 7,
    }
