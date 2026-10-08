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

from irisecho_core import hardware, mesh3d, paths
from irisecho_core.engines import uvenv
from irisecho_core.engines.base import Engine, EngineStartError, RunContext
from irisecho_core.engines.comfy import graphs, sourcepatch

# Versions proven together. Bump install_version when changing any of them.
COMFY = ("Comfy-Org/ComfyUI", "6b747c0428c343e1417219641db93a4fb7cb69ae")  # v0.38.0
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
# ComfyUI-nunchaku has not followed ComfyUI since v0.28: from v0.3x, ModelPatcher.clone
# passes fast_disk to the subclass, and the Z-Image patcher does not take it.
# (file, text to find, replacement); install fails if the text is gone.
NUNCHAKU_PATCHES = [
    (
        "model_patcher/zimage.py",
        "def __init__(self, model, load_device, offload_device, size=0, "
        "weight_inplace_update=False):",
        "def __init__(self, model, load_device, offload_device, size=0, "
        "weight_inplace_update=False, fast_disk=False):",
    ),
    (
        "model_patcher/zimage.py",
        "super().__init__(model, load_device, offload_device, size, weight_inplace_update=False)",
        "super().__init__(\n"
        "            model, load_device, offload_device, size, weight_inplace_update=False,"
        " fast_disk=fast_disk\n"
        "        )",
    ),
]


# Upstream ComfyUI changes not yet in a release, as git diffs (patches/). Each is
# applied strictly: the files must be the pinned release's, and come out as the
# change's own versions (see sourcepatch).
#
# PR 16805 (Comfy-Org/ComfyUI, head 22e5e90f): a "solid" remesh that leaves one
# closed shell instead of an outer surface plus an inverted inner copy, so 3D
# models print as solids. Its faster kernels (comfy-kitchen) are optional.
COMFY_PATCHES = ("comfyui-pr16805-remesh.diff",)
PATCH_DIR = Path(__file__).parent / "patches"

# IrisEcho's own nodes, copied into ComfyUI's custom_nodes on every start.
OWN_NODES = {"irisecho_watchdog": "watchdog_node.py", "irisecho_3d": "threed_nodes.py"}


def patch_comfy(src: Path) -> None:
    if not (src / "comfy").is_dir():
        return  # not a ComfyUI tree (a stand-in in the tests): nothing to patch
    for name in COMFY_PATCHES:
        try:
            sourcepatch.apply(src, (PATCH_DIR / name).read_text(encoding="utf-8"))
        except sourcepatch.PatchError as e:
            raise RuntimeError(f"Cannot apply {name} to ComfyUI: {e}") from e


def patch_sources(root: Path, patches: list[tuple[str, str, str]]) -> None:
    for rel, old, new in patches:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise RuntimeError(f"Cannot patch {rel}: the code it fixes has changed.")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


