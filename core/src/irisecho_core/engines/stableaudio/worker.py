# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable Audio 3 worker. Runs inside the Stable Audio engine's own environment."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from irisecho_worker import Reporter, serve

state: dict = {"model": None, "key": None, "sr": 44100}


def _point_text_encoder_at(config, local: str) -> None:
    """Make the T5Gemma conditioner load from IrisEcho's verified local copy."""
    if isinstance(config, dict):
        if config.get("type") == "t5gemma" and isinstance(config.get("config"), dict):
            inner = config["config"]
            inner["model_path"] = local
            inner.pop("repo_id", None)
            inner.pop("subfolder", None)
        for value in config.values():
            _point_text_encoder_at(value, local)
    elif isinstance(config, list):
        for value in config:
            _point_text_encoder_at(value, local)


def load(params: dict, report: Reporter) -> dict:
    key = (params["root"], params["device"])
    if state["key"] == key:
        return {}
    report.progress(None, "Loading Stable Audio")
    from stable_audio_3 import StableAudioModel
    from stable_audio_3.loading_utils import load_diffusion_cond

    root = Path(params["root"])
    config = json.loads((root / "model_config.json").read_text(encoding="utf-8"))
    _point_text_encoder_at(config, str(root / "t5gemma-b-b-ul2"))
    device = params["device"]
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    half = device == "cuda"
    model = load_diffusion_cond(
        config, str(root / "model.safetensors"), device=device, model_half=half
    )
    model.use_lora = False
    model.lora_names = []
    state.update(
        model=StableAudioModel(model, config, device, half),
        key=key,
        sr=int(config.get("sample_rate", 44100)),
    )
    return {}


def trim(audio: np.ndarray, sr: int, db: float = -50.0, pad: float = 0.03) -> np.ndarray:
    """Cut the silence the model pads short sounds with, keeping a tiny margin."""
    level = np.max(np.abs(audio), axis=1)
    loud = np.flatnonzero(level > 10 ** (db / 20))
    if loud.size == 0:
        return audio
    p = int(pad * sr)
    return audio[max(0, loud[0] - p) : loud[-1] + p + 1]


def generate(params: dict, report: Reporter) -> dict:
    model = state["model"]
    if model is None:
        raise RuntimeError("load was not called")
    outs = params["outs"]
    seed = params.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    report.progress(0.1, "Generating")
    audio = model.generate(
        prompt=params["prompt"],
        duration=float(params["seconds"]),
        batch_size=len(outs),
        seed=int(seed),
    )
    batch = audio.detach().float().cpu().numpy()
    if batch.ndim == 2:
        batch = batch[None]
    sr = state["sr"]
    takes = []
    for i, out in enumerate(outs[: len(batch)]):
        clip = batch[i].T  # [samples, channels]
        if params.get("trim", True):
            clip = trim(clip, sr)
        peak = float(np.max(np.abs(clip))) if clip.size else 0.0
        if peak > 0:
            clip = clip * (10 ** (-1 / 20) / peak)  # peak at -1 dBFS
        sf.write(out, clip, sr, subtype="PCM_16")
        takes.append({"path": out, "duration": round(len(clip) / sr, 3), "seed": int(seed) + i})
    report.progress(1.0, "Done")
    return {"takes": takes}


if __name__ == "__main__":
    serve({"load": load, "generate": generate})
