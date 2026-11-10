# SPDX-License-Identifier: AGPL-3.0-or-later
"""The command line as a contract: what a script or agent can rely on.

The guide (AGENTS.md) and the README promise commands, flags, JSON fields and exit
codes; these tests hold them to it, with no GPU, network or engines.
"""

import argparse
import asyncio
import io
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from irisecho_core import cli, credentials, guide, paths, registry

REPO = Path(__file__).resolve().parents[2]
GUIDE = Path(cli.__file__).with_name("AGENTS.md")
README = REPO / "README.md"
JSON_KEYS = {"id", "label", "status", "model", "outputs", "seed", "error", "needs", "warnings"}
GENERATING = ("image", "say", "clone", "music", "sfx", "3d")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return tmp_path


class FakeBackend:
    """What both real backends offer, with nothing behind it."""

    def __init__(self, tmp, blocked=None, warnings=(), fail=False, rows=()):
        self.tmp, self.blocked, self.warnings, self.fail = tmp, blocked, list(warnings), fail
        self.rows = list(rows)
        self.ready_calls, self.submitted = [], []

    async def wait_ready(self, model_id, accept_license=False):
        self.ready_calls.append((model_id, accept_license))
        if self.blocked:
            raise self.blocked

    async def submit(self, model_id, params):
        self.submitted.append((model_id, params))
        return {
            "id": f"job{len(self.submitted)}",
            "status": "queued",
            "outputs": [],
            "warnings": self.warnings,
        }

    async def job(self, job_id):
        if self.fail:
            return {"id": job_id, "status": "failed", "error": "boom", "outputs": []}
        out = self.tmp / f"{job_id}.glb"
        out.write_bytes(b"glTF")
        return {
            "id": job_id,
            "status": "done",
            "error": None,
            "message": None,
            "progress": None,
            "outputs": [{"path": str(out), "seed": 123, "mesh": {"printable": True, "faces": 9}}],
        }

    async def models(self):
        return self.rows


@pytest.fixture
def backend(home, monkeypatch):
    fake = FakeBackend(home)

    def with_backend(fn):
        return asyncio.run(fn(fake))

    monkeypatch.setattr(cli, "with_backend", with_backend)
    return fake


@pytest.fixture
def never_started(home, monkeypatch):
    """A command that gets as far as starting a backend has not checked its input first."""

    def with_backend(fn):
        raise AssertionError("started the app (and so would have waited for downloads)")

    monkeypatch.setattr(cli, "with_backend", with_backend)


def json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def picture(home, name="in.png"):
    path = home / name
    path.write_bytes(b"\x89PNG")
    return str(path)


# --- the guide and the README only show commands that parse ----------------------------------


def fenced_commands(path):
    """Every line in a fenced code block that starts with `irisecho `, as argv."""
    found, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif fenced and line.startswith("irisecho "):
            words = shlex.split(line, comments=True)
            for stop in ("<", ">", "|"):  # redirections are the shell's
                if stop in words:
                    words = words[: words.index(stop)]
            found.append(words[1:])
    return found


def parse(argv):
    return cli.build_parser().parse_args(argv)


@pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
def test_every_command_example_is_a_valid_command_line(path):
    examples = fenced_commands(path)
    assert examples
    for argv in examples:
        try:
            parse(argv)
        except SystemExit as stop:
            pytest.fail(f"{path.name}: irisecho {' '.join(argv)} is a usage error ({stop.code})")


def command_flags():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {
        name: {o for a in p._actions for o in a.option_strings if o.startswith("--")}
        for name, p in sub.choices.items()
    }


def test_every_flag_in_the_guide_exists():
    flags = command_flags()
    everything = set().union(*flags.values())
    text = GUIDE.read_text(encoding="utf-8")
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", text))
    assert named - everything == set()
    # a command's row in the table names only flags that command has
    for line in text.splitlines():
        m = re.match(r"^\| `(\w+)( [^`]*)?` \|", line)
        if m and m.group(1) in flags:
            missing = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", line)) - flags[m.group(1)]
            assert missing == set(), f"{m.group(1)}: {missing}"


