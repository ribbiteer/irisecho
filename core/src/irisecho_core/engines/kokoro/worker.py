# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kokoro worker. Runs inside the Kokoro engine's own environment."""

from __future__ import annotations

import numpy as np
import soundfile as sf
import torch
from irisecho_audio import normalize, trim
from irisecho_worker import Reporter, serve
from kokoro import KModel, KPipeline

SAMPLE_RATE = 24000
REPO = "hexgrad/Kokoro-82M"

state: dict = {"model": None, "key": None, "pipelines": {}}


def load(params: dict, report: Reporter) -> dict:
    key = (params["model"], params["device"])
    if state["key"] != key:
        report.progress(None, "Loading Kokoro")
        device = params["device"]
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        model = KModel(repo_id=REPO, config=params["config"], model=params["model"])
        state.update(model=model.to(device).eval(), key=key, pipelines={}, device=device)
    return {"device": state["device"]}


def pipeline(lang: str) -> KPipeline:
    if lang not in state["pipelines"]:
        state["pipelines"][lang] = KPipeline(lang_code=lang, repo_id=REPO, model=state["model"])
    return state["pipelines"][lang]


def speak(params: dict, report: Reporter) -> dict:
    if state["model"] is None:
        raise RuntimeError("load was not called")
    pipe = pipeline(params["lang"])
    chunks = []
    report.progress(0.05, "Speaking")
    for result in pipe(params["text"], voice=params["voice"], speed=params.get("speed", 1.0)):
        if result.audio is not None:
            chunks.append(result.audio.detach().cpu().numpy())
    if not chunks:
        raise RuntimeError("Nothing to say: the text had no speakable words.")
    audio = np.concatenate(chunks).astype(np.float32)
    if params.get("trim_db") is not None:
        audio = trim(audio, SAMPLE_RATE, params["trim_db"])
    if params.get("rms_db") is not None:
        audio = normalize(audio, params["rms_db"])
    sf.write(params["out"], audio, SAMPLE_RATE, subtype="PCM_16")
    report.progress(1.0, "Done")
    return {"duration": round(len(audio) / SAMPLE_RATE, 3), "sample_rate": SAMPLE_RATE}


if __name__ == "__main__":
    serve({"load": load, "speak": speak})
