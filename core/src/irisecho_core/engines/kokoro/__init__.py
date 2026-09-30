# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kokoro: fast narration in preset voices."""

from __future__ import annotations

from pathlib import Path

from irisecho_core.engines import uvenv
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.worker import WorkerEngine

SPACY_MODEL = (
    "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)

# Voice id prefix: a = American English, b = British English; f/m = female/male.
VOICES = {
    "af_heart": "Heart",
    "af_bella": "Bella",
    "af_nicole": "Nicole",
    "af_aoede": "Aoede",
    "af_kore": "Kore",
    "af_sarah": "Sarah",
    "af_nova": "Nova",
    "af_sky": "Sky",
    "af_alloy": "Alloy",
    "af_jessica": "Jessica",
    "af_river": "River",
    "am_michael": "Michael",
    "am_fenrir": "Fenrir",
    "am_puck": "Puck",
    "am_echo": "Echo",
    "am_eric": "Eric",
    "am_liam": "Liam",
    "am_onyx": "Onyx",
    "am_adam": "Adam",
    "am_santa": "Santa",
    "bf_emma": "Emma",
    "bf_isabella": "Isabella",
    "bf_alice": "Alice",
    "bf_lily": "Lily",
    "bm_george": "George",
    "bm_fable": "Fable",
    "bm_lewis": "Lewis",
    "bm_daniel": "Daniel",
}


def voice_options() -> list[dict]:
    accent = {"a": "American", "b": "British"}
    gender = {"f": "female", "m": "male"}
    return [
        {"id": vid, "label": label, "group": f"{accent[vid[0]]} {gender[vid[1]]}"}
        for vid, label in VOICES.items()
    ]


class KokoroEngine(WorkerEngine):
    id = "kokoro"
    name = "Kokoro"
    python_version = "3.12"
    install_version = 1
    worker_script = Path(__file__).parent / "worker.py"

    async def install(self, log) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        await uvenv.create_venv(self.venv, self.python_version, log)
        await uvenv.pip_install(
            self.venv,
            [
                "torch==2.11.0",
                "kokoro==0.9.4",
                "misaki[en]==0.9.4",
                "soundfile>=0.12",
                "numpy",
                SPACY_MODEL,
            ],
            log,
            *uvenv.torch_backend_args(self.hw.backend, self.hw.cuda_tag),
            constraints=self.constraints,
        )
        self.mark_installed()

    def device(self) -> str:
        return {"cuda": "cuda", "mps": "mps"}.get(self.hw.backend, "cpu")

    async def run(self, ctx: RunContext) -> list[dict]:
        p = ctx.params
        voice = p.get("voice") or "af_heart"
        if voice not in VOICES:
            raise ValueError(f"unknown voice {voice}")
        text = (p.get("text") or "").strip()
        if not text:
            raise ValueError("Write something for the voice to say.")
        await self.call(
            ctx,
            "load",
            {
                "config": str(ctx.files["kokoro-config"]),
                "model": str(ctx.files["kokoro-model"]),
                "device": self.device(),
            },
        )
        clean = p.get("clean", True)
        out = ctx.output(0, "wav")
        result = await self.call(
            ctx,
            "speak",
            {
                "text": text,
                "voice": str(ctx.files[f"kokoro-voices/{voice}.pt"]),
                "lang": voice[0],
                "speed": float(p.get("speed") or 1.0),
                "trim_db": -42.0 if clean else None,
                "rms_db": -18.0 if clean else None,
                "out": str(out),
            },
        )
        return [{"path": str(out), "type": "audio", "duration": result.get("duration")}]
