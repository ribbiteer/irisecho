# SPDX-License-Identifier: AGPL-3.0-or-later
"""An image engine that will not start must end promptly, cleanly and with a reason."""

import asyncio
import sys
from pathlib import Path

import pytest

from irisecho_core.engines import comfy, uvenv
from irisecho_core.engines.base import EngineStartError

SLEEPS = "import time\ntime.sleep(60)\n"
CRASHES = "import sys\nprint('no such option: --frobnicate')\nsys.exit(3)\n"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """The real ComfyUI engine, with a stand-in program where ComfyUI's main.py would be."""
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(uvenv, "venv_python", lambda venv: Path(sys.executable))
    from irisecho_core.app import App

    engine = App().engines["comfy"]
    engine.src.mkdir(parents=True)
    return engine


def start(engine, program: str, cancel_after: float | None = None):
    (engine.src / "main.py").write_text(program, encoding="utf-8")
    said = []

    async def go():
        cancelled = asyncio.Event()
        if cancel_after is not None:
            asyncio.get_running_loop().call_later(cancel_after, cancelled.set)
        await engine.ensure_running(lambda progress, message: said.append(message), cancelled)

    return go, said


def test_an_engine_that_hangs_is_stopped_and_reported(engine, monkeypatch):
    monkeypatch.setattr(comfy, "START_TIMEOUT", 2)
    go, said = start(engine, SLEEPS)
    with pytest.raises(EngineStartError, match="did not start"):
        asyncio.run(go())
    assert not engine.alive and engine.state == "idle"
    assert said[0] == "Starting the image engine"


def test_an_engine_that_crashes_says_what_it_printed(engine):
    go, _ = start(engine, CRASHES)
    with pytest.raises(EngineStartError, match="--frobnicate"):
        asyncio.run(go())
    assert not engine.alive


def test_a_start_can_be_cancelled(engine):
    go, _ = start(engine, SLEEPS, cancel_after=0.3)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())
    assert not engine.alive and engine.state == "idle"