def test_every_command_that_makes_or_sets_up_takes_accept_license():
    flags = command_flags()
    for command in (*GENERATING, "setup", "write"):
        assert "--accept-license" in flags[command], command


def test_the_guide_quotes_the_license_sentence_and_the_json_shape():
    text = GUIDE.read_text(encoding="utf-8")
    assert cli.LICENSE_ASK in " ".join(text.split())
    block = re.search(r"```json\n(.*?)\n```", text, flags=re.DOTALL).group(1)
    assert set(json.loads(block)) == JSON_KEYS
    for word in ("HF_TOKEN", "huggingface_source", "models --controls", "exit code"):
        assert word in text
    # the one upload-name rule, with its own examples
    for name in re.findall(r"`([0-9a-f]{8,64}\.[a-z0-9]{2,5})`", text):
        assert paths.upload(name).name == name


def test_the_guide_names_every_model_and_no_longer_carries_the_controls_table():
    text = guide.text()
    for model_id in registry.default().models:
        assert f"`{model_id}`" in text, model_id
    assert "| Model | Param |" not in text


def test_from_is_for_the_image_models_that_start_from_a_picture():
    takers = cli.picture_models()
    assert set(takers) == {"flux-schnell", "flux-dev", "flux-krea", "qwen-image-21"}
    text = GUIDE.read_text(encoding="utf-8")
    assert all(f"`{m}`" in text for m in takers)


# --- --json: one object per job, also for a job that never ran -----------------------------


def test_json_for_a_job_that_finished(backend, capsys):
    backend.warnings = ["Z-Image Turbo does not use 'x'; it has no effect."]
    code = cli.main(["image", "a fox", "--count", "2", "--json", "--set", "x=1"])
    out, err = capsys.readouterr()
    assert code == 0
    objects = json_lines(out)
    assert len(objects) == 2 and all(set(o) == JSON_KEYS for o in objects)
    first = objects[0]
    assert first["status"] == "done" and first["seed"] == 123 and first["error"] is None
    assert first["needs"] == [] and first["warnings"] == backend.warnings
    assert "warning: Z-Image Turbo does not use 'x'" in err
    assert "\r" not in out and "label" in out.splitlines()[0]


