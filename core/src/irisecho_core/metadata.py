# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings written into, and read back from, finished pictures.

Off by default (Settings › Keep prompts out of image files). When the person turns
that off, each PNG gets two text chunks: `parameters` in the A1111 layout that
Civitai, Forge and A1111's PNG Info read, and `irisecho` with the job's model and
params so IrisEcho can bring the exact settings back. Nothing else is written: no
paths, names or machine details.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from PIL import Image

from irisecho_core.registry import ModelSpec

PNG = b"\x89PNG\r\n\x1a\n"
# Params that are files on this computer, not settings.
FILE_PARAMS = {
    "image1",
    "image2",
    "image3",
    "guide",
    "start",
    "end",
    "ref",
    "video",
    "front",
    "back",
    "source",
    "init_audio",
}


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def add_text(path: Path, texts: dict[str, str]) -> None:
    """Insert tEXt/iTXt chunks right after IHDR, without re-encoding the picture."""
    raw = path.read_bytes()
    if raw[:8] != PNG:
        return
    (ihdr_len,) = struct.unpack(">I", raw[8:12])
    at = 8 + 12 + ihdr_len
    chunks = b""
    for key, text in texts.items():
        try:
            chunks += _chunk(b"tEXt", key.encode("latin-1") + b"\0" + text.encode("latin-1"))
        except UnicodeEncodeError:
            # iTXt: keyword, no compression, empty language tag and translated keyword, UTF-8 text.
            chunks += _chunk(b"iTXt", key.encode("latin-1") + b"\0\0\0\0\0" + text.encode("utf-8"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw[:at] + chunks + raw[at:])
    tmp.replace(path)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def a1111(model: ModelSpec, params: dict, seed, size: tuple[int, int] | None) -> str:
    """The `parameters` text: prompt, then one line of settings."""
    values = {c.name: params.get(c.name, c.default) for c in model.controls}
    parts = []
    steps = values.get("steps", model.settings.get("steps"))
    if steps is not None:
        parts.append(f"Steps: {steps}")
    if values.get("sampler"):
        parts.append(f"Sampler: {values['sampler']}")
    if values.get("scheduler"):
        parts.append(f"Schedule type: {values['scheduler']}")
    parts.append(f"CFG scale: {_fmt(model.settings.get('cfg', 1.0) or 1.0)}")
    guidance = values.get("guidance", model.settings.get("guidance"))
    if guidance is not None:
        parts.append(f"Distilled CFG Scale: {_fmt(guidance)}")
    if seed is not None:
        parts.append(f"Seed: {seed}")
    if size:
        parts.append(f"Size: {size[0]}x{size[1]}")
    parts.append(f"Model: {model.name}")
    if params.get("image1") and params.get("denoise") is not None:
        parts.append(f"Denoising strength: {_fmt(params['denoise'])}")
    loras = params.get("loras") or []
    if loras:
        names = ", ".join(
            f"{Path(e.get('file') or e.get('high') or '').stem}: {_fmt(e['strength'])}"
            for e in loras
        )
        parts.append(f'Lora weights: "{names}"')
    return f"{params.get('prompt', '').strip()}\n" + ", ".join(parts)


def embed(path: Path, model: ModelSpec, params: dict, seed, size) -> None:
    clean = {k: v for k, v in params.items() if k not in FILE_PARAMS and v is not None}
    if seed is not None:
        clean["seed"] = seed
    add_text(
        path,
        {
            "parameters": a1111(model, params, seed, size),
            "irisecho": json.dumps({"model": model.id, "params": clean}, separators=(",", ":")),
        },
    )


def read(path: Path) -> dict | None:
    """Settings found in a picture: IrisEcho's own, else what can be read from A1111's text."""
    try:
        with Image.open(path) as im:
            texts = dict(getattr(im, "text", {}) or {})
    except (OSError, ValueError):
        return None
    if "irisecho" in texts:
        try:
            found = json.loads(texts["irisecho"])
            if isinstance(found, dict) and isinstance(found.get("params"), dict):
                return {
                    "model": found.get("model"),
                    "params": found["params"],
                    "source": "irisecho",
                }
        except ValueError:
            pass
    text = texts.get("parameters")
    if not text:
        return None
    prompt, _, rest = text.rpartition("\n")
    if not prompt:  # a single line: prompt only, or settings only
        prompt, rest = (text, "") if "Steps:" not in text else ("", text)
    if "\nNegative prompt:" in "\n" + prompt:
        prompt = prompt.split("Negative prompt:", 1)[0]
    params: dict = {"prompt": prompt.strip()}
    fields = {}
    for item in rest.split(", "):
        key, sep, value = item.partition(": ")
        if sep:
            fields[key.strip()] = value.strip()
    if fields.get("Seed", "").isdigit():
        params["seed"] = int(fields["Seed"])
    if fields.get("Steps", "").isdigit():
        params["steps"] = int(fields["Steps"])
    return {"model": None, "params": params, "source": "parameters"}
