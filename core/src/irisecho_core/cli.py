# SPDX-License-Identifier: AGPL-3.0-or-later
"""`irisecho` command line.

irisecho                     open the app
irisecho serve               run the local server (used by the desktop shell)
irisecho info                this machine and what it can run
irisecho models              every model and whether it is ready
irisecho setup MODEL         install and download everything a model needs
irisecho say TEXT            speak a line (or --file cues.txt for a script)
irisecho image PROMPT        make images
irisecho write DRAFT         improve a prompt, or describe a picture as one
irisecho api METHOD PATH     call the running app's local API (api GET models)
irisecho agents              the guide for scripts and coding agents

When the app is already running, commands hand their work to it instead of
starting engines of their own.
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

from irisecho_core import __version__, paths, platform_support

DEFAULT_PORT = 7788


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- server & window --------------------------------------------------------


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
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

    port = free_port(args.port)
    api = create_app()
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


async def wait_ready(app, model_id: str, accept_license: bool = False) -> bool:
    from irisecho_core.app import NotReady

    model = app.model(model_id)
    status = app.model_status(model)
    if "unsupported" in status["needs"]:
        print(f"{model.name} cannot run on this machine.", file=sys.stderr)
        return False
    if "license" in status["needs"]:
        lic = status["license"]
        if not accept_license:
            print(
                f"{model.name} uses the {lic['name']}:\n  {lic['url']}\n"
                f"{lic['summary']}\nRun again with --accept-license to accept it.",
                file=sys.stderr,
            )
            return False
        app.accept_license(lic["id"])
    try:
        app.prepare(model_id)
    except NotReady as e:
        print(str(e), file=sys.stderr)
        return False
    last = ""
    while True:
        status = app.model_status(model)
        if status["ready"]:
            if last:
                print()
            return True
        if model.engine in app.install_tasks:
            log = app.install_logs.get(model.engine)
            line = f"Installing {model.engine}: {(log[-1] if log else '')[:70]}"
        else:
            engine = app.engines[model.engine]
            if engine.state == "error":
                print(f"\nInstalling {engine.name} failed:\n{engine.detail}", file=sys.stderr)
                return False
            blocked = [
                d
                for d in status["downloads"]
                if d["state"] in ("error", "needs_token", "needs_access")
            ]
            if blocked:
                print(f"\n{blocked[0]['id']}: {blocked[0]['error']}", file=sys.stderr)
                return False
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
            print("\r" + line.ljust(90), end="", flush=True)
            last = line
        await asyncio.sleep(0.5)


# --- where the work runs ------------------------------------------------------
#
# A command's work goes to the IrisEcho that is already running when there is
# one (the window, or `irisecho serve`), so there is one queue and one tenant on
# the GPU. Otherwise this process runs the engines itself. Commands are written
# against the few calls both offer.

ENDED = ("done", "failed", "cancelled", "interrupted")


class CliError(Exception):
    """Something to tell the person and stop; not a bug."""


class Local:
    """This process runs the engines itself."""

    def __init__(self, app):
        self.app = app

    async def models(self) -> list[dict]:
        return self.app.models()

    async def wait_ready(self, model_id: str, accept_license: bool = False) -> bool:
        return await wait_ready(self.app, model_id, accept_license)

    async def submit(self, model_id: str, params: dict) -> dict:
        return self.app.submit(model_id, params)

    async def job(self, job_id: str) -> dict:
        return self.app.db.get(job_id)

    async def linked_dirs(self) -> list[str]:
        return list(self.app.settings.linked_model_dirs)

    async def link(self, dirs: list[str]) -> None:
        self.app.set_linked_dirs(dirs)
        await self.app.scan_task


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
    def find(cls) -> Remote | None:
        """The app running on this data folder, if it answers to the token it wrote down."""
        import httpx

        try:
            info = json.loads((paths.data_dir() / "server.json").read_text(encoding="utf-8"))
            remote = cls(int(info["port"]), str(info["token"]))
            if remote.http.get("/api/system", timeout=2).status_code == 200:
                return remote
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            pass
        return None

    def call(self, method: str, path: str, body=None):
        r = self.http.request(method, path, json=body)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            why = data.get("error") or data.get("detail") if isinstance(data, dict) else ""
            raise CliError(str(why or f"IrisEcho answered {r.status_code} to {method} {path}."))
        return data

    async def models(self) -> list[dict]:
        return self.call("GET", "/api/models")

    async def wait_ready(self, model_id: str, accept_license: bool = False) -> bool:
        last = ""
        while True:
            model = next((m for m in await self.models() if m["id"] == model_id), None)
            if model is None:
                raise CliError(f"unknown model {model_id}")
            needs = model["needs"]
            if model["ready"]:
                if last:
                    print()
                return True
            if "unsupported" in needs:
                print(f"{model['name']} cannot run on this machine.", file=sys.stderr)
                return False
            if "license" in needs:
                if not accept_license:
                    print(license_notice(model), file=sys.stderr)
                    return False
                self.call("POST", f"/api/licenses/{model['license']['id']}/accept")
                continue
            engine = next(e for e in self.call("GET", "/api/engines") if e["id"] == model["engine"])
            downloads = model["downloads"]
            blocked = [d for d in downloads if d["state"] in BLOCKED]
            if blocked:
                print(f"\n{blocked[0]['id']}: {blocked[0]['error']}", file=sys.stderr)
                return False
            installing = model["installing"] or engine["state"] == "installing"
            if engine["state"] == "error" and not installing:
                print(f"\nInstalling {engine['name']} failed:\n{engine['detail']}", file=sys.stderr)
                return False
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
                print("\r" + line.ljust(90), end="", flush=True)
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


ACTIVE = ("queued", "downloading", "verifying")
BLOCKED = ("error", "needs_token", "needs_access")


def license_notice(model: dict) -> str:
    lic = model["license"]
    return (
        f"{model['name']} uses the {lic['name']}:\n  {lic['url']}\n"
        f"{lic['summary']}\nRun again with --accept-license to accept it."
    )


def with_backend(fn) -> int:
    """Run `fn(backend)` against the running app if there is one, else in this process."""
    remote = Remote.find()
    if remote is not None:
        return asyncio.run(fn(remote))

    async def go(app):
        return await fn(Local(app))

    return asyncio.run(with_app(go))


async def run_jobs(
    backend, model_id: str, param_list: list[dict], out: Path | None, as_json: bool = False
) -> int:
    """Queue each job and follow it to the end. With `as_json`, stdout carries one object a job."""
    failures = 0
    notes = sys.stderr if as_json else sys.stdout
    for params in param_list:
        job = await backend.submit(model_id, params)
        label = params.get("id") or (params.get("prompt") or params.get("text") or "")[:50]
        while True:
            job = await backend.job(job["id"])
            if job["status"] in ENDED:
                break
            msg = job.get("message") or job["status"]
            pct = f" {job['progress'] * 100:.0f}%" if job.get("progress") is not None else ""
            print(f"\r{label}: {msg}{pct}".ljust(90), end="", flush=True, file=notes)
            await asyncio.sleep(0.25)
        files = []
        if job["status"] != "done":
            failures += 1
            why = job.get("error") or job["status"]
            print(f"\r{label}: failed: {why}".ljust(90), file=notes)
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
            print(f"\r{label}: {src}".ljust(90), file=notes)
        if as_json:
            result = {
                "id": job["id"],
                "label": params.get("id") or "",
                "status": job["status"],
                "model": model_id,
                "outputs": files,
                "seed": next((o.get("seed") for o in job["outputs"] if "seed" in o), None),
                "error": job.get("error"),
            }
            print(json.dumps(result), flush=True)
    return 1 if failures else 0


def generate(args, model_id: str, param_list: list[dict], accept_license: bool = False) -> int:
    """What every generating command does once it knows its jobs."""

    async def go(backend):
        with contextlib.redirect_stdout(sys.stderr if args.json else sys.stdout):
            ready = await backend.wait_ready(model_id, accept_license)
        if not ready:
            return 1
        out = Path(args.out) if args.out else None
        return await run_jobs(backend, model_id, param_list, out, args.json)

    return with_backend(go)


def read_cues(path: Path) -> list[tuple[str, str]]:
    """Cue sheet: one line per file, `id|text` or just `text`; # comments."""
    cues = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
    from irisecho_core import hardware

    hw = hardware.detect()
    if args.json:
        print(json.dumps(hw.public(), indent=2))
        return 0
    info = platform_support.detect()
    for key, value in asdict(info).items():
        print(f"{key}: {value}")
    print(f"backend: {hw.backend}" + (f" ({hw.quant}, {hw.cuda_tag})" if hw.quant else ""))
    for g in hw.gpus:
        print(f"gpu: {g.name}, {g.vram_mb // 1024} GB, driver {g.driver}")
    print(f"ram: {hw.ram_mb // 1024} GB")
    print(f"data: {paths.data_dir()}")
    return 0


