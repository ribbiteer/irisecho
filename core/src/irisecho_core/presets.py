# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named sets of advanced control values, per model, saved by the person.

Kept in presets.json in the data folder ({model id: {name: {control: value}}}),
so they can be read, copied and edited by hand.
"""

from __future__ import annotations

import json
import os
import tempfile

from irisecho_core import controls, paths
from irisecho_core.registry import ModelSpec

MAX_NAME = 60
MAX_PER_MODEL = 50


def _file():
    return paths.data_dir() / "presets.json"


def load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = _file()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".presets-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def put(model: ModelSpec, name: str, values: dict) -> dict:
    name = (name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise controls.ControlError(f"Give the preset a name of up to {MAX_NAME} characters.")
    if not isinstance(values, dict):
        raise controls.ControlError("A preset holds control values.")
    unknown = [k for k in values if model.control(k) is None]
    if unknown:
        raise controls.ControlError(f"{model.name} has no control named {', '.join(unknown)}.")
    clean, _ = controls.check(model, values)
    data = load()
    mine = data.setdefault(model.id, {})
    if name not in mine and len(mine) >= MAX_PER_MODEL:
        raise controls.ControlError(f"{model.name} already has {MAX_PER_MODEL} presets.")
    mine[name] = clean
    _save(data)
    return data


def delete(model_id: str, name: str) -> dict:
    data = load()
    if data.get(model_id, {}).pop(name, None) is not None:
        if not data[model_id]:
            del data[model_id]
        _save(data)
    return data
