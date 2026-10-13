# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable Audio 3 worker. Runs inside the Stable Audio engine's own environment."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from irisecho_audio import trim
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


def generate(params: dict, report: Reporter) -> dict:
    model = state["model"]
    if model is None:
        raise RuntimeError("load was not called")
    outs = params["outs"]
    seed = params.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    sr = state["sr"]
    takes = []
    # One take per seed, so a take's seed makes that same sound again.
    for i, out in enumerate(outs):
        report.progress(
            0.05 + 0.9 * i / len(outs),
            f"Generating · take {i + 1} of {len(outs)}" if len(outs) > 1 else "Generating",
        )
        audio = model.generate(
            prompt=params["prompt"],
            duration=float(params["seconds"]),
            batch_size=1,
            seed=int(seed) + i,
        )
        clip = audio.detach().float().cpu().numpy().reshape(-1, audio.shape[-1]).T
        if params.get("trim", True):
            # Only the silence the model pads short sounds with; decays and room tone stay.
            clip = trim(clip, sr, below_peak_db=60.0, floor_db=-75.0, pad_s=0.05)
        # Loudness stays as generated: a pin drop should not come out as loud
        # as an explosion. Only keep peaks from clipping.
        peak = float(np.max(np.abs(clip))) if clip.size else 0.0
        if peak > 0.99:
            clip = clip * (0.99 / peak)
        sf.write(out, clip, sr, subtype="PCM_16")
        takes.append({"path": out, "duration": round(len(clip) / sr, 3), "seed": int(seed) + i})
    report.progress(1.0, "Done")
    return {"takes": takes}


if __name__ == "__main__":
    serve({"load": load, "generate": generate})
