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
import os
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
    index: int = 0  # nvidia-smi's index, which is PCI bus order
    uuid: str = ""  # "GPU-…", stable across reboots and device orderings


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
    gpu_uuid: str | None = None  # the card the engines run on
    gpu_pinned: bool = False  # chosen in Settings rather than picked automatically

    @property
    def gpu(self) -> Gpu | None:
        return next((g for g in self.gpus if g.uuid and g.uuid == self.gpu_uuid), None)

    @property
    def vram_mb(self) -> int:
        if self.gpu:
            return self.gpu.vram_mb
        return max((g.vram_mb for g in self.gpus), default=0)

    def public(self) -> dict:
        data = asdict(self)
        data["vram_mb"] = self.vram_mb
        data["gpu"] = asdict(self.gpu) if self.gpu else None
        return data


def parse_gpus(out: str) -> list[Gpu]:
    """Rows of `index,uuid,name,memory.total,driver_version,compute_cap` from nvidia-smi."""
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        index, uuid, mem, driver, cap = parts[0], parts[1], *parts[-3:]
        name = ", ".join(parts[2:-3])  # a name could hold a comma
        try:
            gpus.append(Gpu(name, int(float(mem)), driver, float(cap), int(index), uuid))
        except ValueError:
            continue
    return gpus


def _nvidia_gpus() -> list[Gpu]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=index,uuid,name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_gpus(out)


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


def choose(gpus: list[Gpu], wanted: str = "") -> Gpu | None:
    """The card to run on: the one asked for when it can run the engines, else the largest."""
    usable = [g for g in gpus if g.compute_cap >= 7.5]
    if not usable:
        return None
    return next((g for g in usable if wanted and g.uuid == wanted), None) or max(
        usable, key=lambda g: g.vram_mb
    )


def classify(
    system: str, machine: str, gpus: list[Gpu], wanted: str = ""
) -> tuple[str, str | None, str | None]:
    """Return (backend, quant, cuda_tag) for a machine."""
    if system == "Darwin" and machine.lower() in ("arm64", "aarch64"):
        return "mps", None, None
    gpu = choose(gpus, wanted)
    if system in ("Windows", "Linux") and gpu:
        quant = "fp4" if gpu.compute_cap >= 12.0 else "int4"
        try:
            driver = float(".".join(gpu.driver.split(".")[:2]))
        except ValueError:
            driver = 0.0
        cuda_tag = "cu130" if driver >= CU130_MIN_DRIVER else "cu128"
        return "cuda", quant, cuda_tag
    return "cpu", None, None


@lru_cache(maxsize=4)
def detect(wanted: str = "") -> Hardware:
    """This machine, running on the card whose uuid is `wanted` (empty: pick automatically)."""
    system, machine = platform.system(), platform.machine()
    gpus = _nvidia_gpus() if system in ("Windows", "Linux") else []
    backend, quant, cuda_tag = classify(system, machine, gpus, wanted)
    gpu = choose(gpus, wanted) if backend == "cuda" else None
    return Hardware(
        system=system,
        machine=machine,
        ram_mb=_ram_mb(),
        tier=support_tier(system, machine),
        backend=backend,
        quant=quant,
        cuda_tag=cuda_tag,
        gpus=gpus,
        gpu_uuid=gpu.uuid if gpu and gpu.uuid else None,
        gpu_pinned=bool(gpu and wanted and gpu.uuid == wanted),
    )


def gpu_env(hw: Hardware) -> dict[str, str]:
    """Environment that makes an engine's CUDA see only the chosen card.

    CUDA numbers cards fastest first while nvidia-smi numbers them by PCI bus, so
    on a machine with several cards "device 0" is not necessarily the card the
    installs were chosen for. A uuid names the card whatever the ordering. One
    card picked automatically changes nothing, and a CUDA_VISIBLE_DEVICES the
    person set themselves is kept unless they chose a card in Settings.
    """
    if hw.backend != "cuda" or not hw.gpu_uuid:
        return {}
    if not hw.gpu_pinned and (len(hw.gpus) < 2 or "CUDA_VISIBLE_DEVICES" in os.environ):
        return {}
    return {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": hw.gpu_uuid}
