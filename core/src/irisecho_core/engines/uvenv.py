# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and run engine environments with uv.

Every engine gets its own virtual environment under <data>/engines/<id>/.venv
with the Python version it needs, so engines that pin conflicting versions of
torch never meet. uv's cache and managed Pythons also live under the data
folder, so removing that folder removes everything IrisEcho installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from irisecho_core import paths

Log = Callable[[str], None]


def uv_bin() -> str:
    env = os.environ.get("IRISECHO_UV")
    if env:
        return env
    try:
        from uv import find_uv_bin

        return find_uv_bin()
    except Exception:
        found = shutil.which("uv")
        if found:
            return found
    raise RuntimeError("uv was not found; reinstall IrisEcho")


def uv_env() -> dict[str, str]:
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(paths.sub("cache") / "uv")
    env["UV_PYTHON_INSTALL_DIR"] = str(paths.sub("python"))
    # Only Pythons IrisEcho downloaded itself: an environment built on one that
    # happens to be on the computer breaks when that one is updated or removed.
    env["UV_PYTHON_PREFERENCE"] = "only-managed"
    env["UV_NO_PROGRESS"] = "1"
    # Engines never read the developer's or user's global pip/uv config.
    env["UV_NO_CONFIG"] = "1"
    env.pop("VIRTUAL_ENV", None)
    return env


def venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


async def run(args: list[str], log: Log, cwd: Path | None = None, env: dict | None = None) -> None:
    """Run a command, streaming each output line to `log`. Raises on failure."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        env=env or uv_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=_no_window(),
    )
    assert proc.stdout
    tail: list[str] = []
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            log(line)
            tail = (tail + [line])[-20:]
    code = await proc.wait()
    if code != 0:
        raise RuntimeError(
            f"{Path(args[0]).name} {args[1] if len(args) > 1 else ''} failed:\n" + "\n".join(tail)
        )


async def create_venv(venv: Path, python: str, log: Log) -> None:
    await run([uv_bin(), "venv", "--python", python, "--seed", str(venv)], log)


async def pip_install(venv: Path, packages: list[str], log: Log, *extra: str) -> None:
    await run(
        [uv_bin(), "pip", "install", "--python", str(venv_python(venv)), *extra, *packages],
        log,
    )


def torch_backend_args(backend: str, cuda_tag: str | None) -> list[str]:
    """uv flags that pick the right torch build for this machine."""
    if backend == "cuda" and cuda_tag:
        return ["--torch-backend", cuda_tag]
    if backend == "mps":
        return []  # PyPI's macOS wheels include Metal support
    return ["--torch-backend", "cpu"]


def _no_window() -> int:
    return 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
