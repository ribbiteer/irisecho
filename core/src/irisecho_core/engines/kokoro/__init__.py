# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kokoro: fast narration in preset voices."""

from __future__ import annotations

import math
from pathlib import Path

from irisecho_core.engines import audioformat, uvenv
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


MAX_BLEND = 5
# A voice may be named by its id (af_bella) or the name shown in the list (Bella).
BY_NAME = {**{vid: vid for vid in VOICES}, **{label.lower(): vid for vid, label in VOICES.items()}}


def parse_blend(text: str) -> list[tuple[str, float]]:
    """A voice mix such as "Bella:0.6, Sky:0.4" or "af_bella,af_sky" (equal parts).

    Returns (voice, weight) pairs; a voice named twice has its weights added.
    """
    mix: dict[str, float] = {}
    for item in str(text).split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, weight = item.partition(":")
        name = BY_NAME.get(name.strip().lower(), "")
        if not name:
            raise ValueError(f"Voice mix: unknown voice {item.partition(':')[0].strip()!r}.")
        w = 1.0
        if sep:
            try:
                w = float(weight)
            except ValueError:
                raise ValueError(f"Voice mix: {weight.strip()!r} is not a weight.") from None
        if not math.isfinite(w) or w <= 0:
            raise ValueError(f"Voice mix: the weight of {name} must be above zero.")
        mix[name] = mix.get(name, 0.0) + w
    if not mix:
        raise ValueError("Voice mix: name at least one voice.")
    if len(mix) > MAX_BLEND:
        raise ValueError(f"Voice mix: use at most {MAX_BLEND} voices.")
    return list(mix.items())


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
        blend = (p.get("blend") or "").strip()
        mix = parse_blend(blend) if blend else None
        # A mix replaces the single voice; the first voice in it sets the language.
        voice = mix[0][0] if mix else p.get("voice") or "af_heart"
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
        _, ext, fmt = audioformat.pick(p)
        out = ctx.output(0, ext)
        request = {
            "text": text,
            "voice": str(ctx.files[f"kokoro-voices/{voice}.pt"]),
            "lang": voice[0],
            "speed": float(p.get("speed") or 1.0),
            "clean": bool(clean),
            "out": str(out),
            **fmt,
        }
        if mix:
            del request["voice"]
            request["voices"] = [
                [str(ctx.files[f"kokoro-voices/{name}.pt"]), weight] for name, weight in mix
            ]
        result = await self.call(ctx, "speak", request)
        return [{"path": str(out), "type": "audio", "duration": result.get("duration")}]