def cmd_models(args) -> int:
    async def go(backend):
        rows = await backend.models()
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
        ok = await backend.wait_ready(args.model, args.accept_license)
        if ok:
            print(f"{args.model} is ready.")
        return 0 if ok else 1

    return with_backend(go)


def cmd_link(args) -> int:
    async def go(backend):
        dirs = await backend.linked_dirs()
        for d in args.folders:
            full = str(Path(d).resolve())
            if not Path(full).is_dir():
                print(f"Not a folder: {d}", file=sys.stderr)
                return 1
            if args.remove:
                dirs = [x for x in dirs if x != full]
            elif full not in dirs:
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


def cmd_api(args) -> int:
    remote = Remote.find()
    if remote is None:
        print(
            "IrisEcho is not running. Open the app, or start it with: irisecho serve",
            file=sys.stderr,
        )
        return 1
    body = None
    if args.body is not None:
        raw = sys.stdin.read() if args.body == "-" else args.body
        try:
            body = json.loads(raw)
        except ValueError as e:
            print(f"The request body is not JSON: {e}", file=sys.stderr)
            return 2
    r = remote.http.request(args.method.upper(), api_path(args.path), json=body)
    if "application/json" not in r.headers.get("content-type", ""):
        sys.stdout.buffer.write(r.content)
        return 0 if r.status_code < 400 else 1
    data = r.json()
    ok = r.status_code < 400
    if args.wait and ok and isinstance(data, dict) and {"id", "status"} <= set(data):
        while data["status"] not in ENDED:
            time.sleep(0.5)
            data = remote.call("GET", f"/api/jobs/{data['id']}")
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
        print("Give a line to say, or --file with a cue sheet.", file=sys.stderr)
        return 2
    params = [
        {"id": cid, "text": text, "voice": args.voice, "speed": args.speed, "clean": not args.raw}
        for cid, text in cues
    ]
    return generate(args, "kokoro", params)


