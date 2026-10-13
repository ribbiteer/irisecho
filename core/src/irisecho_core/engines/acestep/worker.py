# SPDX-License-Identifier: AGPL-3.0-or-later
"""ACE-Step worker. Runs inside the ACE-Step engine's own environment."""

from __future__ import annotations

import os
import random

import numpy as np
from irisecho_audio import loop_cut, write_audio
from irisecho_worker import Reporter, serve

state: dict = {"dit": None, "llm": None, "key": None, "lm_key": None}
MAX_SECONDS = 600.0  # the longest ACE-Step makes (inference.py duration range 10-600 s)


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


def source_task(params: dict) -> dict:
    """GenerationParams for a job that starts from a recording (nothing for a plain one).

    cover: task_type "cover" with the recording as src_audio (inference.py:100-105) and
    audio_cover_strength / cover_noise_strength (:188-189); the length follows the recording.
    repaint: task_type "repaint" with repainting_start / repainting_end in seconds (:161-162).
    extend: a repaint whose region starts at the end of the recording and runs `extend_by`
    seconds past it; the handler pads the recording with silence for that part
    (core/generation/handler/padding_utils.py:47-58) and the result is the recording plus
    the new music.
    """
    task = params.get("task")
    if not task:
        return {}
    out: dict = {"src_audio": params["source"]}
    if task == "cover":
        out["task_type"] = "cover"
        if params.get("cover_strength") is not None:
            out["audio_cover_strength"] = float(params["cover_strength"])
        if params.get("cover_noise") is not None:
            out["cover_noise_strength"] = float(params["cover_noise"])
    elif task == "repaint":
        out["task_type"] = "repaint"
        out["repainting_start"], out["repainting_end"] = (float(v) for v in params["region"])
    elif task == "extend":
        import soundfile as sf

        length = float(sf.info(params["source"]).duration)
        end = length + float(params["extend_by"])
        if end > MAX_SECONDS:
            raise ValueError(
                f"The recording is {length:.0f} s; adding {params['extend_by']:g} s would pass "
                f"the {MAX_SECONDS:.0f} s ACE-Step can make."
            )
        out["task_type"] = "repaint"
        out["repainting_start"], out["repainting_end"] = length, end
    else:
        raise ValueError(f"Unknown task {task}")
    return out


def compose(params: dict, report: Reporter) -> dict:
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    outs = params["outs"]
    base_seed = params.get("seed")
    if base_seed is None:
        base_seed = random.randrange(2**31)
    seeds = [int(base_seed) + i for i in range(len(outs))]
    lyrics = params.get("lyrics") or ""
    instrumental = not lyrics.strip()

    # Settings the job set; anything left out keeps ACE-Step's own default
    # (8 steps, the planner at 0.85 / 0.9, peak-normalised output, key and language
    # left to the planner).
    extra: dict = {}
    for ours, theirs in (
        ("key", "keyscale"),
        ("time_signature", "timesignature"),
        ("vocal_language", "vocal_language"),
        ("steps", "inference_steps"),
        ("lm_temperature", "lm_temperature"),
        ("lm_top_p", "lm_top_p"),
    ):
        if params.get(ours) is not None:
            extra[theirs] = params[ours]
    if params.get("raw"):
        extra["enable_normalization"] = False  # skip the -1 dBFS peak normalisation
    extra.update(source_task(params))

    gen = GenerationParams(
        caption=params["caption"],
        lyrics=lyrics if not instrumental else "[inst]",
        instrumental=instrumental,
        bpm=params.get("bpm"),
        duration=params["duration"],
        thinking=bool(params.get("thinking")) and state["llm"] is not None,
        # The turbo model is distilled for a timestep shift of 3; ACE-Step's
        # own app uses 3, while the library default of 1 is for the base model.
        shift=params.get("shift", 3.0),
        seed=seeds[0],
        **extra,
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
        write_audio(outs[i], audio, sr, params.get("format", "wav16"))
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
