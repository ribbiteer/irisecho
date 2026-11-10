# SPDX-License-Identifier: AGPL-3.0-or-later
"""`irisecho` command line.

irisecho                     open the app
irisecho serve               run the local server (used by the desktop shell)
irisecho info                this machine and what it can run
irisecho models              every model and whether it is ready
irisecho setup MODEL         install and download everything a model needs
irisecho say TEXT            speak a line (or --file cues.txt for a script)
irisecho clone TEXT --ref F  speak in a voice cloned from a short recording
irisecho music TAGS          compose music (or --file cues.txt)
irisecho sfx PROMPT          make sound effects (or --file cues.txt)
irisecho link FOLDER         use model files you already have
irisecho 3d PICTURE          make a 3D model from a picture
irisecho image PROMPT        make images
irisecho write DRAFT         improve a prompt, or describe a picture as one
irisecho api METHOD PATH     call the running app's local API (api GET models)
irisecho agents              the guide for scripts and coding agents

When the app is already running, commands hand their work to it instead of
starting engines of their own.

Exit codes (AGENTS.md says the same): 0 done; 1 it could not be done (a job
failed, a model is not ready, the app does not answer); 2 the command line or a
value in it is wrong and the caller can fix it by changing the call (an
unknown model, a number out of range, a missing file). Raise `UsageError` for
2 and `CliError` for 1; `main` is the only place that turns them into a code.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path

from irisecho_core import __version__, credentials, instance, paths, platform_support
from irisecho_core.instance import InstanceRunning

DEFAULT_PORT = 7788
# How long a command waits for an IrisEcho that holds the data folder to answer.
# One that is just starting, or busy for a moment, does within this.
ANSWER_WAIT = 15.0


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- server & window --------------------------------------------------------


def free_port(preferred: int, strict: bool = False) -> int:
    """`preferred` if it is free, else any free port; with `strict`, an error instead.

    Whoever named a port will look for the server there and nowhere else."""
    for port in (preferred,) if strict else (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    if strict:
        raise CliError(
            f"Port {preferred} is already in use. "
            "Close what is using it, or start IrisEcho on another port."
        )
    raise RuntimeError("no free port")


def open_window(url: str) -> None:
    """Open the UI in a chromeless app window when a Chromium browser is available."""
    profile = paths.sub("window")
    candidates = []
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates = [
            shutil.which(n) or "" for n in ("chromium", "google-chrome", "microsoft-edge")
        ]
    for exe in candidates:
        if exe and Path(exe).exists():
            subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1440,920"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    webbrowser.open(url)


def watch_stdin() -> None:
    """Exit when the other end of stdin closes.

    The desktop shell holds that end; when it goes away, so do we (engines
    follow: workers on their own stdin, ComfyUI via its watchdog).

    The pipe is moved off stdin first, and stdin pointed at the null device.
    Left in place, every process started from here would inherit the pipe, and
    on Windows a program hangs while starting if its stdin is a pipe that
    another process is blocked reading.
    """
    import threading

    lifeline = os.fdopen(os.dup(0), "rb", buffering=0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)

    def watch() -> None:
        try:
            while lifeline.read(1024):
                pass
        finally:
            os._exit(0)

    threading.Thread(target=watch, daemon=True, name="stdin-watch").start()


def cmd_serve(args) -> int:
    import uvicorn

    from irisecho_core import guide
    from irisecho_core.server import create_app, write_server_info

    if args.stdin_watch:
        watch_stdin()

    named = args.port if args.port is not None else os.environ.get("IRISECHO_PORT")
    port = free_port(DEFAULT_PORT if named is None else int(named), strict=named is not None)
    api = create_app()
    try:
        # Before server.json is written: it belongs to the IrisEcho that is running.
        api.state.core.claim()
    except InstanceRunning as e:
        remote = running_app()  # says so itself when that one does not answer
        if remote is None:
            raise CliError(str(e)) from None
        url = str(remote.http.base_url)
        if args.open:
            print(f"IrisEcho is already running on {url}", flush=True)
            open_window(url)
            return 0
        raise CliError(
            f"IrisEcho is already running on {url} with this data folder "
            f"({paths.data_dir()}). Use that one, or close it first."
        ) from None
    write_server_info(port, api.state.token)
    with contextlib.suppress(OSError):
        guide.write()  # this installation's own copy, for agents pointed at the data folder
    url = f"http://127.0.0.1:{port}/"
    print(f"IrisEcho {__version__} on {url}", flush=True)
    if args.open:
        # Open once the server is accepting connections.
        def opener():
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            open_window(url)

        import threading

        threading.Thread(target=opener, daemon=True).start()
    uvicorn.run(api, host="127.0.0.1", port=port, log_level="warning", ws_ping_interval=None)
    return 0


# --- in-process helpers -----------------------------------------------------


async def with_app(fn):
    from irisecho_core.app import App

    app = App()
    await app.start()
    try:
        return await fn(app)
    finally:
        await app.stop()


async def wait_ready(app, model_id: str, accept_license: bool = False) -> None:
    """Set a model up and wait until it can run; CliError (with `needs`) if it cannot."""
    from irisecho_core.app import NotReady, UnknownModel

    try:
        model = app.model(model_id)
    except UnknownModel as e:
        raise UsageError(str(e)) from None
    status = app.model_status(model)
    if "unsupported" in status["needs"]:
        raise CliError(f"{model.name} cannot run on this machine.", ["unsupported"])
    if "license" in status["needs"]:
        lic = status["license"]
        if not accept_license:
            raise CliError(license_notice(model.name, lic), ["license"])
        app.accept_license(lic["id"])
    if "download" in status["needs"]:
        token_notice(credentials.source())
    try:
        app.prepare(model_id)
    except NotReady as e:
        raise CliError(str(e), e.needs) from None
    last = ""
    while True:
        status = app.model_status(model)
        if status["ready"]:
            progress_end()
            return
        if model.engine in app.install_tasks:
            log = app.install_logs.get(model.engine)
            line = f"Installing {model.engine}: {(log[-1] if log else '')[:70]}"
        else:
            engine = app.engines[model.engine]
            if engine.state == "error":
                raise CliError(f"Installing {engine.name} failed:\n{engine.detail}", ["engine"])
            blocked = [
                d
                for d in status["downloads"]
                if d["state"] in ("error", "needs_token", "needs_access")
            ]
            if blocked:
                raise CliError(f"{blocked[0]['id']}: {blocked[0]['error']}", ["download"])
            if (
                status["downloads"]
                and not any(
                    d["state"] in ("queued", "downloading", "verifying")
                    for d in status["downloads"]
                )
                and "download" in status["needs"]
            ):
                app.prepare(model_id)
            line = f"Downloading {human(status['have'])} of {human(status['size'])}"
        if line != last:
            progress(line)
            last = line
        await asyncio.sleep(0.5)


# --- where the work runs ------------------------------------------------------
#
# A command's work goes to the IrisEcho that is already running when there is
# one (the window, or `irisecho serve`), so there is one queue and one tenant on
# the GPU. Otherwise this process runs the engines itself. Commands are written
# against the few calls both offer.

ENDED = ("done", "failed", "cancelled", "interrupted")
ACTIVE = ("queued", "downloading", "verifying")
BLOCKED = ("error", "needs_token", "needs_access")


class CliError(Exception):
    """Something to tell the person and stop; not a bug. Exit code 1.

    `needs` says what is missing when a model is not ready (the same words as the API:
    `license`, `unsupported`, `engine`, `download`). `job_id` is the job that failed,
    if it was queued. `reported` is set when `--json` already printed this failure."""

    code = 1

    def __init__(self, message: str, needs=(), job_id: str | None = None):
        super().__init__(message)
        self.needs = list(needs)
        self.job_id = job_id
        self.reported = False


class UsageError(CliError):
    """The command line, or a value in it, is wrong. Exit code 2."""

    code = 2


# --- what a long command shows while it waits -----------------------------------
#
# All of it goes to stderr, so stdout carries only results (or JSON).

_live = False  # a rewritten line is showing
_last_key: str | None = None


def progress(line: str, key: str | None = None) -> None:
    """Show where a wait has got to: one line rewritten in place on a terminal, and
    a new line each time `key` (default: the line) changes anywhere else."""
    global _live, _last_key
    if sys.stderr.isatty():
        print("\r" + line[:100].ljust(100), end="", file=sys.stderr, flush=True)
        _live = True
    elif (key or line) != _last_key:
        print(line, file=sys.stderr, flush=True)
    _last_key = key or line


def progress_end() -> None:
    """Clear the progress line before anything else is printed."""
    global _live, _last_key
    if _live:
        print("\r" + " " * 100 + "\r", end="", file=sys.stderr, flush=True)
        _live = False
    _last_key = None


LICENSE_ASK = (
    "This model's license must be accepted by the person. Ask them; with their OK, "
    "re-run with `--accept-license` (or accept it in the app)."
)


def license_notice(name: str, lic: dict) -> str:
    """What every license stop says. The person accepts; a script or agent does not."""
    return f"{name} uses the {lic['name']}:\n  {lic['url']}\n{lic['summary']}\n{LICENSE_ASK}"


def token_notice(source: str | None) -> None:
    """Say so when a Hugging Face token inherited from the environment is about to be sent."""
    if source == "environment":
        progress_end()
        print(
            "Using the Hugging Face token from the HF_TOKEN environment variable.", file=sys.stderr
        )


def show_warnings(job: dict) -> list[str]:
    """Print what the app says about params the model did not use; returns them."""
    warnings = job.get("warnings") or []
    for text in warnings:
        progress_end()
        print(f"warning: {text}", file=sys.stderr)
    return warnings


# --- where the work runs ------------------------------------------------------
#
# A command's work goes to the IrisEcho that is already running when there is
# one (the window, or `irisecho serve`), so there is one queue and one tenant on
# the GPU. Otherwise this process runs the engines itself. Commands are written
# against the few calls both offer. Both turn a refusal into a CliError of the
# same kind: UsageError for a value that is wrong, CliError for the rest.


class Local:
    """This process runs the engines itself."""

    def __init__(self, app):
        self.app = app

    async def models(self) -> list[dict]:
        return self.app.models()

    async def wait_ready(self, model_id: str, accept_license: bool = False) -> None:
        await wait_ready(self.app, model_id, accept_license)

    async def submit(self, model_id: str, params: dict) -> dict:
        from irisecho_core.app import NotReady, UnknownModel
        from irisecho_core.controls import ControlError

        try:
            return self.app.submit(model_id, params)
        except (ControlError, UnknownModel) as e:
            raise UsageError(str(e)) from None
        except NotReady as e:
            raise CliError(str(e), e.needs) from None

    async def job(self, job_id: str) -> dict:
        job = self.app.db.get(job_id)
        if job is None:
            raise CliError("That job is no longer in the history.", job_id=job_id)
        return job

    async def linked_dirs(self) -> list[str]:
        return list(self.app.settings.linked_model_dirs)

    async def link(self, dirs: list[str]) -> None:
        self.app.set_linked_dirs(dirs)
        await self.app.scan_task


# A GET that fails to get through is tried again this many times, this far apart,
# before the connection counts as lost. (A POST is never repeated: it may have worked.)
GET_TRIES = 5
RETRY_WAIT = 1.0


class Remote:
    """An IrisEcho that is already running, reached over its local API."""

    def __init__(self, port: int, token: str):
        import httpx

        from irisecho_core.server import COOKIE

        self.http = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            cookies={COOKIE: token},
            headers={"X-IrisEcho": "1"},
            timeout=60,
            trust_env=False,  # loopback: never through a proxy from the environment
        )

    @classmethod
    def look(cls) -> tuple[Remote | None, str]:
        """The app running on this data folder, or why there is none to talk to:
        "silent" (nothing answered) or "refused" (something did, but not to the
        token written down)."""
        import httpx

        try:
            info = json.loads((paths.data_dir() / "server.json").read_text(encoding="utf-8"))
            remote = cls(int(info["port"]), str(info["token"]))
            status = remote.http.get("/api/system", timeout=2).status_code
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            return None, "silent"
        return (remote, "") if status == 200 else (None, "refused")

    @classmethod
    def find(cls) -> Remote | None:
        """The app running on this data folder, if it answers to the token it wrote down."""
        return cls.look()[0]

    def send(self, method: str, path: str, body=None):
        """The response. A connection that is lost ends in a CliError, not a traceback."""
        import httpx

        tries = GET_TRIES if method == "GET" else 1
        for attempt in range(tries):
            try:
                return self.http.request(method, path, json=body)
            except httpx.HTTPError:
                if attempt + 1 < tries:
                    time.sleep(RETRY_WAIT)
        raise CliError(
            "Lost the connection to IrisEcho. It may have been closed: open it again, "
            "then run the command again."
        )

    def call(self, method: str, path: str, body=None):
        r = self.send(method, path, body)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            why = data.get("error") or data.get("detail") if isinstance(data, dict) else ""
            needs = data.get("needs") if isinstance(data, dict) else None
            kind = UsageError if r.status_code in (400, 422) else CliError
            raise kind(
                str(why or f"IrisEcho answered {r.status_code} to {method} {path}."), needs or ()
            )
        return data

    async def models(self) -> list[dict]:
        return self.call("GET", "/api/models")

    async def token_source(self) -> str | None:
        return self.call("GET", "/api/bootstrap")["credentials"].get("huggingface_source")

    async def wait_ready(self, model_id: str, accept_license: bool = False) -> None:
        last = ""
        noted = False
        while True:
            model = next((m for m in await self.models() if m["id"] == model_id), None)
            if model is None:
                raise UsageError(f"unknown model {model_id}")
            needs = model["needs"]
            if model["ready"]:
                progress_end()
                return
            if "unsupported" in needs:
                raise CliError(f"{model['name']} cannot run on this machine.", ["unsupported"])
            if "license" in needs:
                if not accept_license:
                    raise CliError(license_notice(model["name"], model["license"]), ["license"])
                self.call("POST", f"/api/licenses/{model['license']['id']}/accept")
                continue
            if "download" in needs and not noted:
                noted = True
                token_notice(await self.token_source())
            engine = next(e for e in self.call("GET", "/api/engines") if e["id"] == model["engine"])
            downloads = model["downloads"]
            blocked = [d for d in downloads if d["state"] in BLOCKED]
            if blocked:
                raise CliError(f"{blocked[0]['id']}: {blocked[0]['error']}", ["download"])
            installing = model["installing"] or engine["state"] == "installing"
            if engine["state"] == "error" and not installing:
                raise CliError(
                    f"Installing {engine['name']} failed:\n{engine['detail']}", ["engine"]
                )
            if installing:
                line = f"Installing {engine['name']}"
            elif any(d["state"] in ACTIVE for d in downloads):
                done = sum(f["have"] for f in model["files"] if f["state"] in ("ready", "linked"))
                done += sum(d["done"] for d in downloads if d["state"] in ACTIVE)
                line = f"Downloading {human(done)} of {human(model['size'])}"
            else:
                self.call("POST", f"/api/models/{model_id}/prepare")
                line = f"Setting up {model['name']}"
            if line != last:
                progress(line)
                last = line
            await asyncio.sleep(1)

    async def submit(self, model_id: str, params: dict) -> dict:
        return self.call("POST", "/api/jobs", {"model": model_id, "params": params})

    async def job(self, job_id: str) -> dict:
        return self.call("GET", f"/api/jobs/{job_id}")

    async def linked_dirs(self) -> list[str]:
        return self.call("GET", "/api/settings")["linked_model_dirs"]

    async def link(self, dirs: list[str]) -> None:
        self.call("POST", "/api/settings/linked-dirs", {"dirs": dirs})
        while self.call("GET", "/api/bootstrap")["scan"]["state"] == "scanning":
            await asyncio.sleep(1)


def running_app() -> Remote | None:
    """The IrisEcho running on this data folder; None when there is none.

    The lock on the data folder tells "not running" from "running, but slow or
    not answering": a held lock means there is one, and it is waited for. A
    command never starts engines of its own beside it."""
    remote, why = Remote.look()
    if remote is not None or not instance.held(paths.data_dir()):
        return remote
    deadline = time.monotonic() + ANSWER_WAIT
    while remote is None and time.monotonic() < deadline:
        time.sleep(0.5)
        remote, why = Remote.look()
    if remote is None:
        detail = (
            "It answered, but not to the token in server.json."
            if why == "refused"
            else "Another irisecho command may be working there, or the app is "
            "still starting or has stopped responding."
        )
        raise CliError(
            f"IrisEcho is already running on this data folder ({paths.data_dir()}) "
            f"but does not answer. {detail}\n"
            "Wait for it to finish, or close it, then try again."
        )
    return remote


def with_backend(fn) -> int:
    """Run `fn(backend)` against the running app if there is one, else in this process."""
    remote = running_app()
    if remote is not None:
        return asyncio.run(fn(remote))

    async def go(app):
        return await fn(Local(app))

    try:
        return asyncio.run(with_app(go))
    except InstanceRunning as e:
        # One started between the look above and here.
        raise CliError(str(e)) from None


def result(*, job_id, label, status, model, outputs, seed, error, needs=(), warnings=()) -> dict:
    """The one object `--json` prints for a job, whether or not it ever ran."""
    return {
        "id": job_id,
        "label": label,
        "status": status,
        "model": model,
        "outputs": list(outputs),
        "seed": seed,
        "error": error,
        "needs": list(needs),
        "warnings": list(warnings),
    }


def failed_result(model_id: str, params: dict, e: CliError) -> dict:
    """A job that stopped before it ran (or before it was queued)."""
    return result(
        job_id=e.job_id,
        label=params.get("id") or "",
        status="failed",
        model=model_id,
        outputs=[],
        seed=None,
        error=str(e),
        needs=e.needs,
    )


async def poll(backend, job: dict, label: str = "") -> dict:
    """Wait for a job to end and return it, showing its progress on stderr.

    There is no time limit: a job can legitimately run for a very long time. What ends
    the wait early is a lost connection, after the few tries `Remote.send` makes."""
    while job["status"] not in ENDED:
        job = await backend.job(job["id"])
        if job["status"] in ENDED:
            break
        msg = job.get("message") or job["status"]
        pct = f" {job['progress'] * 100:.0f}%" if job.get("progress") is not None else ""
        progress(f"{label}: {msg}{pct}" if label else f"{msg}{pct}", key=msg)
        await asyncio.sleep(0.25)
    progress_end()
    return job


async def follow(backend, job: dict, label: str) -> dict:
    """Wait for a job that must succeed; CliError if it does not."""
    job = await poll(backend, job, label)
    if job["status"] != "done":
        raise CliError(f"{label}: {job.get('error') or job['status']}", job_id=job["id"])
    return job


async def run_jobs(
    backend, model_id: str, param_list: list[dict], out: Path | None, as_json: bool = False
) -> int:
    """Queue each job and follow it to the end. With `as_json`, stdout carries one object a job."""
    failures = 0
    notes = sys.stderr if as_json else sys.stdout
    for n, params in enumerate(param_list):
        label = params.get("id") or (params.get("prompt") or params.get("text") or "")[:50]
        try:
            job = await backend.submit(model_id, params)
        except CliError as e:
            if as_json:  # this job and those after it will not run
                for rest in param_list[n:]:
                    print(json.dumps(failed_result(model_id, rest, e)), flush=True)
                e.reported = True
            raise
        warnings = show_warnings(job)
        job = await poll(backend, job, label)
        files = []
        if job["status"] != "done":
            failures += 1
            print(f"{label}: failed: {job.get('error') or job['status']}", file=sys.stderr)
        for i, o in enumerate(job["outputs"]):
            src = Path(o["path"])
            if out:
                out.mkdir(parents=True, exist_ok=True)
                name = params.get("id")
                dest = out / (
                    f"{name}{'-' + str(i + 1) if i else ''}{src.suffix}" if name else src.name
                )
                shutil.copy2(src, dest)
                src = dest
            files.append(str(src))
            print(f"{label}: {src}", file=notes)
        if as_json:
            seed = next((o.get("seed") for o in job["outputs"] if "seed" in o), None)
            done = result(
                job_id=job["id"],
                label=params.get("id") or "",
                status=job["status"],
                model=model_id,
                outputs=files,
                seed=seed,
                error=job.get("error"),
                warnings=warnings,
            )
            print(json.dumps(done), flush=True)
    return 1 if failures else 0


def describe_control(c: dict) -> str:
    """One line: type, range or choices, and the recommended value."""
    if c["type"] in ("int", "float"):
        kind = f"{c['type']} {c.get('min')}..{c.get('max')}"
    elif c["type"] == "choice":
        kind = "one of " + ", ".join(str(o["value"]) or '"" (any)' for o in c.get("options", []))
    elif c["type"] == "loras":
        shape = (
            '{"high": FILE, "low": FILE, "strength": 1}'
            if c.get("pair")
            else '{"file": FILE, "strength": 1}'
        )
        kind = f"list of up to 3 {shape}"
    else:
        kind = c["type"]
    default = json.dumps(c.get("default"))
    text = f"{kind}; recommended {default}"
    if c.get("needs"):
        text += f" (only with {c['needs']})"
    return text + (" (expert)" if c.get("tier") == "expert" else "")


def describe_limit(lim: dict) -> str:
    return f"{lim['min']:g}..{lim['max']:g}; default {lim['default']:g}"


def parse_sets(items: list[str]) -> dict:
    """`--set name=value` pairs: a value that reads as JSON (12, 0.5, true, [...]) is taken as
    that, anything else as text. The app checks each against the model's controls."""
    out = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise UsageError(f"--set takes NAME=VALUE, got: {item}")
        try:
            out[name.strip()] = json.loads(value)
        except ValueError:
            out[name.strip()] = value
    return out