def read_music_cues(path: Path) -> list[dict]:
    """Cue sheet: `id|seconds|bpm|tags` per line (bpm may be empty); # comments."""
    cues = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            raise SystemExit(f"Expected id|seconds|bpm|tags, got: {line}")
        cue_id, seconds, bpm, tags = parts
        cues.append(
            {
                "id": cue_id,
                "duration": float(seconds),
                "bpm": int(bpm) if bpm else None,
                "prompt": tags,
            }
        )
    return cues


def cmd_clone(args) -> int:
    import uuid

    ref = Path(args.ref)
    if not ref.is_file():
        print(f"No such file: {ref}", file=sys.stderr)
        return 2
    upload = f"{uuid.uuid4().hex}{ref.suffix.lower()}"
    shutil.copyfile(ref, paths.sub("uploads") / upload)
    if args.file:
        cues = read_cues(Path(args.file))
    elif args.text:
        cues = [("", args.text)]
    else:
        print("Give a line to say, or --file with a cue sheet.", file=sys.stderr)
        return 2
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
        print("Give style tags, or --file with a cue sheet.", file=sys.stderr)
        return 2
    params = [
        {
            **cue,
            "takes": args.takes,
            "loop": args.loop,
            "thinking": args.thinking,
            "lyrics": args.lyrics or "",
            "seed": args.seed,
        }
        for cue in cues
    ]
    return generate(args, "ace-step", params)


def cmd_image(args) -> int:
    import uuid

    extra: dict = {}
    if (args.source or args.size) and args.model not in ("flux-schnell", "flux-dev", "flux-krea"):
        print("--from and --size work with flux-schnell, flux-dev and flux-krea.", file=sys.stderr)
        return 2
    if args.size:
        try:
            w, h = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            print("--size takes WIDTHxHEIGHT, for example 1920x1088.", file=sys.stderr)
            return 2
        extra.update(width=w, height=h)
    if args.source:
        src = Path(args.source)
        if not src.is_file():
            print(f"No such file: {src}", file=sys.stderr)
            return 2
        upload = f"{uuid.uuid4().hex}{src.suffix.lower()}"
        shutil.copyfile(src, paths.sub("uploads") / upload)
        extra.update(image1=upload, denoise=args.denoise)
    params = [
        {
            "prompt": args.prompt,
            "aspect": args.aspect,
            "seed": (args.seed + i) if args.seed is not None else None,
            **extra,
        }
        for i in range(args.count)
    ]
    return generate(args, args.model, params, args.accept_license)


