# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prompt writer: Qwen3-VL-4B-Instruct rewrites or invents prompts.

Runs on transformers in its own environment (not as a ComfyUI node) and uses
beam search, so the same draft gives the same rewrite every time.
"""

from __future__ import annotations

from pathlib import Path

from irisecho_core import paths, registry
from irisecho_core.engines import uvenv
from irisecho_core.engines.acestep import group_root
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.promptwriter.styles import (
    MODES,
    STYLES,
    clean,
    system_prompt,
    user_prompt,
)
from irisecho_core.engines.worker import WorkerEngine

DEPS = [
    "torch==2.11.0",
    "torchvision==0.26.0",
    "transformers==5.2.0",
    "accelerate>=1.0",
    "pillow",
    "numpy",
]
BEAMS = 4


def style_for(model_id: str, settings: dict) -> str:
    style = settings.get("prompt_style")
    if style not in STYLES:
        raise ValueError(f"No prompt style is defined for {model_id}.")
    return style


class PromptWriterEngine(WorkerEngine):
    id = "prompt-writer"
    name = "Prompt Writer"
    python_version = "3.12"
    install_version = 1
    worker_script = Path(__file__).parent / "worker.py"

    def supported(self) -> bool:
        return self.hw.backend in ("cuda", "mps")

    async def install(self, log) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        await uvenv.create_venv(self.venv, self.python_version, log)
        await uvenv.pip_install(
            self.venv, DEPS, log, *uvenv.torch_backend_args(self.hw.backend, self.hw.cuda_tag)
        )
        self.mark_installed()

    def device(self) -> str:
        return {"cuda": "cuda", "mps": "mps"}.get(self.hw.backend, "cpu")

    async def run(self, ctx: RunContext) -> list[dict]:
        p = ctx.params
        mode = p.get("mode") or "improve"
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode}")
        text = (p.get("text") or "").strip()
        images = [n for n in p.get("images") or [] if n]
        if mode == "improve" and not text:
            raise ValueError("Write a rough idea first, then improve it.")
        if mode in ("describe", "improve_image") and not images:
            raise ValueError("Add a picture first.")
        if mode == "improve_image" and not text:
            raise ValueError("Write a rough idea to improve, or use Describe for a picture.")
        target = p.get("target") or ""
        model = registry.default().models.get(target)
        if model is None:
            raise ValueError(f"unknown target model {target}")
        style = STYLES[style_for(target, model.settings)]
        image_paths = []
        for name in images[:3]:
            path = paths.sub("uploads") / Path(str(name)).name
            if not path.is_file():
                raise ValueError("That picture is no longer available; add it again.")
            image_paths.append(str(path))
        if mode == "improve" and image_paths:
            image_paths = []
        folder = group_root(ctx, "qwen3-vl-4b") / "Qwen3-VL-4B-Instruct"
        await self.call(ctx, "load", {"dir": str(folder), "device": self.device()})
        result = await self.call(
            ctx,
            "write",
            {
                "system": system_prompt(style, mode, bool(image_paths)),
                "user": user_prompt(style, mode, text),
                "images": image_paths,
                "beams": BEAMS,
                "max_new_tokens": 300,
            },
        )
        prompt = clean(result["text"])
        if not prompt:
            raise RuntimeError("The prompt writer returned nothing. Try again.")
        return [{"type": "text", "text": prompt, "mode": mode, "style": style.id}]
