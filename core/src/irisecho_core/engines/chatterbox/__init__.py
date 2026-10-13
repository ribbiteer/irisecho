# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chatterbox: a voice from a short reference clip.

Chatterbox's package pins an old torch that cannot run on RTX 50 cards, so it
is installed without its pins and given the same dependency set on a current
torch. Every clip it makes carries Resemble AI's inaudible Perth watermark;
IrisEcho keeps it.
"""

from __future__ import annotations

import re
from pathlib import Path

from irisecho_core import paths
from irisecho_core.engines import audioformat, uvenv
from irisecho_core.engines.acestep import group_root
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.worker import WorkerEngine

DEPS = [
    "torch==2.11.0",
    "torchaudio==2.11.0",
    "numpy>=1.24,<2",
    "librosa==0.11.0",
    "s3tokenizer",
    "transformers==5.2.0",
    "diffusers==0.29.0",
    "resemble-perth>=1.0.0",
    "conformer==0.3.2",
    "safetensors==0.5.3",
    "spacy-pkuseg",
    "pykakasi==2.3.0",
    "pyloudnorm",
    "omegaconf",
    "soundfile>=0.12",
]
TAGS = [
    "[laugh]",
    "[chuckle]",
    "[sigh]",
    "[gasp]",
    "[groan]",
    "[cough]",
    "[sniff]",
    "[shush]",
    "[clear throat]",
]


def upload_path(name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{2,5}", name or ""):
        raise ValueError("Add a voice sample first.")
    path = paths.sub("uploads") / name
    if not path.is_file():
        raise ValueError("That voice sample is no longer available; add it again.")
    return path


# What each variant's generate() takes besides the text and the voice (chatterbox
# tts.py:208-218 classic, tts_turbo.py:248-260 turbo). Turbo ignores min_p, classic has no top_k.
SAMPLING = {
    "classic": ("temperature", "top_p", "min_p", "repetition_penalty"),
    "turbo": ("temperature", "top_p", "top_k", "repetition_penalty"),
}


def sampling_options(model, variant: str, p: dict) -> dict:
    """The sampling values a job set, held to the ranges the registry lists for the model."""
    out: dict = {}
    for name in SAMPLING["turbo" if variant == "turbo" else "classic"]:
        value = p.get(name)
        if value is None:
            continue
        c = model.control(name)
        value = int(value) if name == "top_k" else float(value)
        if c is not None and c.min is not None and c.max is not None:
            value = max(c.min, min(c.max, value))
        out[name] = value
    return out


class ChatterboxEngine(WorkerEngine):
    id = "chatterbox"
    name = "Chatterbox"
    python_version = "3.12"
    install_version = 1
    worker_script = Path(__file__).parent / "worker.py"

    def supported(self) -> bool:
        return self.hw.backend in ("cuda", "mps")

    async def install(self, log) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        await uvenv.create_venv(self.venv, self.python_version, log)
        await uvenv.pip_install(
            self.venv,
            DEPS,
            log,
            *uvenv.torch_backend_args(self.hw.backend, self.hw.cuda_tag),
            constraints=self.constraints,
        )
        await uvenv.pip_install(self.venv, ["chatterbox-tts==0.1.7"], log, "--no-deps")
        self.mark_installed()

    def device(self) -> str:
        return {"cuda": "cuda", "mps": "mps"}.get(self.hw.backend, "cpu")

    async def run(self, ctx: RunContext) -> list[dict]:
        p = ctx.params
        text = (p.get("text") or "").strip()
        if not text:
            raise ValueError("Write something for the voice to say.")
        if not p.get("consent"):
            raise ValueError("Confirm you have permission to use this voice.")
        ref = upload_path(p.get("ref", ""))
        variant = ctx.model.settings.get("variant", "classic")
        group = "chatterbox-turbo" if variant == "turbo" else "chatterbox"
        folder = group_root(ctx, group) / ("turbo" if variant == "turbo" else "classic")
        takes = max(1, min(4, int(p.get("takes") or 1)))
        _, ext, fmt = audioformat.pick(p)
        await self.call(
            ctx, "load", {"variant": variant, "dir": str(folder), "device": self.device()}
        )
        request = {
            "text": text,
            "ref": str(ref),
            "variant": variant,
            "exaggeration": float(p.get("exaggeration", 0.5)),
            "cfg": float(p.get("cfg", 0.5)),
            "seed": p.get("seed"),
            "clean": bool(p.get("clean", True)),
            "outs": [str(ctx.output(i, ext)) for i in range(takes)],
            **fmt,
        }
        sampling = sampling_options(ctx.model, variant, p)
        if sampling:
            request["sampling"] = sampling
        result = await self.call(ctx, "speak", request)
        return [
            {
                "path": t["path"],
                "type": "audio",
                "duration": t["duration"],
                "seed": t["seed"],
            }
            for t in result["takes"]
        ]
