# SPDX-License-Identifier: AGPL-3.0-or-later
"""User settings, stored as JSON in the data folder."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from irisecho_core import paths


@dataclass
class Settings:
    # Where downloaded weights go. Empty means <data>/models.
    models_dir: str = ""
    # Where outputs go. Empty means <data>/outputs.
    outputs_dir: str = ""
    # Existing model folders (for example a ComfyUI "models" folder) that
    # IrisEcho may read weights from instead of downloading them again.
    linked_model_dirs: list[str] = field(default_factory=list)
    # License ids the user has accepted in the app (non-commercial models).
    accepted_licenses: list[str] = field(default_factory=list)
    # Remove metadata from saved images. Always on unless turned off.
    strip_metadata: bool = True
    # Phone / LAN access. Off by default.
    lan_enabled: bool = False
    # The NVIDIA card to run on, by uuid. Empty picks the card with the most memory.
    gpu: str = ""

    @property
    def models_path(self) -> Path:
        return _folder(self.models_dir, "models")

    @property
    def outputs_path(self) -> Path:
        return _folder(self.outputs_dir, "outputs")

    def public(self) -> dict:
        data = asdict(self)
        data["models_path"] = str(self.models_path)
        data["outputs_path"] = str(self.outputs_path)
        return data


def _folder(chosen: str, default: str) -> Path:
    """The chosen folder, made if needed; the default one when the choice cannot be used.

    A bad value may already be saved (a file, a drive that is gone), and it must
    not stop the app from starting.
    """
    if chosen:
        path = Path(chosen)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass
    path = paths.data_dir() / default
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_folder(value: str) -> None:
    """Raise ValueError, with a message for the person, unless value is a usable folder."""
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Use a full folder path.")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot use {value} as a folder: {e.strerror or e}") from e


def settings_file() -> Path:
    return paths.data_dir() / "settings.json"


def load() -> Settings:
    path = settings_file()
    if not path.exists():
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save(settings: Settings) -> None:
    path = settings_file()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, indent=2)
    os.replace(tmp, path)


def update(settings: Settings, changes: dict) -> Settings:
    known = {f.name for f in fields(Settings)}
    unknown = set(changes) - known
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    for key, value in changes.items():
        setattr(settings, key, value)
    save(settings)
    return settings