def test_text_output_is_clean_result_lines_with_progress_elsewhere(backend, capsys):
    assert cli.main(["image", "a fox"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == f"a fox: {backend.tmp / 'job1.glb'}"
    assert "\r" not in out and out == out.strip() + "\n"


def test_json_for_a_job_that_failed(backend, capsys):
    backend.fail = True
    assert cli.main(["image", "a fox", "--json"]) == 1
    out, err = capsys.readouterr()
    (obj,) = json_lines(out)
    assert obj["status"] == "failed" and obj["error"] == "boom" and obj["id"] == "job1"
    assert "failed: boom" in err


def test_json_for_jobs_that_failed_before_they_were_queued(backend, capsys):
    backend.blocked = cli.CliError(cli.license_notice("M", LIC), ["license"])
    assert cli.main(["image", "a fox", "--count", "3", "--json"]) == 1
    out, err = capsys.readouterr()
    objects = json_lines(out)
    assert len(objects) == 3  # one for each planned job
    for obj in objects:
        assert set(obj) == JSON_KEYS and obj["status"] == "failed" and obj["id"] is None
        assert obj["needs"] == ["license"] and cli.LICENSE_ASK in obj["error"]
    assert cli.LICENSE_ASK in err and backend.submitted == []


LIC = {"name": "Some License", "url": "https://example.org/license", "summary": "Read it."}


def test_json_names_a_cue_id_as_the_label(backend, capsys, home):
    sheet = home / "cues.txt"
    sheet.write_text("# lines\nintro|Welcome back.\n", encoding="utf-8")
    assert cli.main(["say", "--file", str(sheet), "--json"]) == 0
    assert json_lines(capsys.readouterr().out)[0]["label"] == "intro"


def test_3d_json_has_the_same_shape_and_a_failure_gets_an_object(backend, capsys, home):
    pic = picture(home)
    assert cli.main(["3d", pic, "--model", "trellis2", "--json", "--out", str(home / "o")]) == 0
    (obj,) = json_lines(capsys.readouterr().out)
    assert set(obj) == JSON_KEYS | {"glb", "printable", "reasons"}
    assert obj["status"] == "done" and obj["printable"] is True and obj["error"] is None
    assert obj["outputs"][0].endswith(".glb") and Path(obj["outputs"][0]).is_file()

    backend.blocked = cli.CliError("Pixal3D cannot run on this machine.", ["unsupported"])
    assert cli.main(["3d", pic, "--model", "trellis2", "--json"]) == 1
    (obj,) = json_lines(capsys.readouterr().out)
    assert obj["status"] == "failed" and obj["needs"] == ["unsupported"]
    assert set(obj) == JSON_KEYS | {"glb", "printable", "reasons"}


def test_3d_back_with_a_model_that_builds_from_one_picture_is_refused(backend, capsys, home):
    pic = picture(home)
    for model in ("trellis2", "pixal3d-single"):
        assert cli.main(["3d", pic, "--back", pic, "--model", model, "--json"]) == 2
        out, err = capsys.readouterr()
        assert "--back" in err and json_lines(out)[0]["status"] == "failed"
    assert backend.ready_calls == []
    # with auto or pixal3d, --back means Pixal3D, as the guide says
    assert cli.main(["3d", pic, "--back", pic, "--json"]) == 0
    assert backend.ready_calls[-1][0] == "pixal3d"


# --- exit codes: 2 for a call the caller can fix, 1 for what could not be done ----------


@pytest.mark.parametrize(
    "argv, words",
    [
        (["image", "x", "--model", "no-such-model"], "unknown model no-such-model"),
        (["music", "x", "--takes", "9"], "1 to 4"),
        (["music", "x", "--loop"], "bpm"),
        (["image", "x", "--model", "flux-dev", "--set", "steps=9999"], "Steps"),
        (["image", "x", "--set", "oops"], "NAME=VALUE"),
        (["say"], "Give a line"),
        (["image", "x", "--from", "missing.png", "--model", "flux-dev"], "No such file"),
        (["image", "x", "--from", "missing.png"], "--from works with"),
        (["image", "x", "--model", "qwen-image-21", "--size", "64x64"], "--size works with"),
        (["image", "x", "--size", "big", "--model", "flux-dev"], "WIDTHxHEIGHT"),
        (["music", "--file", "missing.txt"], "Could not read"),
    ],
)
def test_bad_input_is_exit_2_with_a_message_before_anything_starts(
    never_started, capsys, argv, words
):
    assert cli.main([*argv, "--json"] if argv[0] != "say" else argv) == 2
    out, err = capsys.readouterr()
    assert words in err and "Traceback" not in err


def test_every_command_checks_its_input_before_it_waits_for_downloads(never_started, capsys):
    # (also in --json mode: the failure is reported as an object, not lost)
    assert cli.main(["music", "x", "--takes", "9", "--json"]) == 2
    (obj,) = json_lines(capsys.readouterr().out)
    assert obj["status"] == "failed" and "1 to 4" in obj["error"] and obj["needs"] == []


def test_a_bad_cue_sheet_is_exit_2(never_started, home, capsys):
    sheet = home / "cues.txt"
    sheet.write_text("intro|thirty|90|calm\n", encoding="utf-8")
    assert cli.main(["music", "--file", str(sheet)]) == 2
    assert "id|seconds|bpm|tags" in capsys.readouterr().err


def test_what_could_not_be_done_is_exit_1(backend, capsys):
    backend.blocked = cli.CliError("Installing comfy failed", ["engine"])
    assert cli.main(["image", "x"]) == 1
    assert "Installing comfy failed" in capsys.readouterr().err


def test_a_command_line_that_does_not_parse_is_exit_2(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["music", "--takes", "many"])
    assert stop.value.code == 2


def test_local_backend_turns_the_apps_refusals_into_clean_errors():
    from irisecho_core.app import NotReady, UnknownModel
    from irisecho_core.controls import ControlError

    def fake(exc):
        def submit(model_id, params):
            raise exc

        return cli.Local(SimpleNamespace(submit=submit))

    for exc in (ControlError("Takes must be 1 to 4."), UnknownModel("unknown model x")):
        with pytest.raises(cli.UsageError) as raised:
            asyncio.run(fake(exc).submit("m", {}))
        assert raised.value.code == 2 and str(raised.value) == str(exc)
    with pytest.raises(cli.CliError) as raised:
        asyncio.run(fake(NotReady("M is not ready yet.", ["download"])).submit("m", {}))
    assert raised.value.code == 1 and raised.value.needs == ["download"]


# --- licenses and tokens --------------------------------------------------------------------


class FakeApp:
    """Just enough App for `wait_ready` to run against."""

    def __init__(self, needs, accepted=None):
        self.needs, self.accepted = list(needs), accepted
        self.install_tasks, self.install_logs, self.prepared = {}, {}, 0
        self.model_ = SimpleNamespace(id="m", name="Model M", engine="e")

    def model(self, model_id):
        return self.model_

    def model_status(self, model):
        lic = {"id": "lic", "name": "Some License", "url": "https://x.test/l", "summary": "Read."}
        return {
            "needs": [n for n in self.needs if n != "accepted"],
            "ready": self.prepared > 0 and "license" not in self.needs,
            "license": lic,
            "downloads": [],
            "have": 0,
            "size": 1,
        }

    def accept_license(self, license_id):
        self.accepted = license_id
        self.needs.remove("license")

    def prepare(self, model_id):
        self.prepared += 1


def test_local_license_stop_says_what_to_do_and_names_no_shortcut(home, capsys):
    app = FakeApp(["license", "download"])
    with pytest.raises(cli.CliError) as stop:
        asyncio.run(cli.wait_ready(app, "m"))
    message = str(stop.value)
    assert stop.value.needs == ["license"] and cli.LICENSE_ASK in message
    assert "Some License" in message and "https://x.test/l" in message
    assert app.accepted is None and app.prepared == 0
    asyncio.run(cli.wait_ready(app, "m", accept_license=True))
    assert app.accepted == "lic"


def test_music_passes_the_license_flag_on(backend):
    assert cli.main(["music", "x", "--model", "yue2"]) == 0
    assert backend.ready_calls == [("yue2", False)]
    assert cli.main(["music", "x", "--model", "yue2", "--accept-license"]) == 0
    assert backend.ready_calls[-1] == ("yue2", True)


def test_a_token_from_the_environment_is_announced_and_never_printed(home, monkeypatch, capsys):
    monkeypatch.setenv("HF_TOKEN", "hf_secret_value")
    asyncio.run(cli.wait_ready(FakeApp(["download"]), "m"))
    out, err = capsys.readouterr()
    assert "Using the Hugging Face token from the HF_TOKEN environment variable." in err
    assert "hf_secret_value" not in out + err
    monkeypatch.delenv("HF_TOKEN")
    monkeypatch.setattr(credentials, "get", lambda name=credentials.HF: None)
    asyncio.run(cli.wait_ready(FakeApp(["download"]), "m"))
    assert "HF_TOKEN" not in capsys.readouterr().err


def test_where_the_token_comes_from(monkeypatch):
    monkeypatch.setattr(credentials, "get", lambda name=credentials.HF: "hf_x")
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    assert credentials.source() == "environment"
    monkeypatch.delenv("HF_TOKEN")
    assert credentials.source() == "saved"
    monkeypatch.setattr(credentials, "get", lambda name=credentials.HF: None)
    assert credentials.source() is None


def test_the_app_reports_the_token_source_without_the_token(home, monkeypatch):
    from fastapi.testclient import TestClient

    from irisecho_core.server import create_app

    monkeypatch.setenv("HF_TOKEN", "hf_secret_value")
    with TestClient(create_app(token="t"), base_url="http://127.0.0.1") as client:
        client.get("/")
        r = client.get("/api/bootstrap")
    assert r.json()["credentials"]["huggingface_source"] == "environment"
    assert r.json()["credentials"]["huggingface"] is True
    assert "hf_secret_value" not in r.text


# --- input files ----------------------------------------------------------------------------


def test_cue_sheets_and_lyrics_lose_a_byte_order_mark(home):
    sheet = home / "cues.txt"
    sheet.write_bytes("﻿# first line\nintro|Hello\n".encode())
    assert cli.read_cues(sheet) == [("intro", "Hello")]
    music = home / "music.txt"
    music.write_bytes("﻿intro|10||calm piano\n".encode())
    assert cli.read_music_cues(music)[0]["id"] == "intro"
    assert cli.read_text(music).startswith("intro")


def test_stdin_is_read_as_utf_8(monkeypatch):
    body = '{"prompt": "café"}'.encode()
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"\xef\xbb\xbf" + body)))
    assert json.loads(cli.read_stdin()) == {"prompt": "café"}
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"\xff\xfe")))
    with pytest.raises(cli.UsageError):
        cli.read_stdin()