def add_set(p) -> None:
    p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="an advanced control, for example --set steps=12 (see `irisecho models --controls`)",
    )


def add_license(p) -> None:
    p.add_argument(
        "--accept-license",
        action="store_true",
        help="accept the model's license; only when the person told you to",
    )


def registry_model(model_id: str):
    from irisecho_core import registry

    model = registry.default().models.get(model_id)
    if model is None:
        raise UsageError(f"unknown model {model_id}")
    return model


def precheck(model_id: str, param_list: list[dict]) -> None:
    """Refuse a wrong model id or value now, before any download or engine start.

    The registry is this installation's own, so it says what the app (running or
    started here) would say; the app still checks again when the job is queued."""
    from irisecho_core import controls

    model = registry_model(model_id)
    for params in param_list:
        try:
            controls.check(model, params)
        except controls.ControlError as e:
            raise UsageError(str(e)) from None


def generate(args, model_id: str, param_list: list[dict]) -> int:
    """What every generating command does once it knows its jobs."""
    try:
        sets = parse_sets(getattr(args, "set", None))
        param_list = [{**p, **sets} for p in param_list]
        precheck(model_id, param_list)

        async def go(backend):
            await backend.wait_ready(model_id, args.accept_license)
            out = Path(args.out) if args.out else None
            return await run_jobs(backend, model_id, param_list, out, args.json)

        return with_backend(go)
    except CliError as e:
        if args.json and not e.reported:  # a failed object for every job that did not run
            for params in param_list:
                print(json.dumps(failed_result(model_id, params, e)), flush=True)
            e.reported = True
        raise


