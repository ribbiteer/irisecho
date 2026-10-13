# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable Audio 3 Small-SFX: one-shot sound effects."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import httpx

from irisecho_core.engines import audioformat, audiosource, uvenv
from irisecho_core.engines.acestep import group_root
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.worker import WorkerEngine

SOURCE = ("Stability-AI/stable-audio-3", "3a82c807b69cf4b7c5c05270011a5d5e47abac18")
TORCH = ["torch==2.7.1", "torchaudio==2.7.1"]
MAX_SECONDS = 30.0  # the longest sound the studio makes


def source_options(model, p: dict) -> dict:
    """What a job that starts from a sound adds to the request ({} for a plain job).

    `init_audio` alone makes a variation of the sound (`init_strength` says how far it
    may move). With `region` as well, only that part of the sound is made again and the
    rest stays as it was.
    """
    source = p.get("init_audio")
    if not source:
        if p.get("region") is not None:
            raise ValueError("Add the sound to start from first.")
        return {}
    out: dict = {"init_audio": str(audiosource.upload_path(source, "sound"))}
    if p.get("region") is not None:
        out["region"] = list(audiosource.region(p["region"], longest=MAX_SECONDS))
    else:
        c = model.control("init_strength")
        strength = float(p["init_strength"]) if p.get("init_strength") is not None else c.default
        out["init_strength"] = max(float(c.min), min(float(c.max), strength))
    return out


class StableAudioEngine(WorkerEngine):
    id = "stable-audio"
    name = "Stable Audio"
    install_version = 1
    worker_script = Path(__file__).parent / "worker.py"

    def __init__(self, hardware):
        super().__init__(hardware)
        self.src = self.home / "src"

    def supported(self) -> bool:
        return self.hw.backend in ("cuda", "mps")

    def install_key(self) -> str:
        return f"{super().install_key()}:{SOURCE[1][:12]}"

    def worker_env(self) -> dict[str, str]:
        return {"PYTHONPATH": str(self.src), "TOKENIZERS_PARALLELISM": "false"}

    async def install(self, log) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        log(f"Downloading {SOURCE[0]} @ {SOURCE[1][:10]}")
        async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
            r = await client.get(f"https://codeload.github.com/{SOURCE[0]}/zip/{SOURCE[1]}")
            r.raise_for_status()
        if self.src.exists():
            shutil.rmtree(self.src)
        tmp = self.home / "src.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(tmp)
        (inner,) = list(tmp.iterdir())
        inner.rename(self.src)
        tmp.rmdir()

        if self.venv.exists():
            shutil.rmtree(self.venv)
        env = uvenv.uv_env()
        env["UV_PROJECT_ENVIRONMENT"] = str(self.venv)
        sync = [uvenv.uv_bin(), "sync", "--frozen", "--no-dev"]
        if self.hw.backend == "cuda":
            # The project's lock resolves Windows torch to a CPU build; keep its
            # version but take the CUDA build that suits this machine.
            sync += ["--no-install-package", "torch", "--no-install-package", "torchaudio"]
        log("Installing Stable Audio 3 and its pinned dependencies")
        await uvenv.run(sync, log, cwd=self.src, env=env)
        if self.hw.backend == "cuda":
            await uvenv.pip_install(self.venv, TORCH, log, "--torch-backend", "cu128")
        self.mark_installed()

    def device(self) -> str:
        return {"cuda": "cuda", "mps": "mps"}.get(self.hw.backend, "cpu")

    @staticmethod
    def bounded(ctx: RunContext, name: str, value, kind):
        value = kind(value)
        c = ctx.model.control(name)
        if c is not None and c.min is not None and c.max is not None:
            value = max(kind(c.min), min(kind(c.max), value))
        return value

    async def run(self, ctx: RunContext) -> list[dict]:
        p = ctx.params
        prompt = (p.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Describe the sound.")
        takes = max(1, min(4, int(p.get("takes") or 3)))
        seconds = max(0.5, min(30.0, float(p.get("duration") or 3)))
        root = group_root(ctx, "sa3-sfx") / "small-sfx"
        _, ext, fmt = audioformat.pick(p)
        await self.call(ctx, "load", {"root": str(root), "device": self.device()})
        request = {
            # Stability's guide: this tag, leading the prompt the way their
            # prompt assistant writes it, steers toward clean, sensible effects.
            # `track_type: false` leaves it off.
            "prompt": f"TrackType: SFX, {prompt}" if p.get("track_type") is not False else prompt,
            "seconds": seconds,
            "seed": p.get("seed"),
            "outs": [str(ctx.output(i, ext)) for i in range(takes)],
            "trim": p.get("trim", True),
            **fmt,
        }
        if p.get("steps") is not None:
            request["steps"] = self.bounded(ctx, "steps", p["steps"], int)
        if p.get("cfg") is not None:
            request["cfg"] = self.bounded(ctx, "cfg", p["cfg"], float)
        negative = (p.get("negative") or "").strip()
        if negative:
            request["negative"] = negative
        request.update(source_options(ctx.model, p))
        result = await self.call(ctx, "generate", request)
        return [
            {"path": t["path"], "type": "audio", "duration": t["duration"], "seed": t["seed"]}
            for t in result["takes"]
        ]