def test_from_works_for_an_image_model_that_is_not_flux(backend, home):
    assert cli.main(["image", "x", "--model", "qwen-image-21", "--from", picture(home)]) == 0
    model, params = backend.submitted[0]
    assert model == "qwen-image-21" and params["denoise"] == 0.2
    assert paths.UPLOAD_NAME.fullmatch(params["image1"])
    assert (paths.sub("uploads") / params["image1"]).is_file()


def test_link_remove_works_for_a_folder_that_is_gone(home, monkeypatch, capsys):
    gone = home / "removed-models"
    resolved = str(gone.resolve())

    class Linking:
        dirs = [resolved, "other"]

        async def linked_dirs(self):
            return list(self.dirs)

        async def link(self, dirs):
            self.dirs = dirs

        async def models(self):
            return []

    fake = Linking()
    monkeypatch.setattr(cli, "with_backend", lambda fn: asyncio.run(fn(fake)))
    assert cli.main(["link", "--remove", str(gone)]) == 0
    assert fake.dirs == ["other"]
    assert cli.main(["link", str(gone)]) == 2  # adding one still needs the folder
    assert "Not a folder" in capsys.readouterr().err


# --- a lost connection ----------------------------------------------------------------------


def remote_with(handler):
    remote = cli.Remote(1, "t")
    remote.http = httpx.Client(
        base_url="http://127.0.0.1:1", transport=httpx.MockTransport(handler)
    )
    return remote


