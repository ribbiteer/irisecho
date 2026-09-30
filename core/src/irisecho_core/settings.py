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

    @property
    def models_path(self) -> Path:
        path = Path(self.models_dir) if self.models_dir else paths.data_dir() / "models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def outputs_path(self) -> Path:
        path = Path(self.outputs_dir) if self.outputs_dir else paths.data_dir() / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def public(self) -> dict:
        data = asdict(self)
        data["models_path"] = str(self.models_path)
        data["outputs_path"] = str(self.outputs_path)
        return data


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