# A cold start is normally well under a minute; the first one after installing is slower.
START_TIMEOUT = 300
CATEGORIES = (
    "diffusion_models",
    "text_encoders",
    "vae",
    "loras",
    "upscale_models",
    "frame_interpolation",
    "clip_vision",
    "latent_upscale_models",
    "background_removal",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def finished_entry(history, wait: float = 60.0, every: float = 0.25) -> dict:
    """A finished job's history entry. ComfyUI announces success before it records
    the job, and after a long video the record can lag by seconds; reading it at
    once found no outputs and failed jobs whose files were already written."""
    entry: dict = {}
    for _ in range(max(1, int(wait / every))):
        entry = await history()
        if entry.get("status"):
            break
        await asyncio.sleep(every)
    return entry


# What ComfyUI still holds once its models are unloaded (about 0.1 GB).
RELEASED_BYTES = 1 << 30


async def until_released(stats, wait: float = 15.0, every: float = 0.25) -> bool:
    """Wait until ComfyUI has given its memory back. /free only asks: its worker
    thread unloads a moment later, and the next engine would start before that."""
    for _ in range(max(1, int(wait / every))):
        devices = (await stats()).get("devices") or []
        if max((d.get("torch_vram_total") or 0 for d in devices), default=0) < RELEASED_BYTES:
            return True
        await asyncio.sleep(every)
    return False


def out_of_memory(e: BaseException) -> bool:
    text = str(e).lower()
    return "out of memory" in text or "would exceed allowed memory" in text


def ui_value(outputs: dict, node: str, key: str) -> float | None:
    """A number an IrisEcho node reported, e.g. the mirror check's score."""
    for item in (outputs.get(node) or {}).get("irisecho") or []:
        if key in item:
            return float(item[key])
    return None


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
        patch_comfy(self.src)
        for name, (repo, sha) in CUSTOM_NODES.items():
            if name == "ComfyUI-nunchaku" and self.hw.backend != "cuda":
                continue
            await self._fetch_source(repo, sha, self.src / "custom_nodes" / name, log)
            if name == "ComfyUI-nunchaku":
                patch_sources(self.src / "custom_nodes" / name, NUNCHAKU_PATCHES)

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
            self.venv,
            packages,
            log,
            *uvenv.torch_backend_args(self.hw.backend, self.hw.cuda_tag),
            constraints=self.constraints,
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

    async def ensure_running(
        self, report, cancelled: asyncio.Event | None = None, dynamic_vram: bool = False
    ) -> None:
        """Start ComfyUI, or restart it when the model folders or the memory mode changed.

        Dynamic VRAM (weights streamed from their files) is for the video workflows
        only: it keeps a 31 GB machine out of swap there, but Nunchaku's Z-Image
        loader reads weights that it has not loaded yet."""
        async with self._start_lock:
            yaml_text = self._model_paths_yaml()
            key = f"{yaml_text}dynamic={dynamic_vram}"
            if self.alive and key == self.paths_key:
                return
            if self.alive:
                await self.stop()
            report(None, "Starting the image engine")
            self.state = "starting"
            for sub in ("input", "output", "temp", "user"):
                (self.io / sub).mkdir(parents=True, exist_ok=True)
            for folder, source in OWN_NODES.items():
                dest = self.src / "custom_nodes" / folder
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(__file__).parent / source, dest / "__init__.py")
            try:
                # Installs from before a patch was added get it here, without a reinstall.
                patch_comfy(self.src)
            except RuntimeError as e:
                self.state = "error"
                raise EngineStartError(str(e)) from e
            cfg = self.home / "extra_model_paths.yaml"
            cfg.write_text(yaml_text, encoding="utf-8")
            self.paths_key = key
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
            if not dynamic_vram:
                args.append("--disable-dynamic-vram")
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "DO_NOT_TRACK": "1",
                    "IRISECHO_PARENT_PID": str(os.getpid()),
                    **hardware.gpu_env(self.hw),
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
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
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
        workflow = ctx.variant.workflow
        if workflow == "views3d":
            return await self._run_views(ctx)
        builder = graphs.BUILDERS.get(workflow)
        if builder is None:
            raise RuntimeError(f"no workflow {workflow!r}")
        prompt = (ctx.params.get("prompt") or "").strip()
        if not prompt and workflow not in graphs.PROMPT_OPTIONAL:
            raise ValueError("Describe what you want first.")
        seed = ctx.params.get("seed")
        if seed is None:
            seed = random.randrange(2**50)
        names = {fid: path.name for fid, path in ctx.files.items()}
        staged = self._stage_inputs(ctx, workflow)

        def build(params: dict) -> tuple[dict, str]:
            return builder(
                prompt=prompt,
                seed=int(seed),
                aspect=params.get("aspect", "1:1"),
                names=names,
                settings=ctx.model.settings,
                hw=self.hw,
                prefix=f"irisecho/{ctx.job_id}",
                images={k: p.name for k, p in staged.items()},
                params=params,
            )

        try:
            graph, save_node = build(ctx.params)
            await self.ensure_running(ctx.report, ctx.cancelled, workflow in graphs.DYNAMIC_VRAM)
            self.state = "busy"
            try:
                if workflow in graphs.MODEL3D:
                    return await self._run_model3d(ctx, build, int(seed))
                found = await self._execute(ctx, graph)
            finally:
                self.state = "ready" if self.alive else "idle"
        finally:
            for p in staged.values():
                p.unlink(missing_ok=True)
        files = self._saved(found, save_node)
        if not files:
            raise RuntimeError("The image engine finished without an image.")
        outputs = []
        for i, src in enumerate(files):
            ext = src.suffix.lstrip(".").lower() or "png"
            dest = ctx.output(i, ext)
            shutil.move(str(src), dest)
            if ext in ("mp4", "webm", "mov"):
                outputs.append({"path": str(dest), "type": "video", "seed": int(seed)})
                continue
            outputs.append(self._image_out(dest, int(seed)))
        ctx.params["seed"] = int(seed)
        return outputs

    async def _run_model3d(self, ctx: RunContext, build, seed: int) -> list[dict]:
        """Build a 3D model. When High detail does not fit in graphics memory, step
        down (upstream lowers the resolution the same way when a shape is too large)."""
        wanted = graphs.DETAIL.get(ctx.params.get("detail", "standard"), 1024)
        tries = list(dict.fromkeys(r for r in (wanted, 1280, 1024) if r <= wanted))
        for n, res in enumerate(tries):
            graph, save_node = build({**ctx.params, "resolution": res})
            try:
                found = await self._execute(ctx, graph, graphs.MODEL3D_PROGRESS)
                break
            except RuntimeError as e:
                if not out_of_memory(e) or n == len(tries) - 1:
                    raise
                ctx.report(0.02, f"Not enough graphics memory at {res}; trying {tries[n + 1]}")
                await self.release()
        model = self._saved(found, save_node)
        if not model:
            raise RuntimeError("The image engine finished without a model.")
        dest = ctx.output(0, "glb")
        shutil.move(str(model[0]), dest)
        out = {"path": str(dest), "type": "model3d", "seed": seed, "resolution": res}
        ctx.report(0.99, "Checking the model")
        try:
            out["mesh"] = await asyncio.to_thread(mesh3d.inspect, dest)
        except Exception as e:  # a model that cannot be measured is still a model
            out["mesh"] = {"error": str(e) or type(e).__name__}
        thumb = self._saved(found, "thumb")
        if thumb:
            preview = ctx.out_dir / f"{ctx.stem}.preview.png"
            shutil.move(str(thumb[0]), preview)
            out["preview"] = str(preview)
        ctx.params["seed"] = seed
        return [out]

    async def _run_views(self, ctx: RunContext) -> list[dict]:
        """Front and back views for Pixal3D, made from one picture with Qwen Edit.

        level "auto": if an eye-level edit barely changes the picture, it already is
        level and becomes the front; otherwise the eye-level version comes back on
        its own (role "level") for the person to choose. "best" goes on with the
        eye-level version in that case; "keep" uses the picture as the front as it is,
        without checking. The back is drawn up to three times, until it passes the
        mirror check."""
        mode = ctx.params.get("level", "auto")
        if mode not in ("auto", "best", "keep"):
            raise ValueError("level must be auto, best or keep.")
        seed = ctx.params.get("seed")
        seed = int(seed) if seed is not None else random.randrange(2**50)
        names = {fid: path.name for fid, path in ctx.files.items()}
        staged = self._stage_inputs(ctx, "views3d")
        if "image1" not in staged:
            raise ValueError("Add a picture of the object.")
        made: Path | None = None
        outputs: list[dict] = []
        try:
            await self.ensure_running(ctx.report, ctx.cancelled, True)
            self.state = "busy"
            front, source, similarity = staged["image1"].name, "yours", None
            if mode in ("auto", "best"):
                graph = graphs.views_level(
                    names=names, image=front, seed=seed, prefix=f"irisecho/{ctx.job_id}-level"
                )
                found = await self._execute(
                    ctx, graph, {"sample": (0.05, 0.3, "Checking the camera angle")}
                )
                level_file = self._saved(found, "save")[0]
                similarity = ui_value(found, "same", "similarity")
                angled = similarity is not None and similarity < graphs.LEVEL_SAME
                if mode == "auto" and angled:
                    dest = ctx.output(0, "png")
                    shutil.move(str(level_file), dest)
                    ctx.params["seed"] = seed
                    return [self._image_out(dest, seed, role="level", similarity=similarity)]
                if angled:
                    made = self.io / "input" / f"irisecho_{ctx.job_id}_level.png"
                    shutil.move(str(level_file), made)
                    front, source = made.name, "made"
                else:
                    level_file.unlink(missing_ok=True)
            best: tuple[float, list[Path]] | None = None
            for attempt in range(3):
                lo = 0.3 + attempt * 0.23
                graph = graphs.views_back(
                    names=names,
                    image=front,
                    attempt=attempt,
                    seed=seed + attempt,
                    prefix=f"irisecho/{ctx.job_id}-{attempt}",
                )
                found = await self._execute(
                    ctx, graph, {"sample": (lo, lo + 0.2, "Drawing the back")}
                )
                score = ui_value(found, "check", "mirror_iou") or 0.0
                files = self._saved(found, "save_front") + self._saved(found, "save_back")
                if best is None or score > best[0]:
                    for f in best[1] if best else []:
                        f.unlink(missing_ok=True)
                    best = (score, files)
                else:
                    for f in files:
                        f.unlink(missing_ok=True)
                if score >= graphs.MIRROR_OK:
                    break
                ctx.report(None, "The back did not match the front; drawing it again")
            assert best is not None
            score, (front_file, back_file) = best
            for i, (src, role) in enumerate(((front_file, "front"), (back_file, "back"))):
                dest = ctx.output(i, "png")
                shutil.move(str(src), dest)
                extra: dict = {"source": source} if role == "front" else {}
                if role == "back":
                    extra |= {"mirror_iou": score, "matched": score >= graphs.MIRROR_OK}
                if similarity is not None:
                    extra["similarity"] = similarity
                outputs.append(self._image_out(dest, seed, role=role, **extra))
        finally:
            self.state = "ready" if self.alive else "idle"
            for p in staged.values():
                p.unlink(missing_ok=True)
            if made:
                made.unlink(missing_ok=True)
        ctx.params["seed"] = seed
        return outputs

    @staticmethod
    def _image_out(path: Path, seed: int, **extra) -> dict:
        size = png_size(path)
        return {
            "path": str(path),
            "type": "image",
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "seed": seed,
            **extra,
        }

    def _saved(self, outputs: dict, node: str) -> list[Path]:
        """Files a save node wrote: pictures and clips under "images", models under "3d"."""
        found = []
        for key in ("images", "3d"):
            for item in (outputs.get(node) or {}).get(key) or []:
                base = self.io / item.get("type", "output")
                found.append(base / item.get("subfolder", "") / item["filename"])
        return found

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

    async def _execute(self, ctx: RunContext, graph: dict, plan: dict | None = None) -> dict:
        """Run one graph; returns its history outputs (node id -> what it saved or reported).

        plan maps node ids to (start, end, message) for progress; without one, the
        sampler's steps drive it."""
        import websockets

        client_id = uuid.uuid4().hex
        labels = graphs.stage_labels(graph)
        first_load = True
        reached = 0.0  # a plan's progress never goes back, whatever order nodes run in

        def advance(value: float, message: str) -> None:
            nonlocal reached
            reached = max(reached, value)
            ctx.report(reached, message)

        async with websockets.connect(
            f"ws://127.0.0.1:{self.port}/ws?clientId={client_id}", max_size=None
        ) as ws:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
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
                            if plan is not None and node in plan:
                                advance(plan[node][0], plan[node][2])
                            elif label == "load" and first_load:
                                ctx.report(None if plan else 0.03, f"Loading {ctx.model.name}")
                                first_load = False
                            elif plan is None and label == "sample":
                                ctx.report(0.1, "Drawing")
                            elif plan is None and label == "decode":
                                ctx.report(0.95, "Finishing")
                        elif kind == "progress":
                            node = str(d.get("node"))
                            value, total = d.get("value", 0), max(1, d.get("max", 1))
                            if plan is not None and node in plan:
                                lo, hi, message = plan[node]
                                advance(lo + (hi - lo) * value / total, message)
                            elif plan is None and labels.get(node) == "sample":
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

                    async def history() -> dict:
                        r = await client.get(self.url(f"/history/{prompt_id}"))
                        return r.json().get(prompt_id) or {}

                    entry = await finished_entry(history)
                    status = entry.get("status") or {}
                    if status.get("status_str") == "error":
                        raise RuntimeError(graphs.history_error(status))
                    # Leave nothing behind in ComfyUI's own history.
                    await client.post(self.url("/history"), json={"delete": [prompt_id]})
                    return entry.get("outputs") or {}
                finally:
                    self.prompt_of.pop(ctx.job_id, None)

    async def cancel(self, job_id: str) -> None:
        prompt_id = self.prompt_of.get(job_id)
        if not prompt_id or not self.alive:
            return
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            await client.post(self.url("/queue"), json={"delete": [prompt_id]})
            await client.post(self.url("/interrupt"), json={"prompt_id": prompt_id})

    async def release(self) -> None:
        """Unload models so another engine can use the GPU; the process stays up."""
        if not self.alive:
            return
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                await client.post(
                    self.url("/free"), json={"unload_models": True, "free_memory": True}
                )

                async def stats() -> dict:
                    return (await client.get(self.url("/system_stats"))).json()

                await until_released(stats)
        except httpx.HTTPError:
            await self.stop()

    async def shutdown(self) -> None:
        await self.stop()
