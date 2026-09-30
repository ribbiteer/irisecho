# SPDX-License-Identifier: AGPL-3.0-or-later
"""Image and video engine: a private, pinned, headless ComfyUI.

IrisEcho installs its own ComfyUI under <data>/engines/comfy, runs it on a
random local port with its own input/output/temp folders and database, and
talks to it over its HTTP and websocket API. It never uses or touches a
ComfyUI the person already has; their model folders can be linked read-only.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import random
import shutil
import socket
import struct
import sys
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path

import httpx

from irisecho_core import paths
from irisecho_core.engines import uvenv
from irisecho_core.engines.base import Engine, EngineStartError, RunContext
from irisecho_core.engines.comfy import graphs

# Versions proven together. Bump install_version when changing any of them.
COMFY = ("Comfy-Org/ComfyUI", "700821e1364eaab0e8f21c538a2131719fec57bf")  # v0.28.0
CUSTOM_NODES = {
    "ComfyUI-nunchaku": (
        "nunchaku-ai/ComfyUI-nunchaku",
        "c71cc259355658e87bc418095c3ef1a65b8f115b",
    ),
    "ComfyUI-GGUF": ("city96/ComfyUI-GGUF", "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"),
}
TORCH = "torch==2.11.0"
NUNCHAKU = "https://github.com/nunchux-ai/nunchaku/releases/download/v1.2.1/"
NUNCHAKU_WHEELS = {
    ("win32", "cu130"): (
        "nunchaku-1.2.1+cu13.0torch2.11-cp313-cp313-win_amd64.whl",
        "722a365a362bd57fd4e6c583ac7a00dfeecd89b658a50f2011c42d5359fc92b6",
    ),
    ("win32", "cu128"): (
        "nunchaku-1.2.1+cu12.8torch2.11-cp313-cp313-win_amd64.whl",
        "20b95cb6415bdc71c65afeb9d401d03e8d7629534987196d235cb865343929cb",
    ),
    ("linux", "cu130"): (
        "nunchaku-1.2.1+cu13.0torch2.11-cp313-cp313-linux_x86_64.whl",
        "b2a2aa30ad9077b6d589a74a52001b032fcdb33d72005381092253ad79139379",
    ),
    ("linux", "cu128"): (
        "nunchaku-1.2.1+cu12.8torch2.11-cp313-cp313-linux_x86_64.whl",
        "149e9d8ec015a9c63f384c271a894ce667171325468271c9536d888ec7af5c2e",
    ),
}
# ComfyUI-nunchaku's face-identity features (PuLID) need packages without
# Windows wheels; IrisEcho does not use them.
NUNCHAKU_NODE_DEPS = [
    "diffusers>=0.35",
    "transformers>=4.54",
    "sentencepiece",
    "protobuf",
    "huggingface_hub>=0.34",
    "tomli",
    "peft>=0.17",
    "accelerate>=1.10",
    "timm",
]
# A cold start is normally well under a minute; the first one after installing is slower.
START_TIMEOUT = 300
CATEGORIES = (
    "diffusion_models",
    "text_encoders",
    "vae",
    "loras",
    "upscale_models",
    "frame_interpolation",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png_size(path: Path) -> tuple[int, int] | None:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


class ComfyEngine(Engine):
    id = "comfy"
    name = "ComfyUI"
    install_version = 1
    python_version = "3.13"

    def __init__(self, hardware, app):
        super().__init__(hardware)
        self.app = app
        self.venv = self.home / ".venv"
        self.src = self.home / "ComfyUI"
        self.io = self.home / "io"
        self.proc: asyncio.subprocess.Process | None = None
        self.port = 0
        self.paths_key = ""
        self.log_tail: deque[str] = deque(maxlen=80)
        self._start_lock = asyncio.Lock()
        self.prompt_of: dict[str, str] = {}  # job id -> prompt id
        self._log_task: asyncio.Task | None = None

    # --- installation ------------------------------------------------------

    def supported(self) -> bool:
        if self.hw.backend == "cuda":
            return (sys.platform, self.hw.cuda_tag) in NUNCHAKU_WHEELS
        return self.hw.backend == "mps"

    def install_key(self) -> str:
        return f"{super().install_key()}:{COMFY[1][:12]}"

    async def _fetch_source(self, repo: str, sha: str, dest: Path, log) -> None:
        log(f"Downloading {repo} @ {sha[:10]}")
        url = f"https://codeload.github.com/{repo}/zip/{sha}"
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            r = await client.get(url)
            r.raise_for_status()
        if dest.exists():
            shutil.rmtree(dest)
        tmp = dest.with_name(dest.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(tmp)
        (inner,) = list(tmp.iterdir())
        inner.rename(dest)
        tmp.rmdir()

    async def install(self, log) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        await self._fetch_source(*COMFY, self.src, log)
        for name, (repo, sha) in CUSTOM_NODES.items():
            if name == "ComfyUI-nunchaku" and self.hw.backend != "cuda":
                continue
            await self._fetch_source(repo, sha, self.src / "custom_nodes" / name, log)

        if self.venv.exists():
            shutil.rmtree(self.venv)
        await uvenv.create_venv(self.venv, self.python_version, log)
        packages = [
            TORCH,
            "torchvision",
            "torchaudio",
            "-r",
            str(self.src / "requirements.txt"),
            "gguf>=0.13.0",
        ]
        if self.hw.backend == "cuda":
            wheel, sha = NUNCHAKU_WHEELS[(sys.platform, self.hw.cuda_tag)]
            packages += [f"nunchaku @ {NUNCHAKU}{wheel.replace('+', '%2B')}#sha256={sha}"]
            packages += NUNCHAKU_NODE_DEPS
        log("Installing PyTorch, ComfyUI and nunchaku (several GB, one time)")
        await uvenv.pip_install(
            self.venv, packages, log, *uvenv.torch_backend_args(self.hw.backend, self.hw.cuda_tag)
        )
        self.mark_installed()

    # --- process -----------------------------------------------------------

    def _model_paths_yaml(self) -> str:
        lines = ["irisecho:"]
        for cat in CATEGORIES:
            dirs = self.app.store.category_dirs(cat)
            (dirs[0]).mkdir(parents=True, exist_ok=True)
            lines.append(f"  {cat}: |")
            lines += [f"    {d}" for d in dirs]
        return "\n".join(lines) + "\n"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def ensure_running(self, report, cancelled: asyncio.Event | None = None) -> None:
        async with self._start_lock:
            yaml_text = self._model_paths_yaml()
            if self.alive and yaml_text == self.paths_key:
                return
            if self.alive:
                await self.stop()
            report(None, "Starting the image engine")
            self.state = "starting"
            for sub in ("input", "output", "temp", "user"):
                (self.io / sub).mkdir(parents=True, exist_ok=True)
            watchdog = self.src / "custom_nodes" / "irisecho_watchdog"
            watchdog.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).parent / "watchdog_node.py", watchdog / "__init__.py")
            cfg = self.home / "extra_model_paths.yaml"
            cfg.write_text(yaml_text, encoding="utf-8")
            self.paths_key = yaml_text
            self.port = _free_port()
            args = [
                str(uvenv.venv_python(self.venv)),
                "-s",
                str(self.src / "main.py"),
                "--listen",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--disable-auto-launch",
                "--disable-metadata",
                "--disable-dynamic-vram",
                "--extra-model-paths-config",
                str(cfg),
                "--input-directory",
                str(self.io / "input"),
                "--output-directory",
                str(self.io / "output"),
                "--temp-directory",
                str(self.io / "temp"),
                "--user-directory",
                str(self.io / "user"),
                "--database-url",
                f"sqlite:///{(self.io / 'comfyui.db').as_posix()}",
            ]
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "DO_NOT_TRACK": "1",
                    "IRISECHO_PARENT_PID": str(os.getpid()),
                }
            )
            self.proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self.src),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            self.log_tail.clear()
            self._log_task = asyncio.create_task(self._pump_log())
            try:
                await self._wait_until_up(report, cancelled)
            except BaseException:
                # Never leave a half-started engine behind: the next job starts a fresh one.
                await self.stop()
                raise
            self.state = "ready"

    async def _wait_until_up(self, report, cancelled: asyncio.Event | None) -> None:
        started = time.monotonic()
        told = 0
        async with httpx.AsyncClient(timeout=3) as client:
            while (waited := time.monotonic() - started) < START_TIMEOUT:
                if cancelled is not None and cancelled.is_set():
                    raise asyncio.CancelledError
                if not self.alive:
                    if self._log_task:  # let its last words arrive before quoting them
                        await asyncio.wait({self._log_task}, timeout=2)
                    raise EngineStartError(
                        self._crash_message("The image engine stopped while starting.")
                    )
                try:
                    r = await client.get(self.url("/system_stats"))
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                if waited - told >= 10:
                    told = int(waited)
                    report(None, f"Starting the image engine · {told} s")
                await asyncio.sleep(0.5)
        raise EngineStartError(
            self._crash_message(
                f"The image engine did not start within {START_TIMEOUT // 60} minutes."
            )
        )

    async def _pump_log(self) -> None:
        assert self.proc and self.proc.stdout
        with open(paths.sub("logs") / "comfy.log", "a", encoding="utf-8") as log:
            async for raw in self.proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                self.log_tail.append(line)
                log.write(line + "\n")
                log.flush()

    def _crash_message(self, head: str) -> str:
        tail = [x for x in self.log_tail if x.strip()][-10:]
        return head + ("\n" + "\n".join(tail) if tail else "")

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self._log_task:
            self._log_task.cancel()
        self.proc = None
        self.state = "idle"

    # --- jobs --------------------------------------------------------------

    async def run(self, ctx: RunContext) -> list[dict]:
        builder = graphs.BUILDERS.get(ctx.variant.workflow)
        if builder is None:
            raise RuntimeError(f"no workflow {ctx.variant.workflow!r}")
        workflow = ctx.variant.workflow
        prompt = (ctx.params.get("prompt") or "").strip()
        if not prompt and workflow not in graphs.PROMPT_OPTIONAL:
            raise ValueError("Describe what you want first.")
        seed = ctx.params.get("seed")
        if seed is None:
            seed = random.randrange(2**50)
        names = {fid: path.name for fid, path in ctx.files.items()}
        staged = self._stage_inputs(ctx, workflow)
        try:
            graph, save_node = builder(
                prompt=prompt,
                seed=int(seed),
                aspect=ctx.params.get("aspect", "1:1"),
                names=names,
                settings=ctx.model.settings,
                hw=self.hw,
                prefix=f"irisecho/{ctx.job_id}",
                images={k: p.name for k, p in staged.items()},
                params=ctx.params,
            )
            await self.ensure_running(ctx.report, ctx.cancelled)
            self.state = "busy"
            try:
                files = await self._execute(ctx, graph, save_node)
            finally:
                self.state = "ready" if self.alive else "idle"
        finally:
            for p in staged.values():
                p.unlink(missing_ok=True)
        outputs = []
        for i, src in enumerate(files):
            ext = src.suffix.lstrip(".").lower() or "png"
            dest = ctx.output(i, ext)
            shutil.move(str(src), dest)
            if ext in ("mp4", "webm", "mov"):
                outputs.append({"path": str(dest), "type": "video", "seed": int(seed)})
                continue
            size = png_size(dest)
            outputs.append(
                {
                    "path": str(dest),
                    "type": "image",
                    "width": size[0] if size else None,
                    "height": size[1] if size else None,
                    "seed": int(seed),
                }
            )
        ctx.params["seed"] = int(seed)
        return outputs

    def _stage_inputs(self, ctx: RunContext, workflow: str) -> dict[str, Path]:
        """Copy the job's uploaded pictures into ComfyUI's private input folder."""
        staged: dict[str, Path] = {}
        (self.io / "input").mkdir(parents=True, exist_ok=True)
        for key in graphs.IMAGE_INPUTS.get(workflow, ()):
            name = ctx.params.get(key)
            if not name:
                continue
            src = paths.sub("uploads") / Path(str(name)).name
            if not src.is_file():
                raise ValueError("A picture you added is no longer available; add it again.")
            dest = self.io / "input" / f"irisecho_{ctx.job_id}_{key}{src.suffix.lower()}"
            shutil.copyfile(src, dest)
            staged[key] = dest
        return staged

    async def _execute(self, ctx: RunContext, graph: dict, save_node: str) -> list[Path]:
        import websockets

        client_id = uuid.uuid4().hex
        labels = graphs.stage_labels(graph)
        first_load = True
        async with websockets.connect(
            f"ws://127.0.0.1:{self.port}/ws?clientId={client_id}", max_size=None
        ) as ws:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    self.url("/prompt"), json={"prompt": graph, "client_id": client_id}
                )
                data = r.json()
                if r.status_code != 200 or data.get("node_errors"):
                    raise RuntimeError(
                        f"The image engine rejected the job: {graphs.describe_error(data)}"
                    )
                prompt_id = data["prompt_id"]
                self.prompt_of[ctx.job_id] = prompt_id
                try:
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            continue  # live previews
                        msg = json.loads(raw)
                        kind, d = msg.get("type"), msg.get("data") or {}
                        if d.get("prompt_id") not in (None, prompt_id):
                            continue
                        if kind == "executing":
                            node = d.get("node")
                            if node is None:
                                break
                            label = labels.get(node)
                            if label == "load" and first_load:
                                ctx.report(0.03, f"Loading {ctx.model.name}")
                                first_load = False
                            elif label == "sample":
                                ctx.report(0.1, "Drawing")
                            elif label == "decode":
                                ctx.report(0.95, "Finishing")
                        elif kind == "progress" and labels.get(str(d.get("node"))) == "sample":
                            value, total = d.get("value", 0), max(1, d.get("max", 1))
                            ctx.report(
                                0.1 + 0.85 * value / total, f"Drawing · step {value} of {total}"
                            )
                        elif kind == "execution_error":
                            raise RuntimeError(
                                d.get("exception_message") or "The image engine failed."
                            )
                        elif kind == "execution_interrupted":
                            raise asyncio.CancelledError
                        elif kind == "execution_success":
                            break
                    if ctx.cancelled.is_set():
                        raise asyncio.CancelledError
                    r = await client.get(self.url(f"/history/{prompt_id}"))
                    entry = r.json().get(prompt_id) or {}
                    status = entry.get("status") or {}
                    if status.get("status_str") == "error":
                        raise RuntimeError(graphs.history_error(status))
                    images = ((entry.get("outputs") or {}).get(save_node) or {}).get("images") or []
                    out = []
                    for img in images:
                        base = self.io / img.get("type", "output")
                        out.append(base / img.get("subfolder", "") / img["filename"])
                    if not out:
                        raise RuntimeError("The image engine finished without an image.")
                    # Leave nothing behind in ComfyUI's own history.
                    await client.post(self.url("/history"), json={"delete": [prompt_id]})
                    return out
                finally:
                    self.prompt_of.pop(ctx.job_id, None)

    async def cancel(self, job_id: str) -> None:
        prompt_id = self.prompt_of.get(job_id)
        if not prompt_id or not self.alive:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(self.url("/queue"), json={"delete": [prompt_id]})
            await client.post(self.url("/interrupt"), json={"prompt_id": prompt_id})

    async def release(self) -> None:
        """Unload models so another engine can use the GPU; the process stays up."""
        if not self.alive:
            return
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    self.url("/free"), json={"unload_models": True, "free_memory": True}
                )
        except httpx.HTTPError:
            await self.stop()

    async def shutdown(self) -> None:
        await self.stop()
