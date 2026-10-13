# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chatterbox worker. Runs inside the Chatterbox engine's own environment."""

from __future__ import annotations

import random

import numpy as np
import soundfile as sf
import torch
from irisecho_audio import normalize, text_chunks, trim
from irisecho_worker import Reporter, serve

# Chatterbox speaks at most 1000 speech tokens (about 40 s) per call and drifts
# well before that, so longer text is spoken sentence by sentence.
PAUSE_S = 0.2

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
    sr = int(model.sr)
    parts = text_chunks(params["text"])
    # The reference is analysed once; every take and chunk reuses it.
    if params["variant"] == "turbo":
        try:
            ref_seconds = sf.info(params["ref"]).duration
        except RuntimeError:
            ref_seconds = None  # a format soundfile cannot open; the model's loader may
        if ref_seconds is not None and ref_seconds <= 5.0:
            raise ValueError("Chatterbox Turbo needs a voice sample longer than 5 seconds.")
        model.prepare_conditionals(params["ref"])
        kwargs = {}
    else:
        model.prepare_conditionals(params["ref"], exaggeration=params["exaggeration"])
        kwargs = {"exaggeration": params["exaggeration"], "cfg_weight": params["cfg"]}
    gap = np.zeros(int(PAUSE_S * sr), dtype=np.float32)
    takes = []
    for i, out in enumerate(outs):
        torch.manual_seed(int(seed) + i)
        pieces = []
        for k, part in enumerate(parts):
            report.progress(
                (i * len(parts) + k) / (len(outs) * len(parts)),
                f"Speaking · take {i + 1} of {len(outs)}" if len(outs) > 1 else "Speaking",
            )
            wav = model.generate(part, **kwargs)
            piece = wav.detach().float().cpu().numpy().reshape(-1)
            if len(parts) > 1:
                piece = trim(piece, sr, pad_s=0.05)
                pieces += [piece, gap] if k < len(parts) - 1 else [piece]
            else:
                pieces.append(piece)
        audio = np.concatenate(pieces)
        if params.get("clean", True):
            audio = normalize(trim(audio, sr), sr)
        sf.write(out, audio, sr, subtype="PCM_16")
        takes.append(
            {
                "path": out,
                "duration": round(len(audio) / sr, 3),
                "seed": int(seed) + i,
            }
        )
    report.progress(1.0, "Done")
    return {"takes": takes}


if __name__ == "__main__":
    serve({"load": load, "speak": speak})