def test_a_dropped_connection_is_a_clean_error_after_a_few_tries(monkeypatch):
    monkeypatch.setattr(cli, "RETRY_WAIT", 0)
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ConnectError("refused")

    remote = remote_with(handler)
    with pytest.raises(cli.CliError, match="Lost the connection") as lost:
        remote.call("GET", "/api/jobs/x")
    assert lost.value.code == 1 and len(calls) == cli.GET_TRIES
    calls.clear()
    with pytest.raises(cli.CliError, match="Lost the connection"):
        remote.call("POST", "/api/jobs", {})
    assert calls == ["POST"]  # a POST may have worked: never repeated
    with pytest.raises(cli.CliError, match="Lost the connection"):
        asyncio.run(cli.poll(remote, {"id": "x", "status": "running"}))


def test_a_few_failed_polls_are_tolerated(monkeypatch):
    monkeypatch.setattr(cli, "RETRY_WAIT", 0)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={"id": "x", "status": "done", "outputs": []})

    job = asyncio.run(cli.poll(remote_with(handler), {"id": "x", "status": "running"}))
    assert job["status"] == "done"


def test_the_apps_refusals_keep_their_kind():
    def handler(request):
        if request.url.path.endswith("/bad"):
            return httpx.Response(400, json={"error": "Takes must be 1 to 4."})
        return httpx.Response(409, json={"error": "M is not ready yet.", "needs": ["license"]})

    remote = remote_with(handler)
    with pytest.raises(cli.UsageError, match="1 to 4"):
        remote.call("POST", "/api/bad", {})
    with pytest.raises(cli.CliError) as stop:
        remote.call("POST", "/api/jobs", {})
    assert stop.value.code == 1 and stop.value.needs == ["license"]


