# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this machine can run.

The answer decides which build of each model to use:

* NVIDIA Turing to Ada (RTX 20/30/40): nunchaku int4 weights
* NVIDIA Blackwell (RTX 50): nunchaku fp4 weights
* Apple Silicon: Metal (MPS) builds
* anything else: CPU-only engines
"""

from __future__ import annotations

import ctypes
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from irisecho_core.platform_support import support_tier

# The newest CUDA build of torch needs at least this driver.
CU130_MIN_DRIVER = 580.0


@dataclass(frozen=True)
class Gpu:
    name: str
    vram_mb: int
    driver: str
    compute_cap: float


@dataclass(frozen=True)
class Hardware:
    system: str
    machine: str
    ram_mb: int
    tier: str
    backend: str  # "cuda" | "mps" | "cpu"
    quant: str | None  # "int4" | "fp4" | None
    cuda_tag: str | None  # "cu130" | "cu128" | None
    gpus: list[Gpu] = field(default_factory=list)

    @property
    def vram_mb(self) -> int:
        return max((g.vram_mb for g in self.gpus), default=0)

    def public(self) -> dict:
        data = asdict(self)
        data["vram_mb"] = self.vram_mb
        return data


def _nvidia_gpus() -> list[Gpu]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        name, mem, driver, cap = parts
        try:
            gpus.append(Gpu(name, int(float(mem)), driver, float(cap)))
        except ValueError:
            continue
    return gpus


_vram: tuple[float, list[dict]] = (-1e9, [])


def vram_usage(max_age: float = 2.0) -> list[dict]:
    """Memory in use and free on each NVIDIA card right now, in MB, desktop included.

    Empty without nvidia-smi. A reading younger than `max_age` seconds is reused.
    """
    global _vram
    now = time.monotonic()
    if now - _vram[0] < max_age:
        return _vram[1]
    rows: list[dict] = []
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
                creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
            ).stdout
            for line in out.strip().splitlines():
                used, free = (int(float(v)) for v in line.split(","))
                rows.append({"used_mb": used, "free_mb": free})
        except (OSError, subprocess.SubprocessError, ValueError):
            rows = []
    _vram = (now, rows)
    return rows


def _ram_mb() -> int:
    try:
        if sys.platform == "win32":

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys // 2**20
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return int(out.stdout.strip()) // 2**20
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def classify(system: str, machine: str, gpus: list[Gpu]) -> tuple[str, str | None, str | None]:
    """Return (backend, quant, cuda_tag) for a machine."""
    if system == "Darwin" and machine.lower() in ("arm64", "aarch64"):
        return "mps", None, None
    usable = [g for g in gpus if g.compute_cap >= 7.5]
    if system in ("Windows", "Linux") and usable:
        gpu = max(usable, key=lambda g: g.vram_mb)
        quant = "fp4" if gpu.compute_cap >= 12.0 else "int4"
        try:
            driver = float(".".join(gpu.driver.split(".")[:2]))
        except ValueError:
            driver = 0.0
        cuda_tag = "cu130" if driver >= CU130_MIN_DRIVER else "cu128"
        return "cuda", quant, cuda_tag
    return "cpu", None, None


@lru_cache(maxsize=1)
def detect() -> Hardware:
    system, machine = platform.system(), platform.machine()
    gpus = _nvidia_gpus() if system in ("Windows", "Linux") else []
    backend, quant, cuda_tag = classify(system, machine, gpus)
    return Hardware(
        system=system,
        machine=machine,
        ram_mb=_ram_mb(),
        tier=support_tier(system, machine),
        backend=backend,
        quant=quant,
        cuda_tag=cuda_tag,
        gpus=gpus,
    )
