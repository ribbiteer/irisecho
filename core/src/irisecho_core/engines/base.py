# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every engine provides, and the context a job runs in."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from irisecho_core import paths
from irisecho_core.hardware import Hardware
from irisecho_core.registry import FileSpec, ModelSpec, Variant


@dataclass
class RunContext:
    job_id: str
    params: dict
    model: ModelSpec
    variant: Variant
    files: dict[str, Path]  # file id -> path on disk
    specs: dict[str, FileSpec]
    out_dir: Path
    stem: str  # output file names start with this
    report: Callable[[float | None, str | None], None]
    category_dirs: Callable[[str], list[Path]]
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    def output(self, index: int, ext: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"-{index + 1}" if index else ""
        return self.out_dir / f"{self.stem}{suffix}.{ext}"


class EngineStartError(RuntimeError):
    """An engine's own process would not start, so every job waiting for it would fail too."""


class Engine(ABC):
    id: str = ""
    name: str = ""
    # Bump when install steps change, so existing installs are redone.
    install_version: int = 1

    def __init__(self, hardware: Hardware):
        self.hw = hardware
        self.home = paths.sub("engines") / self.id
        self.state = "idle"  # idle | installing | starting | ready | busy | error
        self.detail = ""

    # --- installation ------------------------------------------------------

    @property
    def stamp_path(self) -> Path:
        return self.home / "installed.json"

    def installed(self) -> bool:
        try:
            stamp = json.loads(self.stamp_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            stamp.get("version") == self.install_version and stamp.get("key") == self.install_key()
        )

    def install_key(self) -> str:
        """Anything that, when it changes, means the environment must be rebuilt."""
        return f"{self.hw.backend}:{self.hw.cuda_tag}"

    def mark_installed(self) -> None:
        self.stamp_path.write_text(
            json.dumps({"version": self.install_version, "key": self.install_key()}),
            encoding="utf-8",
        )

    def supported(self) -> bool:
        return True

    @abstractmethod
    async def install(self, log: Callable[[str], None]) -> None: ...

    # --- running -----------------------------------------------------------

    @abstractmethod
    async def run(self, ctx: RunContext) -> list[dict]:
        """Run one job. Returns output descriptors: {"path", "type", ...}."""

    async def release(self) -> None:  # noqa: B027 - optional hook
        """Free the GPU for another engine."""

    async def cancel(self, job_id: str) -> None:  # noqa: B027 - optional hook
        """Stop the running job as soon as possible."""

    async def shutdown(self) -> None:
        await self.release()

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "installed": self.installed(),
            "supported": self.supported(),
            "state": self.state,
            "detail": self.detail,
        }
