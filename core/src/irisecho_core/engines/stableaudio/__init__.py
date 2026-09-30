# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable Audio 3 Small-SFX: one-shot sound effects."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import httpx

from irisecho_core.engines import uvenv
from irisecho_core.engines.acestep import group_root
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.worker import WorkerEngine

SOURCE = ("Stability-AI/stable-audio-3", "3a82c807b69cf4b7c5c05270011a5d5e47abac18")
TORCH = ["torch==2.7.1", "torchaudio==2.7.1"]


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

    async def run(self, ctx: RunContext) -> list[dict]:
        p = ctx.params
        prompt = (p.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Describe the sound.")
        takes = max(1, min(4, int(p.get("takes") or 3)))
        seconds = max(0.5, min(30.0, float(p.get("duration") or 3)))
        root = group_root(ctx, "sa3-sfx") / "small-sfx"
        await self.call(ctx, "load", {"root": str(root), "device": self.device()})
        result = await self.call(
            ctx,
            "generate",
            {
                # Stability's guide: this tag steers toward clean, sensible effects.
                "prompt": f"{prompt.rstrip('. ')}. TrackType: SFX",
                "seconds": seconds,
                "seed": p.get("seed"),
                "outs": [str(ctx.output(i, "wav")) for i in range(takes)],
                "trim": p.get("trim", True),
            },
        )
        return [
            {"path": t["path"], "type": "audio", "duration": t["duration"], "seed": t["seed"]}
            for t in result["takes"]
        ]