def read_text(path: Path) -> str:
    """A text file the person named. A BOM (PowerShell 5.1 writes one) is not part of it."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        raise UsageError(f"Could not read {path}.") from None
    except UnicodeDecodeError:
        raise UsageError(f"{path} is not UTF-8 text.") from None


def read_cues(path: Path) -> list[tuple[str, str]]:
    """Cue sheet: one line per file, `id|text` or just `text`; # comments."""
    cues = []
    for n, raw in enumerate(read_text(path).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cue_id, sep, text = line.partition("|")
        if not sep:
            cue_id, text = f"{n:03d}", line
        cues.append((cue_id.strip(), text.strip()))
    return cues


# --- commands ---------------------------------------------------------------


def cmd_info(args) -> int:
    from irisecho_core import hardware, settings

    hw = hardware.detect(settings.load().gpu)
    if args.json:
        print(json.dumps(hw.public(), indent=2))
        return 0
    info = platform_support.detect()
    for key, value in asdict(info).items():
        print(f"{key}: {value}")
    print(f"backend: {hw.backend}" + (f" ({hw.quant}, {hw.cuda_tag})" if hw.quant else ""))
    for g in hw.gpus:
        used = " (in use)" if len(hw.gpus) > 1 and g.uuid == hw.gpu_uuid else ""
        print(f"gpu {g.index}: {g.name}, {g.vram_mb // 1024} GB, driver {g.driver}{used}")
    print(f"ram: {hw.ram_mb // 1024} GB")
    print(f"data: {paths.data_dir()}")
    return 0


def control_sheet(m: dict) -> dict:
    """What `models --controls --json` gives for one model."""
    return {
        "id": m["id"],
        "kind": m["kind"],
        "limits": m.get("limits") or {},
        "controls": m.get("controls") or [],
    }


def print_controls(m: dict, heading: bool) -> None:
    if heading:
        print(f"== {m['id']} ({m['kind']}): {m.get('summary', '')}")
    limits = m.get("limits") or {}
    for name, lim in limits.items():
        print(f"{name:<16} limit {describe_limit(lim)}")
    if not m.get("controls") and not limits:
        print(f"{m['id']} has no advanced controls.")
    for c in m.get("controls") or []:
        print(f"{c['name']:<16} {describe_control(c)}")
        print(f"{'':<16} {c['label']}: {c.get('help', '')}")


def cmd_models(args) -> int:
    async def go(backend):
        rows = await backend.models()
        if args.controls:
            everyone = args.controls == "all"
            wanted = rows if everyone else [r for r in rows if r["id"] == args.controls]
            if not wanted:
                raise UsageError(f"No model {args.controls}.")
            if args.json:
                sheets = [control_sheet(m) for m in wanted]
                print(json.dumps(sheets if everyone else sheets[0], indent=2))
                return 0
            for m in wanted:
                print_controls(m, everyone)
            return 0
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        for m in rows:
            if m["ready"]:
                state = "ready"
            elif not m["available"]:
                state = "not for this machine"
            else:
                state = "needs " + ", ".join(m["needs"])
            lic = "" if m["license"]["commercial"] else "  [non-commercial]"
            print(f"{m['id']:<16} {m['kind']:<7} {human(m['size']):>9}  {state}{lic}")
        return 0

    return with_backend(go)


def cmd_setup(args) -> int:
    async def go(backend):
        await backend.wait_ready(args.model, args.accept_license)
        print(f"{args.model} is ready.")
        return 0

    return with_backend(go)


def cmd_link(args) -> int:
    async def go(backend):
        dirs = await backend.linked_dirs()
        for d in args.folders:
            full = str(Path(d).resolve())
            if args.remove:  # a folder that is gone can still be taken off the list
                dirs = [x for x in dirs if x != full]
                continue
            if not Path(full).is_dir():
                raise UsageError(f"Not a folder: {d}")
            if full not in dirs:
                dirs.append(full)
        print("Checking files (identical files are used in place, nothing is copied)...")
        await backend.link(dirs)
        models = await backend.models()
        shown = set()
        for m in models:
            for f in m["files"]:
                if f["state"] == "linked" and f["id"] not in shown:
                    shown.add(f["id"])
                    print(f"  {f['id']:<48} {f['path']}")
        print()
        for m in models:
            if not any(f["state"] == "linked" for f in m["files"]):
                continue
            if m["ready"]:
                state = "ready"
            elif "download" in m["needs"]:
                state = f"{human(m['size'] - m['have'])} still to download"
            else:
                state = f"files complete; finish with: irisecho setup {m['id']}"
            print(f"  {m['name']:<24} {state}")
        return 0

    return with_backend(go)


def api_path(text: str) -> str:
    """`models`, `api/models` and `/api/models` all mean /api/models.

    Git Bash on Windows rewrites an argument that starts with a slash into a
    Windows path (`C:/Program Files/Git/api/models`); that is understood too.
    """
    text = text.replace("\\", "/")
    if "/api/" in text:
        text = text.split("/api/", 1)[1]
    text = text.lstrip("/")
    return "/api/" + text.removeprefix("api/")


def read_stdin() -> str:
    """All of stdin as UTF-8 text, whatever the console's code page is."""
    try:
        raw = sys.stdin.buffer.read()
    except AttributeError:  # not a real stream (tests)
        return sys.stdin.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UsageError("What was given on stdin is not UTF-8 text.") from None


def cmd_api(args) -> int:
    remote = running_app()
    if remote is None:
        print(
            "IrisEcho is not running. Open the app, or start it with: irisecho serve",
            file=sys.stderr,
        )
        return 1
    body = None
    if args.body is not None:
        raw = read_stdin() if args.body == "-" else args.body
        try:
            body = json.loads(raw)
        except ValueError as e:
            raise UsageError(f"The request body is not JSON: {e}") from None
    r = remote.send(args.method.upper(), api_path(args.path), body)
    if "application/json" not in r.headers.get("content-type", ""):
        sys.stdout.buffer.write(r.content)
        return 0 if r.status_code < 400 else 1
    data = r.json()
    ok = r.status_code < 400
    if args.wait and ok and isinstance(data, dict) and {"id", "status"} <= set(data):
        data = asyncio.run(poll(remote, data))
        ok = data["status"] == "done"
    print(json.dumps(data, indent=2))
    return 0 if ok else 1


def cmd_agents(args) -> int:
    from irisecho_core import guide

    print(guide.text())
    return 0


def cmd_say(args) -> int:
    if args.file:
        cues = read_cues(Path(args.file))
    elif args.text:
        cues = [("", args.text)]
    else:
        raise UsageError("Give a line to say, or --file with a cue sheet.")
    params = [
        {"id": cid, "text": text, "voice": args.voice, "speed": args.speed, "clean": not args.raw}
        for cid, text in cues
    ]
    return generate(args, "kokoro", params)


def read_music_cues(path: Path) -> list[dict]:
    """Cue sheet: `id|seconds|bpm|tags` per line (bpm may be empty); # comments."""
    cues = []
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        try:
            if len(parts) != 4:
                raise ValueError
            cue_id, seconds, bpm, tags = parts
            cue = {
                "id": cue_id,
                "duration": float(seconds),
                "bpm": int(bpm) if bpm else None,
                "prompt": tags,
            }
        except ValueError:
            raise UsageError(f"Expected id|seconds|bpm|tags, got: {line}") from None
        cues.append(cue)
    return cues


def stage_upload(path: Path) -> str:
    """Put a picture or recording where jobs read their inputs; returns its upload name."""
    import uuid

    if not path.is_file():
        raise UsageError(f"No such file: {path}")
    name = f"{uuid.uuid4().hex}{path.suffix.lower()}"
    try:
        dest = paths.upload(name)
    except ValueError:
        raise UsageError(
            f"{path.name}: the file name must end in an extension of 2 to 5 letters or digits."
        ) from None
    shutil.copyfile(path, dest)
    return name


def cmd_clone(args) -> int:
    if args.file:
        cues = read_cues(Path(args.file))
    elif args.text:
        cues = [("", args.text)]
    else:
        raise UsageError("Give a line to say, or --file with a cue sheet.")
    upload = stage_upload(Path(args.ref))
    params = [
        {
            "id": cid,
            "text": text,
            "ref": upload,
            "consent": True,
            "takes": args.takes,
            "seed": args.seed,
            "exaggeration": args.exaggeration,
            "cfg": args.cfg,
            "clean": not args.raw,
        }
        for cid, text in cues
    ]
    print(
        "Only clone voices you have permission to use. Output carries an inaudible watermark.",
        file=sys.stderr,
    )
    return generate(args, args.model, params)


def cmd_music(args) -> int:
    if args.file:
        cues = read_music_cues(Path(args.file))
    elif args.prompt:
        cues = [{"prompt": args.prompt, "duration": args.seconds, "bpm": args.bpm}]
    else:
        raise UsageError("Give style tags, or --file with a cue sheet.")
    lyrics = args.lyrics or ""
    if args.lyrics_file:
        lyrics = read_text(Path(args.lyrics_file))
    params = [
        {
            **cue,
            "takes": args.takes,
            "loop": args.loop,
            "thinking": args.thinking,
            "lyrics": lyrics,
            "seed": args.seed,
        }
        for cue in cues
    ]
    return generate(args, args.model, params)


def cmd_sfx(args) -> int:
    if args.file:
        cues = read_cues(Path(args.file))
    elif args.prompt:
        cues = [("", args.prompt)]
    else:
        raise UsageError("Describe a sound, or give --file with a cue sheet.")
    params = [
        {
            "id": cid,
            "prompt": text,
            "duration": args.seconds,
            "takes": args.takes,
            "trim": not args.raw,
            "seed": args.seed,
        }
        for cid, text in cues
    ]
    return generate(args, "sa3-sfx", params)


def picture_models() -> dict[str, str]:
    """The image models that start from a picture (`--from`), each with its workflow.

    Read from what the engine stages for a workflow, not listed here: a model whose
    workflow takes no `image1` would be given the picture and quietly ignore it."""
    from irisecho_core import registry
    from irisecho_core.engines.comfy import graphs

    found = {}
    for m in registry.default().models.values():
        for v in m.variants if m.kind == "image" else ():
            if "image1" in graphs.IMAGE_INPUTS.get(v.workflow, ()):
                found[m.id] = v.workflow
                break
    return found


def cmd_image(args) -> int:
    extra: dict = {}
    registry_model(args.model)
    if args.source or args.size:
        takers = picture_models()
        if args.source and args.model not in takers:
            raise UsageError(f"--from works with {', '.join(takers)}.")
        # width and height are read by the FLUX workflow only
        flux = [m for m, workflow in takers.items() if workflow == "flux-nunchaku"]
        if args.size and args.model not in flux:
            raise UsageError(f"--size works with {', '.join(flux)}.")
    if args.size:
        try:
            w, h = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            raise UsageError("--size takes WIDTHxHEIGHT, for example 1920x1088.") from None
        extra.update(width=w, height=h)
    if args.source:
        extra.update(image1=stage_upload(Path(args.source)), denoise=args.denoise)
    if args.guide:
        extra.update(guide=stage_upload(Path(args.guide)), guide_type=args.guide_type)
    elif args.model == "z-image-control":
        raise UsageError("z-image-control needs --guide PICTURE.")
    params = [
        {
            "prompt": args.prompt,
            "aspect": args.aspect,
            "seed": (args.seed + i) if args.seed is not None else None,
            **extra,
        }
        for i in range(args.count)
    ]
    return generate(args, args.model, params)


def result_3d(job_id, model, error=None, needs=(), dest=None, glb=None, out=None, warnings=()):
    """The `--json` object of `3d`: the usual one, plus where the GLB is and whether it prints."""
    info = (out or {}).get("mesh") or {}
    done = result(
        job_id=job_id,
        label="",
        status="failed" if error else "done",
        model=model,
        outputs=[str(dest)] if dest else [],
        seed=(out or {}).get("seed"),
        error=error,
        needs=needs,
        warnings=warnings,
    )
    return {
        **done,
        "glb": glb,
        "printable": info.get("printable"),
        "reasons": info.get("reasons", []),
    }


def cmd_3d(args) -> int:
    """A 3D model from a picture, the way the 3D studio makes one."""
    try:
        return build_3d(args)
    except CliError as e:
        if args.json and not e.reported:
            print(json.dumps(result_3d(e.job_id, args.model, str(e), e.needs)), flush=True)
            e.reported = True
        raise


def build_3d(args) -> int:
    from irisecho_core import mesh3d

    if args.back and args.model in ("trellis2", "pixal3d-single"):
        raise UsageError(
            f"--back goes with Pixal3D; {args.model} builds from one picture. "
            "Use --model auto or pixal3d with --back, or leave --back out."
        )
    build = {
        "detail": args.detail,
        "textures": not args.no_textures,
        "openings": "keep" if args.keep_openings else "close",
        "faces": args.faces,
        "seed": args.seed,
    }
    build.update(parse_sets(args.set))
    for model_id in ("pixal3d", "trellis2") if args.model == "auto" else (args.model,):
        precheck(model_id, [build])
    front = stage_upload(Path(args.picture))
    back = stage_upload(Path(args.back)) if args.back else None

    async def go(backend):
        model, params = args.model, {**build, "front": front}
        if back:
            model, params["back"] = "pixal3d", back
        elif model in ("auto", "pixal3d"):
            # The View Maker checks the angle and draws the matching back.
            await backend.wait_ready("object-views", args.accept_license)
            level = "auto" if model == "auto" else "best"
            views = await backend.submit("object-views", {"image1": front, "level": level})
            views = await follow(backend, views, "Views")
            roles = {o.get("role"): o for o in views["outputs"]}
            if "back" not in roles:
                note("Taken from above or at an angle: building from the picture alone.")
                model = "trellis2"
            elif not roles["back"].get("matched") and model == "auto":
                note("No back view matched the front: building from the picture alone.")
                model = "trellis2"
            else:
                model = "pixal3d"
                params["front"] = stage_upload(Path(roles["front"]["path"]))
                params["back"] = stage_upload(Path(roles["back"]["path"]))
        await backend.wait_ready(model, args.accept_license)
        job = await backend.submit(model, params)
        warnings = show_warnings(job)
        job = await follow(backend, job, "Building")
        out = job["outputs"][0]
        src = Path(out["path"])
        dest_dir = Path(args.out) if args.out else src.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        data, ext = await asyncio.to_thread(mesh3d.export, src, args.format, args.height)
        dest = dest_dir / f"{src.stem}.{ext}"
        dest.write_bytes(data)
        if args.json:
            done = result_3d(job["id"], model, dest=dest, glb=str(src), out=out, warnings=warnings)
            print(json.dumps(done), flush=True)
        else:
            print(f"{dest}  ({mesh3d_summary(out.get('mesh') or {})})")
        return 0

    return with_backend(go)


def note(text: str) -> None:
    progress_end()
    print(text, file=sys.stderr)


# mesh3d.FORMATS, without importing numpy and trimesh for every command
MODEL3D_FORMATS = ("glb", "stl", "3mf", "obj", "ply")


def mesh3d_summary(info: dict) -> str:
    if not info or "error" in info:
        return "not measured"
    if info.get("printable"):
        return f"{info['faces']:,} faces, ready to print"
    return f"{info['faces']:,} faces; " + " ".join(info.get("reasons", []))


def cmd_write(args) -> int:
    draft = (args.draft or "").strip()
    upload = stage_upload(Path(args.image)) if args.image else ""
    if not draft and not upload:
        raise UsageError("Give a rough prompt, an --image, or both.")
    mode = "improve_image" if draft and upload else "describe" if upload else "improve"
    params = {
        "mode": mode,
        "target": args.target,
        "text": draft,
        "images": [upload] if upload else [],
    }

    async def go(backend):
        styled = [m["id"] for m in await backend.models() if m["prompt_style"]]
        if args.target not in styled:
            raise UsageError(f"--for must be one of: {', '.join(styled)}")
        # Progress goes to stderr, so stdout carries only the prompt.
        await backend.wait_ready("prompt-writer", args.accept_license)
        job = await backend.submit("prompt-writer", params)
        show_warnings(job)
        job = await poll(backend, job)
        if job["status"] != "done":
            raise CliError(f"failed: {job.get('error') or job['status']}", job_id=job["id"])
        print(job["outputs"][0]["text"])
        return 0

    return with_backend(go)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="irisecho",
        description="Local image, video, voice, music, sound-effect and 3D model generation.",
    )
    parser.add_argument("--version", action="version", version=f"irisecho {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="run the local server")
    p.add_argument(
        "--port",
        type=int,
        help=f"fail if it is taken (default: {DEFAULT_PORT}, or a free one when that is taken)",
    )
    p.add_argument("--open", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--stdin-watch", action="store_true", help="exit when stdin closes")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("info", help="show this machine and what it can run")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("models", help="list models and whether they are ready")
    p.add_argument("--json", action="store_true", help="everything known about each model")
    p.add_argument(
        "--controls",
        nargs="?",
        const="all",
        metavar="MODEL",
        help="list a model's advanced controls and limits (for --set); every model's without MODEL",
    )
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("setup", help="install and download everything a model needs")
    p.add_argument("model")
    add_license(p)
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("link", help="use model files from folders you already have")
    p.add_argument("folders", nargs="+")
    p.add_argument("--remove", action="store_true", help="stop using these folders")
    p.set_defaults(fn=cmd_link)

    p = sub.add_parser("say", help="speak a line, or every line of a cue sheet")
    p.add_argument("text", nargs="?")
    p.add_argument("--file", help="cue sheet: one `id|text` per line")
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--raw", action="store_true", help="skip silence trim and loudness matching")
    add_license(p)
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    add_set(p)
    p.set_defaults(fn=cmd_say)

    p = sub.add_parser("clone", help="speak in a voice cloned from a short clip")
    p.add_argument("text", nargs="?")
    p.add_argument(
        "--ref", required=True, help="10-15 s of one clear voice (WAV, MP3, FLAC or OGG)"
    )
    p.add_argument("--file", help="cue sheet: one `id|text` per line")
    p.add_argument(
        "--model", default="chatterbox-turbo", choices=["chatterbox-turbo", "chatterbox"]
    )
    p.add_argument("--takes", type=int, default=1, help="takes of each line, one seed apart")
    p.add_argument("--seed", type=int)
    p.add_argument("--exaggeration", type=float, default=0.5, help="classic model only")
    p.add_argument("--cfg", type=float, default=0.5, help="classic model only")
    p.add_argument("--raw", action="store_true", help="skip silence trim and loudness matching")
    add_license(p)
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    add_set(p)
    p.set_defaults(fn=cmd_clone)

    p = sub.add_parser("music", help="compose music from style tags, or a cue sheet")
    p.add_argument("prompt", nargs="?", help="comma-separated style tags")
    p.add_argument(
        "--model",
        default="ace-step",
        choices=[
            "ace-step",
            "ace-step-xl",
            "ace-step-xl-sft",
            "yue2",
            "minimax-music3",
            "sa3-medium",
        ],
        help=(
            "ace-step-xl: richer, about twice as slow; ace-step-xl-sft: most detailed, slowest; "
            "yue2, minimax-music3: songs and instrumentals; sa3-medium: instrumentals only"
        ),
    )
    p.add_argument("--file", help="cue sheet: one `id|seconds|bpm|tags` per line")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--bpm", type=int)
    p.add_argument("--takes", type=int, default=1)
    p.add_argument("--loop", action="store_true", help="cut on the bar into a seamless loop")
    p.add_argument(
        "--thinking",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="plan tempo, key and form first (on by default; --no-thinking is faster)",
    )
    p.add_argument("--lyrics", help="sung lyrics (instrumental when omitted)")
    p.add_argument("--lyrics-file", help="read the sung lyrics from this text file")
    p.add_argument("--seed", type=int)
    add_license(p)
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    add_set(p)
    p.set_defaults(fn=cmd_music)

    p = sub.add_parser("sfx", help="make sound effects from a description, or a cue sheet")
    p.add_argument("prompt", nargs="?", help="what the sound is, in plain words")
    p.add_argument("--file", help="cue sheet: one `id|description` per line")
    p.add_argument("--seconds", type=float, default=3, help="0.5 to 30")
    p.add_argument("--takes", type=int, default=3, help="takes of each sound, one seed apart")
    p.add_argument("--seed", type=int)
    p.add_argument("--raw", action="store_true", help="keep the silence the model pads with")
    add_license(p)
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    add_set(p)
    p.set_defaults(fn=cmd_sfx)

    p = sub.add_parser("image", help="make images from a prompt")
    p.add_argument("prompt")
    p.add_argument("--model", default="z-image-turbo")
    p.add_argument(
        "--aspect", default="1:1", choices=["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"]
    )
    p.add_argument("--seed", type=int)
    p.add_argument("--count", type=int, default=1)
    p.add_argument(
        "--from",
        dest="source",
        help="redraw this picture (the FLUX models and qwen-image-21; others refuse it)",
    )
    p.add_argument("--denoise", type=float, default=0.2, help="how much --from changes, 0.05-1")
    p.add_argument("--size", help="WIDTHxHEIGHT, for example 1920x1088 (FLUX models)")
    p.add_argument("--guide", help="a picture to follow (z-image-control)")
    p.add_argument(
        "--guide-type",
        default="canny",
        choices=["canny", "depth", "pose", "as_is"],
        help="what --guide gives: outlines, depth or pose; as_is if it already is such a map",
    )
    add_license(p)
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    add_set(p)
    p.set_defaults(fn=cmd_image)

    p = sub.add_parser("3d", help="make a 3D model from a picture")
    p.add_argument("picture", help="the object, ideally whole and on a plain background")
    p.add_argument("--back", help="a picture of the object from behind, at the same height")
    p.add_argument(
        "--model",
        default="auto",
        choices=["auto", "pixal3d", "trellis2", "pixal3d-single"],
        help="auto: a matching back view and Pixal3D when the picture is at eye level, "
        "else TRELLIS.2 from the picture alone (default); pixal3d-single: Pixal3D from "
        "the picture alone",
    )
    p.add_argument("--detail", default="standard", choices=["standard", "high"])
    p.add_argument("--no-textures", action="store_true", help="shape only, faster")
    p.add_argument(
        "--faces",
        type=int,
        default=500_000,
        choices=[500_000, 100_000, 20_000],
        help="triangles: 500000 for printing, 100000 or 20000 for 3D programs and games",
    )
    p.add_argument(
        "--keep-openings", action="store_true", help="leave cups and vases open at the top"
    )
    p.add_argument("--format", default="glb", choices=MODEL3D_FORMATS)
    p.add_argument("--height", type=float, help="printed height in mm for stl and 3mf (100)")
    p.add_argument("--seed", type=int)
    add_license(p)
    p.add_argument("--out", help="write the model into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object on stdout")
    add_set(p)
    p.set_defaults(fn=cmd_3d)

    p = sub.add_parser("write", help="improve a prompt, or describe a picture as one")
    p.add_argument("draft", nargs="?", help="a rough idea to improve")
    p.add_argument("--for", dest="target", default="z-image-turbo", help="the model it is for")
    p.add_argument("--image", help="describe this picture, or improve the draft using it")
    add_license(p)
    p.set_defaults(fn=cmd_write)

    p = sub.add_parser("api", help="call the running app's local API")
    p.add_argument("method", help="GET, POST, PATCH, PUT or DELETE")
    p.add_argument("path", help="for example models, or jobs/3f9c2a71b0de")
    p.add_argument("body", nargs="?", help="JSON to send; - reads it from stdin")
    p.add_argument("--wait", action="store_true", help="follow a submitted job until it ends")
    p.set_defaults(fn=cmd_api)

    p = sub.add_parser("agents", help="print the guide for scripts and coding agents")
    p.set_defaults(fn=cmd_agents)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["serve", "--open"])
    try:
        return args.fn(args)
    except CliError as e:
        progress_end()
        print(str(e), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