def cmd_write(args) -> int:
    import uuid

    draft = (args.draft or "").strip()
    upload = ""
    if args.image:
        img = Path(args.image)
        if not img.is_file():
            print(f"No such file: {img}", file=sys.stderr)
            return 2
        upload = f"{uuid.uuid4().hex}{img.suffix.lower()}"
        shutil.copyfile(img, paths.sub("uploads") / upload)
    if not draft and not upload:
        print("Give a rough prompt, an --image, or both.", file=sys.stderr)
        return 2
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
            print(f"--for must be one of: {', '.join(styled)}", file=sys.stderr)
            return 2
        # Progress goes to stderr, so stdout carries only the prompt.
        with contextlib.redirect_stdout(sys.stderr):
            ready = await backend.wait_ready("prompt-writer")
        if not ready:
            return 1
        job = await backend.submit("prompt-writer", params)
        while job["status"] not in ENDED:
            await asyncio.sleep(0.25)
            job = await backend.job(job["id"])
        if job["status"] != "done":
            print(f"failed: {job.get('error') or job['status']}", file=sys.stderr)
            return 1
        print(job["outputs"][0]["text"])
        return 0

    return with_backend(go)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="irisecho",
        description="Local image, video, voice, music and sound-effect generation.",
    )
    parser.add_argument("--version", action="version", version=f"irisecho {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="run the local server")
    p.add_argument("--port", type=int, default=int(os.environ.get("IRISECHO_PORT", DEFAULT_PORT)))
    p.add_argument("--open", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--stdin-watch", action="store_true", help="exit when stdin closes")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("info", help="show this machine and what it can run")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("models", help="list models and whether they are ready")
    p.add_argument("--json", action="store_true", help="everything known about each model")
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("setup", help="install and download everything a model needs")
    p.add_argument("model")
    p.add_argument("--accept-license", action="store_true")
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
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    p.set_defaults(fn=cmd_say)

    p = sub.add_parser("clone", help="speak in a voice cloned from a short clip")
    p.add_argument("text", nargs="?")
    p.add_argument("--ref", required=True, help="5-15 s of clean speech; the first 6 s matter most")
    p.add_argument("--file", help="cue sheet: one `id|text` per line")
    p.add_argument(
        "--model", default="chatterbox-turbo", choices=["chatterbox-turbo", "chatterbox"]
    )
    p.add_argument("--takes", type=int, default=1, help="liveliest take is listed first")
    p.add_argument("--seed", type=int)
    p.add_argument("--exaggeration", type=float, default=0.5, help="classic model only")
    p.add_argument("--cfg", type=float, default=0.5, help="classic model only")
    p.add_argument("--raw", action="store_true", help="skip silence trim and loudness matching")
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    p.set_defaults(fn=cmd_clone)

    p = sub.add_parser("music", help="compose music from style tags, or a cue sheet")
    p.add_argument("prompt", nargs="?", help="comma-separated style tags")
    p.add_argument("--file", help="cue sheet: one `id|seconds|bpm|tags` per line")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--bpm", type=int)
    p.add_argument("--takes", type=int, default=1)
    p.add_argument("--loop", action="store_true", help="cut on the bar into a seamless loop")
    p.add_argument("--thinking", action="store_true", help="plan the structure first")
    p.add_argument("--lyrics", help="sung lyrics (instrumental when omitted)")
    p.add_argument("--seed", type=int)
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    p.set_defaults(fn=cmd_music)

    p = sub.add_parser("image", help="make images from a prompt")
    p.add_argument("prompt")
    p.add_argument("--model", default="z-image-turbo")
    p.add_argument(
        "--aspect", default="1:1", choices=["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"]
    )
    p.add_argument("--seed", type=int)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--from", dest="source", help="redraw this picture (FLUX models)")
    p.add_argument("--denoise", type=float, default=0.2, help="how much --from changes, 0.05-1")
    p.add_argument("--size", help="WIDTHxHEIGHT, for example 1920x1088 (FLUX models)")
    p.add_argument("--accept-license", action="store_true")
    p.add_argument("--out", help="copy results into this folder")
    p.add_argument("--json", action="store_true", help="one JSON object per job on stdout")
    p.set_defaults(fn=cmd_image)

    p = sub.add_parser("write", help="improve a prompt, or describe a picture as one")
    p.add_argument("draft", nargs="?", help="a rough idea to improve")
    p.add_argument("--for", dest="target", default="z-image-turbo", help="the model it is for")
    p.add_argument("--image", help="describe this picture, or improve the draft using it")
    p.set_defaults(fn=cmd_write)

    p = sub.add_parser("api", help="call the running app's local API")
    p.add_argument("method", help="GET, POST, PATCH, PUT or DELETE")
    p.add_argument("path", help="for example models, or jobs/3f9c2a71b0de")
    p.add_argument("body", nargs="?", help="JSON to send; - reads it from stdin")
    p.add_argument("--wait", action="store_true", help="follow a submitted job until it ends")
    p.set_defaults(fn=cmd_api)

    p = sub.add_parser("agents", help="print the guide for scripts and coding agents")
    p.set_defaults(fn=cmd_agents)

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
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
