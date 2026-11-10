# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starting and ending: the shell's lifeline pipe, the workers that hang on the
core's, and the one lock on the data folder."""

import asyncio
import contextlib
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

# What `serve --stdin-watch` does, then start a program the way engines are
# started: output captured, stdin left alone.
CORE = textwrap.dedent(
    """
    import subprocess, sys, time
    from irisecho_core.cli import watch_stdin

    watch_stdin()
    time.sleep(0.3)  # let the watcher block on the pipe
    child = subprocess.Popen([sys.executable, "-c", "print('started')"], stdout=subprocess.PIPE)
    try:
        out, _ = child.communicate(timeout=20)
        print(out.decode().strip(), flush=True)
    except subprocess.TimeoutExpired:
        child.kill()
        print("hung", flush=True)
    time.sleep(30)  # still here when the pipe closes: the watcher must end us
    """
)


def test_children_start_while_the_lifeline_is_watched():
    core = subprocess.Popen(
        [sys.executable, "-c", CORE], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    try:
        assert core.stdout.readline().decode().strip() == "started"
        core.stdin.close()  # the shell goes away
        assert core.wait(timeout=10) == 0
    finally:
        core.kill()
        core.wait()


# --- a worker whose core is gone -----------------------------------------------------------------

# Writes a beat for as long as it lives, so a test can tell when it no longer does.
WORKER = textwrap.dedent(
    """
    import os
    import subprocess
    import sys
    import threading
    import time

    from irisecho_worker import serve

    BEAT = os.environ["STUB_BEAT"]


    def beat():
        with open(BEAT, "a", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\\n")
            while True:
                f.write(".")
                f.flush()
                time.sleep(0.05)


    threading.Thread(target=beat, daemon=True).start()


    def echo(params, reporter):
        return params


    def busy(params, reporter):
        reporter.progress(0.1, "in the handler")
        time.sleep(60)
        return {}


    def spawn(params, reporter):
        done = subprocess.run(
            [sys.executable, "-c", "print('started')"], capture_output=True, timeout=20
        )
        return {"said": done.stdout.decode().strip()}


    serve({"echo": echo, "busy": busy, "spawn": spawn})
    """
)

# The core as the shell runs it, with one real WorkerProcess that is idle or inside a call.
CORE_WITH_WORKER = textwrap.dedent(
    """
    import asyncio, sys, time
    from pathlib import Path
    from irisecho_core.cli import watch_stdin
    from irisecho_core.engines.worker import WorkerProcess

    watch_stdin()


    async def main():
        worker = WorkerProcess("stub", Path(sys.executable), Path(sys.argv[1]))
        await worker.start()
        if sys.argv[2] == "busy":
            await worker.call("busy", {}, lambda p, m: print("there", flush=True))
        await worker.call("echo", {})
        print("there", flush=True)
        await asyncio.sleep(60)


    asyncio.run(main())
    """
)


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """A stand-in worker script, and the file it beats into."""
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    beat = tmp_path / "beat.txt"
    monkeypatch.setenv("STUB_BEAT", str(beat))
    script = tmp_path / "stub_worker.py"
    script.write_text(WORKER, encoding="utf-8")
    yield script, beat
    if beat.exists():  # a worker the test could not end must not outlive it
        with contextlib.suppress(OSError, ValueError, IndexError):
            os.kill(int(beat.read_text(encoding="utf-8").split()[0]), signal.SIGTERM)


def stops_beating(beat: Path, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        before = beat.stat().st_size
        time.sleep(0.4)
        if beat.stat().st_size == before:
            return True
    return False


@pytest.mark.parametrize("doing", ["busy", "idle"])
def test_a_worker_ends_when_its_core_is_gone(stub, doing):
    """Inside a handler too, where it reads no requests and would hold the GPU to the end."""
    script, beat = stub
    core = subprocess.Popen(
        [sys.executable, "-c", CORE_WITH_WORKER, str(script), doing],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        assert core.stdout.readline().decode().strip() == "there"
        assert not stops_beating(beat, 0.5)
        core.stdin.close()  # the shell goes away, and the core with it: no engine shutdown
        assert core.wait(timeout=10) == 0
        assert stops_beating(beat, 6)
    finally:
        core.kill()
        core.wait()


def test_a_program_started_from_a_handler_does_not_hang(stub):
    """The worker's thread is blocked reading the core's pipe; that pipe is not its stdin."""
    from irisecho_core.engines.worker import WorkerProcess

    script, _ = stub

    async def scene():
        worker = WorkerProcess("stub", Path(sys.executable), script)
        await worker.start()
        try:
            assert await worker.call("echo", {"a": 1}) == {"a": 1}
            assert await worker.call("spawn", {}) == {"said": "started"}
            assert await worker.call("echo", {"b": 2}) == {"b": 2}
        finally:
            await worker.stop()

    asyncio.run(asyncio.wait_for(scene(), 40))


# --- one IrisEcho on a data folder ---------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("IRISECHO_HOME", str(folder))
    from irisecho_core import hardware

    monkeypatch.setattr(hardware, "vram_usage", lambda max_age=2.0: [])
    return folder


def new_app():
    from irisecho_core.app import App

    app = App()
    app.store.needs_scan = lambda: False
    return app


def running_job(app) -> str:
    """A job in the middle of running, as its row looks."""
    job = {
        "id": "abc123abc123",
        "model": "kokoro",
        "kind": "voice",
        "params": {},
        "status": "running",
        "progress": None,
        "message": None,
        "error": None,
        "created": time.time(),
        "started": time.time(),
        "finished": None,
        "outputs": [],
    }
    app.db.insert(job)
    return job["id"]


def test_a_second_app_on_the_same_folder_is_refused_and_touches_no_job(home):
    from irisecho_core.instance import InstanceRunning

    async def scene():
        first = new_app()
        await first.start()
        job_id = running_job(first)
        second = new_app()
        with pytest.raises(InstanceRunning, match="already running on this data folder"):
            await second.start()
        assert first.db.get(job_id)["status"] == "running"  # still the first one's
        await first.stop()
        # The folder is free again; the job nobody is running any more is now marked.
        await second.start()
        assert second.db.get(job_id)["status"] == "interrupted"
        await second.stop()

    asyncio.run(asyncio.wait_for(scene(), 30))


def test_apps_on_folders_of_their_own_do_not_meet(tmp_path):
    from irisecho_core import instance

    one = instance.acquire(tmp_path)
    (tmp_path / "other").mkdir()
    two = instance.acquire(tmp_path / "other")
    assert instance.held(tmp_path) and instance.held(tmp_path / "other")
    one.release()
    one.release()  # twice is harmless
    assert not instance.held(tmp_path) and instance.held(tmp_path / "other")
    two.release()


HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from irisecho_core import instance

    lock = instance.acquire(Path(sys.argv[1]))
    print("held", flush=True)
    time.sleep(60)
    """
)


def test_the_lock_goes_with_the_process_that_held_it(tmp_path):
    from irisecho_core import instance

    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    try:
        assert holder.stdout.readline().decode().strip() == "held"
        assert instance.held(tmp_path)
        with pytest.raises(instance.InstanceRunning):
            instance.acquire(tmp_path, patience=0)
        holder.kill()  # no clean-up of any kind
        holder.wait()
        deadline = time.monotonic() + 5
        while instance.held(tmp_path) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not instance.held(tmp_path)
    finally:
        holder.kill()
        holder.wait()


RUNNING_APP = textwrap.dedent(
    """
    import asyncio, sys, time
    from irisecho_core.app import App

    async def main():
        app = App()
        app.store.needs_scan = lambda: False
        await app.start()
        app.db.insert({
            "id": "abc123abc123", "model": "kokoro", "kind": "voice", "params": {},
            "status": "running", "progress": None, "message": None, "error": None,
            "created": time.time(), "started": time.time(), "finished": None, "outputs": [],
        })
        print("running", flush=True)
        await asyncio.sleep(60)

    asyncio.run(main())
    """
)


def test_a_second_process_on_the_same_home_leaves_the_first_ones_jobs_alone(
    cli, home, capsys, monkeypatch
):
    """Two real processes on one data folder: the first is an App with a job running.
    The second, as an App or as a command, is refused and changes nothing."""
    from irisecho_core.instance import InstanceRunning

    monkeypatch.setattr(cli, "ANSWER_WAIT", 0.6)
    first = subprocess.Popen(
        [sys.executable, "-c", RUNNING_APP],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        env={**os.environ, "IRISECHO_HOME": str(home)},
    )
    try:
        assert first.stdout.readline().decode().strip() == "running"

        async def second_app():
            app = new_app()
            with pytest.raises(InstanceRunning, match="already running on this data folder"):
                await app.start()

        asyncio.run(asyncio.wait_for(second_app(), 30))
        # A command neither starts engines of its own nor gets through: nothing answers here.
        assert cli.main(["models"]) == 1
        assert "already running on this data folder" in capsys.readouterr().err
        assert not cli.started_own
        assert new_app().db.get("abc123abc123")["status"] == "running"
    finally:
        first.kill()
        first.wait()


# --- the command line finds the running app ------------------------------------------------------


class FakeApp:
    """Answers like a running IrisEcho: /api/system, and an empty /api/models."""

    def __init__(self, folder: Path, deaf_for: int = 0, token: str = "t0ken"):
        fake = self
        self.asked = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                fake.asked += 1
                if fake.asked <= deaf_for:
                    status, body = 503, {}
                elif f"irisecho_session={token}" not in self.headers.get("Cookie", ""):
                    status, body = 401, {}
                else:
                    status, body = 200, [] if self.path == "/api/models" else {}
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        folder.mkdir(parents=True, exist_ok=True)
        self.info = folder / "server.json"
        self.info.write_text(json.dumps({"port": self.port, "token": "t0ken"}), encoding="utf-8")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def cli(home, monkeypatch):
    """The command line, unable to start engines of its own without the test seeing it."""
    from irisecho_core import cli

    monkeypatch.setattr(cli, "started_own", [], raising=False)

    async def with_app(fn):
        cli.started_own.append(fn)
        return 7

    monkeypatch.setattr(cli, "with_app", with_app)
    monkeypatch.setattr(cli, "ANSWER_WAIT", 3.0)
    return cli


def test_a_command_goes_to_the_running_app(cli, home, capsys):
    fake = FakeApp(home)
    try:
        assert cli.main(["models", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert not cli.started_own
    finally:
        fake.close()


def test_a_command_waits_for_a_running_app_that_is_slow_to_answer(cli, home, capsys):
    from irisecho_core import instance

    lock = instance.acquire(home)  # the running app's
    fake = FakeApp(home, deaf_for=2)
    try:
        assert cli.main(["models", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert fake.asked > 2 and not cli.started_own
    finally:
        fake.close()
        lock.release()


def test_a_command_works_on_its_own_when_nothing_is_running(cli, home):
    assert cli.main(["models"]) == 7
    assert len(cli.started_own) == 1


@pytest.mark.parametrize("answers", [False, True])
def test_a_command_refuses_when_the_folder_is_held_and_nothing_answers(
    cli, home, capsys, monkeypatch, answers
):
    from irisecho_core import instance

    monkeypatch.setattr(cli, "ANSWER_WAIT", 0.6)
    lock = instance.acquire(home)
    fake = FakeApp(home, token="another") if answers else None
    try:
        assert cli.main(["models"]) == 1
        said = capsys.readouterr().err
        assert "already running on this data folder" in said and "does not answer" in said
        assert ("not to the token" in said) == answers
        assert not cli.started_own
        assert cli.main(["api", "GET", "models"]) == 1
        assert "does not answer" in capsys.readouterr().err
    finally:
        if fake:
            fake.close()
        lock.release()


def test_a_second_server_is_refused_and_leaves_server_json_alone(cli, home, capsys):
    from irisecho_core import instance

    lock = instance.acquire(home)
    fake = FakeApp(home)
    before = fake.info.read_text(encoding="utf-8")
    try:
        assert cli.main(["serve"]) == 1
        assert f"already running on http://127.0.0.1:{fake.port}" in capsys.readouterr().err
        assert fake.info.read_text(encoding="utf-8") == before
    finally:
        fake.close()
        lock.release()


def test_a_port_that_was_asked_for_and_is_taken_is_an_error(cli, home, capsys):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert cli.main(["serve", "--port", str(port)]) == 1
        assert f"Port {port} is already in use" in capsys.readouterr().err
        assert not (home / "server.json").exists()
        # Not asked for, only preferred: another one will do.
        assert cli.free_port(port) != port
        with pytest.raises(cli.CliError):
            cli.free_port(port, strict=True)
