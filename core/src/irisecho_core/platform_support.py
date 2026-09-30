# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which support tier the current machine falls into (the README's platform table)."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformInfo:
    system: str
    machine: str
    python: str
    tier: str


def support_tier(system: str, machine: str) -> str:
    machine = machine.lower()
    if system == "Windows":
        return "supported"
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "preview"
        return "unsupported (Intel Mac)"
    if system == "Linux":
        return "works, unsupported"
    return "unsupported"


def detect() -> PlatformInfo:
    system, machine = platform.system(), platform.machine()
    return PlatformInfo(
        system=system,
        machine=machine,
        python=sys.version.split()[0],
        tier=support_tier(system, machine),
    )