# --- uploads: one rule for every engine -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["3fa2b1c4.png", "0" * 32 + ".wav", "5d0e7a91c3b84f26.flac", "a" * 64 + ".mp4"],
)
def test_upload_names_that_follow_the_rule(home, name):
    assert paths.upload(name) == paths.sub("uploads") / name


@pytest.mark.parametrize(
    "name",
    [
        "../3fa2b1c4.png",
        "sub/3fa2b1c4.png",
        "sub\\3fa2b1c4.png",
        "3FA2B1C4.png",
        "3fa2b1c4.PNG",
        "3fa2b1c.png",  # 7 digits
        "a" * 65 + ".png",
        "3fa2b1c4",
        "3fa2b1c4.p",
        "3fa2b1c4.pngpng",
        "voice.wav",
        "",
        None,
        7,
    ],
)
def test_upload_names_that_do_not(home, name):
    with pytest.raises(ValueError, match="lowercase hex digits"):
        paths.upload(name, "voice sample")


def test_every_engine_accepts_the_documented_name(home):
    from irisecho_core.engines import audiosource, chatterbox

    for name in ("3fa2b1c4.wav", "5d0e7a91c3b84f26.wav", "0" * 32 + ".wav"):
        (paths.sub("uploads") / name).write_bytes(b"x")
        assert chatterbox.upload_path(name).name == name
        assert audiosource.upload_path(name).name == name
    with pytest.raises(ValueError, match="lowercase hex digits"):
        chatterbox.upload_path("my-voice.wav")
    with pytest.raises(ValueError, match="lowercase hex digits"):
        audiosource.upload_path("../x.wav")


# --- models --controls ------------------------------------------------------------------------


def registry_rows():
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "summary": m.summary,
            "limits": m.settings.get("limits") or {},
            "controls": [c.public() for c in m.controls],
        }
        for m in registry.default().models.values()
    ]


def test_models_controls_gives_every_control_and_limit_of_every_model(backend, capsys):
    backend.rows = registry_rows()
    assert cli.main(["models", "--controls"]) == 0
    sections = capsys.readouterr().out.split("== ")[1:]
    assert len(sections) == len(backend.rows)
    for row, section in zip(backend.rows, sections, strict=True):
        assert section.startswith(f"{row['id']} ({row['kind']})")
        for c in row["controls"]:
            assert re.search(rf"^{c['name']} +\S", section, flags=re.MULTILINE), (row["id"], c)
            assert c["label"] in section
            if "help" in c:
                assert c["help"] in section
        for name, lim in row["limits"].items():
            assert re.search(rf"^{name} +limit {lim['min']:g}\.\.{lim['max']:g}", section, re.M)


def test_models_controls_json_and_one_model(backend, capsys):
    backend.rows = registry_rows()
    assert cli.main(["models", "--controls", "--json"]) == 0
    everything = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in everything] == [r["id"] for r in backend.rows]
    assert cli.main(["models", "--controls", "sa3-sfx", "--json"]) == 0
    one = json.loads(capsys.readouterr().out)
    assert set(one) == {"id", "kind", "limits", "controls"}
    assert one["limits"]["takes"] == {"min": 1, "max": 4, "default": 3}
    assert {c["name"] for c in one["controls"]} >= {"steps", "cfg"}
    assert cli.main(["models", "--controls", "sa3-sfx"]) == 0
    assert "takes" in capsys.readouterr().out
    assert cli.main(["models", "--controls", "nope"]) == 2
