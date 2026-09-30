# SPDX-License-Identifier: AGPL-3.0-or-later
"""ACE-Step worker. Runs inside the ACE-Step engine's own environment."""

from __future__ import annotations

import os
import random

import numpy as np
import soundfile as sf
from irisecho_worker import Reporter, serve

state: dict = {"dit": None, "llm": None, "key": None, "lm_key": None}


def load(params: dict, report: Reporter) -> dict:
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = params["checkpoints"]
    key = (params["checkpoints"], params["device"])
    if state["key"] != key:
        report.progress(None, "Loading ACE-Step")
        from acestep.handler import AceStepHandler

        dit = AceStepHandler()
        message, ok = dit.initialize_service(
            project_root=params["project"],
            config_path="acestep-v15-turbo",
            device=params["device"],
        )
        if not ok:
            raise RuntimeError(f"ACE-Step could not start: {message}")
        state.update(dit=dit, key=key)

    lm_key = (params["lm_root"], params["lm"], params["device"])
    if params.get("thinking") and state["lm_key"] != lm_key:
        report.progress(None, "Loading the song planner")
        from acestep.llm_inference import LLMHandler

        llm = LLMHandler()
        message, ok = llm.initialize(
            checkpoint_dir=params["lm_root"],
            lm_model_path=params["lm"],
            backend="pt",
            device=params["device"],
        )
        if not ok:
            raise RuntimeError(f"The song planner could not start: {message}")
        state.update(llm=llm, lm_key=lm_key)
    return {}


def loop_cut(audio: np.ndarray, sr: int, bpm: int, xfade: float = 0.25) -> np.ndarray:
    """Trim to a whole number of 4/4 bars and crossfade the tail into the head."""
    bar = int(round(sr * 240 / bpm))
    fade = int(sr * xfade)
    bars = (len(audio) - fade) // bar
    if bars < 1:
        return audio
    body = audio[: bars * bar].copy()
    tail = audio[bars * bar : bars * bar + fade]
    ramp = np.linspace(0.0, 1.0, len(tail), dtype=np.float32)[:, None]
    body[: len(tail)] = body[: len(tail)] * ramp + tail * (1.0 - ramp)
    return body


def compose(params: dict, report: Reporter) -> dict:
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    outs = params["outs"]
    base_seed = params.get("seed")
    if base_seed is None:
        base_seed = random.randrange(2**31)
    seeds = [int(base_seed) + i for i in range(len(outs))]
    lyrics = params.get("lyrics") or ""
    instrumental = not lyrics.strip()

    gen = GenerationParams(
        caption=params["caption"],
        lyrics=lyrics if not instrumental else "[inst]",
        instrumental=instrumental,
        bpm=params.get("bpm"),
        duration=params["duration"],
        thinking=bool(params.get("thinking")) and state["llm"] is not None,
        seed=seeds[0],
    )
    config = GenerationConfig(
        batch_size=len(outs),
        use_random_seed=False,
        seeds=seeds,
        audio_format="wav",
    )

    def progress(value=None, desc=None, *args, **kwargs):
        try:
            fraction = float(value) if value is not None else None
        except (TypeError, ValueError):
            fraction = None
        report.progress(fraction, "Composing" if not desc else str(desc)[:80])

    report.progress(0.02, "Composing")
    result = generate_music(
        state["dit"], state["llm"], gen, config, save_dir=None, progress=progress
    )
    if not result.success:
        raise RuntimeError(result.error or "ACE-Step did not return any music.")

    takes = []
    for i, item in enumerate(result.audios[: len(outs)]):
        tensor = item.get("tensor")
        if tensor is None:
            continue
        audio = tensor.detach().cpu().float().numpy().T  # [samples, channels]
        sr = int(item.get("sample_rate") or 48000)
        if params.get("loop") and params.get("bpm"):
            audio = loop_cut(audio, sr, int(params["bpm"]))
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.99:
            audio = audio * (0.99 / peak)
        sf.write(outs[i], audio, sr, subtype="PCM_16")
        takes.append(
            {
                "path": outs[i],
                "duration": round(len(audio) / sr, 2),
                "seed": (item.get("params") or {}).get("seed", seeds[i]),
            }
        )
    if not takes:
        raise RuntimeError("ACE-Step did not return any music.")
    report.progress(1.0, "Done")
    return {"takes": takes}


if __name__ == "__main__":
    serve({"load": load, "compose": compose})
