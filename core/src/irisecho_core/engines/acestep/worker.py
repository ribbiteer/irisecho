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

    # ACE-Step quietly makes fewer takes than asked when free VRAM is short (its VRAM
    # guard, core/generation/handler/memory_utils.py _vram_guard_reduce_batch, allows
    # about 0.5 GB a take plus 0.15 GB per extra minute, so a 4-minute song with the
    # planner loaded gets one take on a 12 GB card). Asking the guard first and making
    # the rest in further passes gives every take; each pass also plans its own songs.
    takes: list[dict] = []
    first_plan: dict = {}
    while len(takes) < len(outs):
        done = len(takes)
        batch = pass_size(state["dit"], len(outs) - done, params["duration"])
        config = GenerationConfig(
            batch_size=batch,
            use_random_seed=False,
            seeds=seeds[done : done + batch],
            audio_format="wav",
        )
        label = "Composing" if batch == len(outs) else f"Composing take {done + 1} of {len(outs)}"

        def progress(value=None, desc=None, *args, _done=done, _batch=batch, _label=label, **kw):
            try:
                fraction = float(value) if value is not None else None
            except (TypeError, ValueError):
                fraction = None
            if fraction is not None:
                fraction = (_done + _batch * fraction) / len(outs)
            report.progress(fraction, _label if not desc else str(desc)[:80])

        report.progress(max(0.02, done / len(outs)), label)
        result = generate_music(
            state["dit"], state["llm"], gen, config, save_dir=None, progress=progress
        )
        if not result.success:
            raise RuntimeError(result.error or "ACE-Step did not return any music.")
        plan = planned(result)
        first_plan = first_plan or plan
        made = 0
        for item in result.audios[:batch]:
            tensor = item.get("tensor")
            if tensor is None:
                continue
            i = len(takes)
            audio = tensor.detach().cpu().float().numpy().T  # [samples, channels]
            sr = int(item.get("sample_rate") or 48000)
            if params.get("loop") and params.get("bpm"):
                audio = loop_cut(audio, sr, int(params["bpm"]))
            elif params.get("trim_end"):
                audio = trim_end(audio, sr)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 0.99:
                audio = audio * (0.99 / peak)
            write_audio(outs[i], audio, sr, params.get("format", "wav16"))
            take = {
                "path": outs[i],
                "duration": round(len(audio) / sr, 2),
                "seed": (item.get("params") or {}).get("seed", seeds[i]),
            }
            if plan:
                take["plan"] = plan
            takes.append(take)
            made += 1
        if not made:
            break  # a pass that made nothing would make nothing again
    if not takes:
        raise RuntimeError("ACE-Step did not return any music.")
    report.progress(1.0, "Done")
    return {"takes": takes, "plan": first_plan}


def pass_size(dit, wanted: int, duration) -> int:
    """How many takes ACE-Step will make in one go (its VRAM guard's answer, at least 1)."""
    guard = getattr(dit, "_vram_guard_reduce_batch", None)
    if guard is None or wanted <= 1:
        return max(1, wanted)
    try:
        return max(1, min(wanted, int(guard(wanted, audio_duration=duration))))
    except Exception:
        return wanted


# What the planner (5 Hz LM) decided, when it ran: generate_music keeps it in
# extra_outputs["lm_metadata"] (inference.py) and conditions the music on it. The
# planner writes nothing when the job sets bpm, key, time signature and duration
# (llm_inference.py has_all_metas): lm_metadata then only echoes them, with no
# caption, so there is no plan to report. It never rewrites lyrics.
PLAN_KEYS = ("caption", "bpm", "keyscale", "timesignature", "duration", "language")


def planned(result) -> dict:
    meta = (getattr(result, "extra_outputs", None) or {}).get("lm_metadata") or {}
    if not isinstance(meta, dict) or not str(meta.get("caption") or "").strip():
        return {}
    out = {}
    for key in PLAN_KEYS:
        value = meta.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip() not in ("", "N/A", "None"):
            out[key] = value
    return out


def trim_end(audio: np.ndarray, sr: int, floor_db: float = -45.0) -> np.ndarray:
    """Cut a near-silent tail (ACE-Step often finishes a song several seconds early and
    pads the rest quietly), keeping half a second of it with a short fade."""
    window = max(1, int(sr * 0.05))
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    frames = len(mono) // window
    if not frames:
        return audio
    rms = np.sqrt((mono[: frames * window].reshape(frames, window) ** 2).mean(axis=1))
    loud = np.nonzero(rms > 10 ** (floor_db / 20))[0]
    if not loud.size:
        return audio
    end = min(len(audio), (int(loud[-1]) + 1) * window + int(sr * 0.5))
    if end >= len(audio) - sr:  # less than a second to gain: leave it as made
        return audio
    out = audio[:end].copy()
    fade = min(len(out), int(sr * 0.3))
    ramp = np.linspace(1.0, 0.0, fade, dtype=out.dtype)
    out[-fade:] *= ramp[:, None] if out.ndim > 1 else ramp
    return out


if __name__ == "__main__":
    serve({"load": load, "compose": compose})
