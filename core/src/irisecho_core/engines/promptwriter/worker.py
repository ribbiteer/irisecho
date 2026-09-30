# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prompt writer worker. Runs inside the prompt writer's own environment."""

from __future__ import annotations

import torch
from irisecho_worker import Reporter, serve
from PIL import Image

MAX_SIDE = 1024

state: dict = {"model": None, "processor": None, "key": None}


def load(params: dict, report: Reporter) -> dict:
    device = params["device"]
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    key = (params["dir"], device)
    if state["key"] == key:
        return {"device": device}
    report.progress(None, "Loading the prompt writer")
    from transformers import AutoModelForImageTextToText, AutoProcessor

    dtype = torch.bfloat16 if device != "cpu" else torch.float32
    model = AutoModelForImageTextToText.from_pretrained(params["dir"], dtype=dtype)
    state.update(
        model=model.to(device).eval(),
        processor=AutoProcessor.from_pretrained(params["dir"]),
        key=key,
        device=device,
    )
    return {"device": device}


def open_image(path: str) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def write(params: dict, report: Reporter) -> dict:
    model, processor = state["model"], state["processor"]
    if model is None:
        raise RuntimeError("load was not called")
    report.progress(0.1, "Writing")
    content = [{"type": "image", "image": open_image(p)} for p in params["images"]]
    content.append({"type": "text", "text": params["user"]})
    messages = [
        {"role": "system", "content": [{"type": "text", "text": params["system"]}]},
        {"role": "user", "content": content},
    ]
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    ).to(model.device)
    with torch.inference_mode():
        out = model.generate(
            **inputs,
            max_new_tokens=int(params.get("max_new_tokens", 300)),
            num_beams=int(params.get("beams", 4)),
            do_sample=False,
            early_stopping=True,
            temperature=None,
            top_p=None,
            top_k=None,
        )
    generated = out[:, inputs["input_ids"].shape[1] :]
    text = processor.batch_decode(generated, skip_special_tokens=True)[0]
    report.progress(1.0, "Done")
    return {"text": text}


if __name__ == "__main__":
    serve({"load": load, "write": write})
