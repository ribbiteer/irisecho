# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    from irisecho_core.server import create_app

    with TestClient(create_app(token="t0ken"), base_url="http://127.0.0.1") as c:
        yield c


def test_page_sets_session_cookie(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "irisecho_session" in r.cookies


def test_api_requires_session(client):
    client.cookies.clear()
    assert client.get("/api/bootstrap").status_code == 401


def test_bootstrap_with_session(client):
    client.get("/")
    data = client.get("/api/bootstrap").json()
    assert {m["id"] for m in data["models"]} >= {"kokoro", "z-image-turbo"}
    assert data["jobs"] == []


def test_mutation_needs_header(client):
    client.get("/")
    assert client.post("/api/settings/rescan").status_code == 403
    assert client.post("/api/settings/rescan", headers={"X-IrisEcho": "1"}).status_code == 200


def test_foreign_host_rejected(client):
    r = client.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 403


def test_unknown_model_is_404(client):
    client.get("/")
    r = client.post("/api/jobs", json={"model": "nope"}, headers={"X-IrisEcho": "1"})
    assert r.status_code == 404


def test_not_ready_model_is_409_with_needs(client):
    client.get("/")
    r = client.post(
        "/api/jobs", json={"model": "kokoro", "params": {"text": "hi"}}, headers={"X-IrisEcho": "1"}
    )
    assert r.status_code == 409
    assert "download" in r.json()["needs"]


def test_the_agent_guide_names_this_installation(client, tmp_path):
    r = client.get("/agents.md")
    assert r.status_code == 200
    from irisecho_core import guide

    assert str(tmp_path) in r.text and guide.cli_command() in r.text
    assert "$cli" not in r.text and "$data" not in r.text
    client.get("/")
    assert client.get("/api/bootstrap").json()["guide"]["path"] == str(tmp_path / "AGENTS.md")


def test_every_command_the_guide_mentions_exists(client):
    import re

    from irisecho_core import cli

    text = client.get("/agents.md").text
    used = set(re.findall(r"^irisecho (\w+)", text, flags=re.MULTILINE))
    used |= set(re.findall(r"^\| `([a-z]+) [A-Z]", text, flags=re.MULTILINE))  # the commands table
    assert used >= {"models", "image", "say", "music", "setup", "link", "api", "clone", "write"}
    for command in used:
        with pytest.raises(SystemExit) as stop:  # --help exits 0; an unknown command exits 2
            cli.main([command, "--help"])
        assert stop.value.code == 0, command


def test_api_command_says_when_nothing_is_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "empty"))
    from irisecho_core import cli

    assert cli.main(["api", "GET", "/api/models"]) == 1
    assert "not running" in capsys.readouterr().err


def test_api_command_accepts_every_way_of_writing_a_path():
    from irisecho_core.cli import api_path

    forms = ["models", "api/models", "/api/models", "C:/Program Files/Git/api/models"]
    assert {api_path(f) for f in forms} == {"/api/models"}
    assert api_path("jobs?kind=image&limit=5") == "/api/jobs?kind=image&limit=5"


def _job(db, job_id, model, kind, params, created, favorite=0):
    db.insert(
        {
            "id": job_id,
            "model": model,
            "kind": kind,
            "params": params,
            "status": "done",
            "created": created,
            "favorite": favorite,
        }
    )


def test_library_search(client):
    client.get("/")
    db = client.app.state.core.db
    _job(db, "a1", "z-image-turbo", "image", {"prompt": "A red fox on a basalt cliff"}, 1000)
    _job(db, "a2", "flux-dev", "image", {"prompt": "Foxes playing in snow"}, 2000, favorite=1)
    _job(db, "a3", "kokoro", "voice", {"text": "The quick brown fox jumps"}, 3000)
    _job(db, "a4", "kokoro", "voice", {"text": "Nothing to see"}, 4000)

    def ids(**params):
        return [j["id"] for j in client.get("/api/jobs", params=params).json()]

    assert ids(q="fox") == ["a3", "a2", "a1"]  # stemmed: "foxes" matches "fox"
    assert ids(q="fo") == ["a3", "a2", "a1"]  # the last word is a prefix
    assert ids(q="red fox") == ["a1"]  # every word must match
    assert ids(q="fox", kind="image") == ["a2", "a1"]
    assert ids(q="fox", model="flux-dev") == ["a2"]
    assert ids(q="fox", favorite="true") == ["a2"]
    assert ids(q="fox", after=1500, before=2500) == ["a2"]
    assert ids(q="kokoro") == ["a4", "a3"]  # the model name is searchable too
    assert ids(q='"; DROP TABLE jobs; --') == []
    assert ids(q="!!!") == []
    assert len(ids()) == 4


def test_search_follows_edits_and_deletes(client):
    client.get("/")
    db = client.app.state.core.db
    _job(db, "b1", "z-image-turbo", "image", {"prompt": "lighthouse"}, 1)
    assert [j["id"] for j in db.list(q="lighthouse")] == ["b1"]
    db.update("b1", params={"prompt": "harbor"})
    assert db.list(q="lighthouse") == []
    assert [j["id"] for j in db.list(q="harbor")] == ["b1"]
    db.delete("b1")
    assert db.list(q="harbor") == []


def test_settings_reject_unusable_folder(client, tmp_path):
    client.get("/")
    a_file = tmp_path / "not-a-folder"
    a_file.write_text("x")
    for key in ("models_dir", "outputs_dir"):
        r = client.patch("/api/settings", json={key: str(a_file)}, headers={"X-IrisEcho": "1"})
        assert r.status_code == 400
    assert client.get("/api/settings").json()["models_dir"] == ""


def test_saved_bad_folder_does_not_block_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    from irisecho_core import settings

    a_file = tmp_path / "not-a-folder"
    a_file.write_text("x")
    s = settings.Settings(models_dir=str(a_file), outputs_dir=str(a_file))
    assert s.models_path.is_dir() and s.models_path != a_file
    assert s.outputs_path.is_dir() and s.outputs_path != a_file
