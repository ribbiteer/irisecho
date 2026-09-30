# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chatterbox worker. Runs inside the Chatterbox engine's own environment."""

from __future__ import annotations

import random

import soundfile as sf
import torch
from irisecho_audio import normalize, pitch_range, trim
from irisecho_worker import Reporter, serve

state: dict = {"model": None, "key": None}


def load(params: dict, report: Reporter) -> dict:
    device = params["device"]
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    key = (params["variant"], params["dir"], device)
    if state["key"] == key:
        return {}
    report.progress(None, "Loading Chatterbox")
    state.update(model=None, key=None)
    if params["variant"] == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
    else:
        from chatterbox.tts import ChatterboxTTS as Model
    state.update(model=Model.from_local(params["dir"], device), key=key)
    return {}


def speak(params: dict, report: Reporter) -> dict:
    model = state["model"]
    if model is None:
        raise RuntimeError("load was not called")
    outs = params["outs"]
    seed = params.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    takes = []
    for i, out in enumerate(outs):
        report.progress(
            i / len(outs),
            f"Speaking · take {i + 1} of {len(outs)}" if len(outs) > 1 else "Speaking",
        )
        torch.manual_seed(int(seed) + i)
        kwargs = {"audio_prompt_path": params["ref"]}
        if params["variant"] != "turbo":
            kwargs.update(exaggeration=params["exaggeration"], cfg_weight=params["cfg"])
        wav = model.generate(params["text"], **kwargs)
        audio = wav.detach().float().cpu().numpy().reshape(-1)
        sr = int(model.sr)
        if params.get("clean", True):
            audio = normalize(trim(audio, sr))
        sf.write(out, audio, sr, subtype="PCM_16")
        takes.append(
            {
                "path": out,
                "duration": round(len(audio) / sr, 3),
                "seed": int(seed) + i,
                "liveliness": round(pitch_range(audio, sr), 2),
            }
        )
    # Best take first: the liveliest delivery (widest pitch range) leads.
    takes.sort(key=lambda t: -t["liveliness"])
    report.progress(1.0, "Done")
    return {"takes": takes}


if __name__ == "__main__":
    serve({"load": load, "speak": speak})
