# SPDX-License-Identifier: AGPL-3.0-or-later
"""Advanced controls: checking the values a job asks for against what its model offers.

Each model lists its controls in the registry. A job may set any of them; a value
that is left out means "what the model is tuned for", and the engine then builds
exactly the job it always built. The params every studio already had (prompt,
aspect, seed, ...) are listed here so they are not reported as unknown.
"""

from __future__ import annotations

import math

from irisecho_core.registry import Control, ModelSpec

# Params each kind of model took before controls existed. Unknown params are
# reported back as warnings (and ignored), not refused, so older scripts keep working.
BASE: dict[str, set[str]] = {
    "image": {
        "prompt",
        "aspect",
        "seed",
        "image1",
        "denoise",
        "width",
        "height",
        "guide",
        "guide_type",
    },
    "edit": {"prompt", "image1", "image2", "image3", "seed", "box", "mask"},
    "upscale": {"image1", "scale", "retain", "seed"},
    "video": {"prompt", "aspect", "seconds", "size", "smooth", "start", "end", "seed", "camera"},
    "video-upscale": {"video", "short_side", "seed"},
    "model3d": {"front", "back", "detail", "faces", "textures", "openings", "seed"},
    "views3d": {"image1", "level", "seed"},
    "voice": {"text", "voice", "speed", "clean"},
    "clone": {"text", "ref", "consent", "takes", "seed", "exaggeration", "cfg", "clean"},
    "music": {
        "prompt",
        "duration",
        "bpm",
        "lyrics",
        "takes",
        "loop",
        "thinking",
        "seed",
        "source",
        "task",
        "region",
        "extend_by",
    },
    "sfx": {"prompt", "duration", "takes", "trim", "seed", "init_audio", "region"},
    "prompt": {"mode", "target", "text", "images"},
}
# Any job may carry a label of the caller's own (the command line sets it).
COMMON = {"id"}

MAX_TEXT = 4000
MAX_LORAS = 3
LORA_STRENGTH = (-2.0, 2.0)


class ControlError(ValueError):
    """A control value the model cannot take; the message is for people."""


def _number(c: Control, value) -> float:
    if isinstance(value, bool):
        raise ControlError(f"{c.label} must be a number.")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ControlError(f"{c.label} must be a number.") from None
    if not isinstance(value, int | float) or not math.isfinite(value):
        raise ControlError(f"{c.label} must be a number.")
    if c.type == "int":
        if value != int(value):
            raise ControlError(f"{c.label} must be a whole number.")
        value = int(value)
    else:
        value = float(value)
    if (c.min is not None and value < c.min) or (c.max is not None and value > c.max):
        raise ControlError(f"{c.label} must be between {_fmt(c.min)} and {_fmt(c.max)}.")
    return value


def _fmt(v) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _bool(c: Control, value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in (
        "true",
        "false",
        "yes",
        "no",
        "on",
        "off",
        "1",
        "0",
    ):
        return value.lower() in ("true", "yes", "on", "1")
    raise ControlError(f"{c.label} must be true or false.")


def _choice(c: Control, value):
    allowed = c.values()
    if value in allowed:
        return value
    if not isinstance(value, bool):
        # "4" for a numeric option, or 4 (a command line's --set reads JSON) for a "4" option.
        text = _fmt(value) if isinstance(value, int | float) else str(value)
        for a in allowed:
            if (
                not isinstance(a, bool)
                and (_fmt(a) if isinstance(a, int | float) else str(a)) == text
            ):
                return a
    shown = ", ".join(
        '"" (any)' if a == "" else _fmt(a) if isinstance(a, float) else str(a) for a in allowed
    )
    raise ControlError(f"{c.label} must be one of: {shown}.")


def _strength(c: Control, value, what: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ControlError(f"{c.label}: {what} must be a number.") from None
    lo, hi = LORA_STRENGTH
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ControlError(f"{c.label}: {what} must be between {_fmt(lo)} and {_fmt(hi)}.")
    return v


def _loras(c: Control, value) -> list[dict]:
    if not isinstance(value, list):
        raise ControlError(f"{c.label} must be a list of add-ons.")
    if len(value) > MAX_LORAS:
        raise ControlError(f"{c.label}: at most {MAX_LORAS} add-ons per job.")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise ControlError(f"{c.label} must be a list of add-ons.")
        if c.pair:
            high, low = item.get("high"), item.get("low")
            if not (isinstance(high, str) and high) and not (isinstance(low, str) and low):
                raise ControlError(f"{c.label}: each add-on needs a high- or low-noise file.")
            entry = {"strength": _strength(c, item.get("strength", 1.0), "strength")}
            for key in ("high", "low"):
                if item.get(key):
                    if not isinstance(item[key], str):
                        raise ControlError(f"{c.label}: file names must be text.")
                    entry[key] = item[key]
        else:
            name = item.get("file")
            if not isinstance(name, str) or not name:
                raise ControlError(f"{c.label}: each add-on needs a file.")
            entry = {"file": name, "strength": _strength(c, item.get("strength", 1.0), "strength")}
        out.append(entry)
    return out


def coerce(c: Control, value):
    if c.type in ("int", "float"):
        return _number(c, value)
    if c.type == "bool":
        return _bool(c, value)
    if c.type == "choice":
        return _choice(c, value)
    if c.type == "text":
        if not isinstance(value, str):
            raise ControlError(f"{c.label} must be text.")
        if len(value) > MAX_TEXT:
            raise ControlError(f"{c.label} is too long.")
        return value
    if c.type == "loras":
        return _loras(c, value)
    raise ControlError(f"{c.label} cannot be set.")


def check(model: ModelSpec, params: dict) -> tuple[dict, list[str]]:
    """Validated params plus warnings about params the model does not use.

    Control values are converted to their type; `None` means "recommended" and is
    dropped, as is a LoRA list that is empty.
    """
    if not isinstance(params, dict):
        raise ControlError("params must be an object.")
    out: dict = {}
    warnings: list[str] = []
    base = BASE.get(model.kind, set()) | COMMON
    for key, value in params.items():
        c = model.control(key)
        if c is not None:
            if value is None:
                continue
            value = coerce(c, value)
            if c.type == "loras" and not value:
                continue
            out[key] = value
            continue
        if key not in base:
            warnings.append(f"{model.name} does not use '{key}'; it was ignored.")
        out[key] = value
    return out, warnings


def opt(params: dict, settings: dict, name: str, fallback=None):
    """The value a builder should use: the job's, else the model's setting, else the fallback."""
    value = params.get(name)
    if value is not None:
        return value
    return settings.get(name, fallback)
