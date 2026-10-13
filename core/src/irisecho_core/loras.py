# SPDX-License-Identifier: AGPL-3.0-or-later
"""The person's own add-ons (LoRAs): finding them and telling which model family each fits.

Only `.safetensors` files are listed (never pickles). Most LoRAs carry no usable
metadata, so the family comes from the tensor names and shapes in the file's
header, which is read without loading any weights.
"""

from __future__ import annotations

import json
import os
import re
import struct
from collections import Counter
from pathlib import Path

# Families a model can declare in its `loras` control.
FAMILIES = ("flux", "zimage", "wan14b", "qwen-image", "ltx", "sd")
MAX_HEADER = 64 * 1024 * 1024

_cache: dict[tuple[str, int, int], dict] = {}


class LoraError(ValueError):
    """An add-on a job cannot use; the message is for people."""


def read_header(path: Path) -> dict:
    with path.open("rb") as f:
        raw = f.read(8)
        if len(raw) != 8:
            raise ValueError("not a safetensors file")
        (size,) = struct.unpack("<Q", raw)
        if not 2 <= size <= MAX_HEADER:
            raise ValueError("not a safetensors file")
        return json.loads(f.read(size))


def _dim(header: dict, pattern: str) -> int | None:
    """The input width of the first LoRA down/A matrix whose name matches pattern."""
    rx = re.compile(pattern)
    for key, info in header.items():
        if key == "__metadata__" or not rx.search(key):
            continue
        if (
            re.search(r"(lora_down|lora_A|\.down)\.weight$", key)
            and len(info.get("shape", [])) == 2
        ):
            return int(info["shape"][1])
    return None


def family_of(header: dict) -> str:
    meta = header.get("__metadata__") or {}
    arch = (
        f"{meta.get('modelspec.architecture', '')} {meta.get('ss_base_model_version', '')}".lower()
    )
    keys = [k for k in header if k != "__metadata__"]
    text = "\n".join(keys)
    if "img_mlp" in text or "txt_mlp" in text or "qwen" in arch:
        return "qwen-image"
    if (
        "double_blocks" in text
        or "single_blocks" in text
        or "single_transformer_blocks" in text
        or "flux" in arch
    ):
        return "flux"
    if re.search(r"(^|[._])blocks[._]\d+[._](self_attn|cross_attn)", text):
        dim = _dim(header, r"self_attn[._]q")
        return "wan14b" if dim in (None, 5120) else "unknown"
    if re.search(r"(noise_refiner|context_refiner)", text) or re.search(
        r"layers[._]\d+[._]attention", text
    ):
        dim = _dim(header, r"layers[._]\d+[._]attention")
        if dim in (None, 3840) or "z-image" in arch or "zimage" in arch:
            return "zimage"
        return "unknown"
    if re.search(r"transformer_blocks[._]\d+[._](attn1|audio_attn)", text):
        return "ltx"
    if "lora_unet_" in text or "lora_te" in text or "down_blocks" in text or "input_blocks" in text:
        return "sd"
    return "unknown"


def triggers_of(header: dict) -> list[str]:
    meta = header.get("__metadata__") or {}
    words: list[str] = []
    phrase = meta.get("modelspec.trigger_phrase") or meta.get("trigger_word") or ""
    if isinstance(phrase, str) and phrase.strip():
        words += [w.strip() for w in phrase.split(",") if w.strip()]
    try:
        freq = json.loads(meta.get("ss_tag_frequency") or "{}")
    except ValueError:
        freq = {}
    counts: Counter = Counter()
    if isinstance(freq, dict):
        for tags in freq.values():
            if isinstance(tags, dict):
                counts.update(
                    {str(t).strip(): int(n) for t, n in tags.items() if isinstance(n, int)}
                )
    words += [t for t, _ in counts.most_common(6) if t and t not in words]
    return words[:6]


def pair_hint(name: str) -> str:
    low = name.lower()
    if re.search(r"high[_ -]?noise|(^|[_\-. ])(high|hn)([_\-. ]|$)", low):
        return "high"
    if re.search(r"low[_ -]?noise|(^|[_\-. ])(low|ln)([_\-. ]|$)", low):
        return "low"
    return ""


def describe(path: Path) -> dict:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    found = _cache.get(key)
    if found is None:
        try:
            header = read_header(path)
            found = {"family": family_of(header), "triggers": triggers_of(header)}
        except (OSError, ValueError, KeyError, TypeError):
            found = {"family": "unreadable", "triggers": []}
        _cache[key] = found
    return found


def scan(dirs: list[Path]) -> list[dict]:
    """Every add-on in these folders (subfolders included), first folder winning a name clash."""
    seen: dict[str, dict] = {}
    for root in dirs:
        if not root.is_dir():
            continue
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if not n.lower().endswith(".safetensors"):
                    continue
                path = Path(dirpath) / n
                rel = os.path.relpath(path, root)
                if rel in seen:
                    continue
                info = describe(path)
                if info["family"] == "unreadable":
                    continue
                seen[rel] = {
                    "file": rel,
                    "family": info["family"],
                    "triggers": info["triggers"],
                    "size": path.stat().st_size,
                    "pair_hint": pair_hint(n),
                }
    return sorted(seen.values(), key=lambda e: e["file"].lower())


def resolve(dirs: list[Path], name: str, family: str) -> str:
    """Check that a job's add-on exists in the add-on folders and fits; returns its name."""
    if (
        not isinstance(name, str)
        or not name
        or ".." in Path(name).parts
        or Path(name).is_absolute()
    ):
        raise LoraError("That add-on file name cannot be used.")
    for root in dirs:
        path = root / name
        if path.is_file() and path.suffix.lower() == ".safetensors":
            fam = describe(path)["family"]
            if fam not in (family, "unknown"):
                raise LoraError(
                    f"{Path(name).stem} is made for another kind of model ({fam}), not this one."
                )
            return name
    raise LoraError(f"The add-on {Path(name).stem} is not in the loras folder.")
