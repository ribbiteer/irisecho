# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable Audio 3 worker. Runs inside the Stable Audio engine's own environment."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import torch
from irisecho_audio import trim, write_audio
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


def starting_sound(params: dict, seconds: float) -> tuple[dict, float]:
    """generate() arguments for a job that starts from a sound, and the length to make.

    A variation passes `init_audio` and `init_noise_level` (model.py:96-97; 1.0 ignores the
    sound, lower stays closer to it). A region passes the sound as `inpaint_audio` with
    `inpaint_mask_start_seconds` / `inpaint_mask_end_seconds` (model.py:98-101), which
    keeps everything outside the region. The sound is a numpy float32 [samples, channels]
    array, which the model converts and resamples itself (model.py:438-453). The length is
    never shorter than the sound (or than the end of the region), so nothing is cut off.
    """
    if not params.get("init_audio"):
        return {}, seconds
    import soundfile as sf

    data, in_sr = sf.read(params["init_audio"], dtype="float32", always_2d=True)
    seconds = max(seconds, len(data) / in_sr)
    if params.get("region"):
        begin, end = (float(v) for v in params["region"])
        seconds = max(seconds, end)
        args = {
            "inpaint_audio": (int(in_sr), data),
            "inpaint_mask_start_seconds": begin,
            "inpaint_mask_end_seconds": end,
        }
    else:
        args = {
            "init_audio": (int(in_sr), data),
            "init_noise_level": float(params["init_strength"]),
        }
    return args, min(30.0, seconds)


def generate(params: dict, report: Reporter) -> dict:
    model = state["model"]
    if model is None:
        raise RuntimeError("load was not called")
    outs = params["outs"]
    seed = params.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    sr = state["sr"]
    seconds = float(params["seconds"])
    start, seconds = starting_sound(params, seconds)
    takes = []
    # One take per seed, so a take's seed makes that same sound again.
    for i, out in enumerate(outs):
        report.progress(
            0.05 + 0.9 * i / len(outs),
            f"Generating · take {i + 1} of {len(outs)}" if len(outs) > 1 else "Generating",
        )
        # Only what the job set; the rest stays at the library's defaults (8 steps, no guidance).
        extra = {}
        if params.get("steps") is not None:
            extra["steps"] = int(params["steps"])
        if params.get("cfg") is not None:
            extra["cfg_scale"] = float(params["cfg"])
        if params.get("negative"):
            extra["negative_prompt"] = params["negative"]
        audio = model.generate(
            prompt=params["prompt"],
            duration=seconds,
            **start,
            batch_size=1,
            seed=int(seed) + i,
            **extra,
        )
        clip = audio.detach().float().cpu().numpy().reshape(-1, audio.shape[-1]).T
        if params.get("trim", True) and not params.get("region"):
            # Only the silence the model pads short sounds with; decays and room tone stay.
            clip = trim(clip, sr, below_peak_db=60.0, floor_db=-75.0, pad_s=0.05)
        # Loudness stays as generated: a pin drop should not come out as loud
        # as an explosion. Only keep peaks from clipping.
        peak = float(np.max(np.abs(clip))) if clip.size else 0.0
        if peak > 0.99:
            clip = clip * (0.99 / peak)
        write_audio(out, clip, sr, params.get("format", "wav16"))
        takes.append({"path": out, "duration": round(len(clip) / sr, 3), "seed": int(seed) + i})
    report.progress(1.0, "Done")
    return {"takes": takes}


if __name__ == "__main__":
    serve({"load": load, "generate": generate})
