# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real WorkerProcess and WorkerEngine against a stand-in worker script."""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from irisecho_core.engines import uvenv

STUB = """
import os
import time

time.sleep(float(os.environ.get("STUB_START_DELAY", "0")))

from irisecho_worker import serve


def echo(params, reporter):
    return params


def hang(params, reporter):
    reporter.progress(0.1, "in the handler")
    time.sleep(60)
    return {}


serve({"echo": echo, "hang": hang})
"""


@pytest.fixture
def script(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    path = tmp_path / "stub_worker.py"
    path.write_text(STUB, encoding="utf-8")
    return path


def process(script, delay: float = 0):
    from irisecho_core.engines.worker import WorkerProcess

    return WorkerProcess("stub", Path(sys.executable), script, {"STUB_START_DELAY": str(delay)})


def run(scene):
    return asyncio.run(asyncio.wait_for(scene(), 30))


def test_an_idle_worker_answers_and_leaves_when_asked(script):
    async def scene():
        worker = process(script)
        await worker.start()
        assert await worker.call("echo", {"a": 1}) == {"a": 1}
        began = time.monotonic()
        await worker.stop()
        assert not worker.alive and time.monotonic() - began < 4

    run(scene)


def test_stopping_a_worker_inside_a_call_is_quick_and_fails_the_call(script):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        worker = process(script)
        await worker.start()
        inside = asyncio.Event()
        call = asyncio.create_task(worker.call("hang", {}, lambda p, m: inside.set()))
        await inside.wait()
        began = time.monotonic()
        await worker.stop()
        assert time.monotonic() - began < 3  # killed, not asked and waited for
        with pytest.raises(WorkerError):
            await call
        assert not worker.alive

    run(scene)


def test_stopping_a_worker_that_is_still_starting_ends_the_start(script):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        worker = process(script, delay=60)
        start = asyncio.create_task(worker.start())
        while worker.proc is None:
            await asyncio.sleep(0.01)
        began = time.monotonic()
        await worker.stop()
        assert time.monotonic() - began < 3
        with pytest.raises(WorkerError):
            await start

    run(scene)


def test_stop_fails_waiting_calls_itself(script):
    """Not only when the stdout reader happens to see the end of the stream first."""
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        worker = process(script)
        await worker.start()
        for reader in worker._readers:
            reader.cancel()
        await asyncio.gather(*worker._readers, return_exceptions=True)
        waiting = asyncio.get_running_loop().create_future()
        worker._pending["x"] = (waiting, None)
        await worker.stop()
        assert isinstance(waiting.exception(), WorkerError)

    run(scene)


@pytest.fixture
def engine(script, monkeypatch):
    from irisecho_core import hardware
    from irisecho_core.engines.worker import WorkerEngine

    monkeypatch.setattr(uvenv, "venv_python", lambda venv: Path(sys.executable))

    class StubEngine(WorkerEngine):
        id = "stub"
        name = "Stub"
        worker_script = script
        delay = 0.0

        def worker_env(self):
            return {"STUB_START_DELAY": str(self.delay)}

        async def install(self, log):
            pass

        async def run(self, ctx):
            return [await self.call(ctx, "hang", {})]

    return StubEngine(hardware.detect(""))


def context(job_id: str = "j1"):
    said = asyncio.Event()
    ctx = SimpleNamespace(
        job_id=job_id, cancelled=asyncio.Event(), report=lambda p, m: p and said.set()
    )
    return ctx, said


def test_cancelling_a_job_inside_a_call_ends_it_at_once(engine):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        ctx, inside = context()
        job = asyncio.create_task(engine.run(ctx))
        await inside.wait()
        began = time.monotonic()
        ctx.cancelled.set()
        await engine.cancel(ctx.job_id)
        with pytest.raises(WorkerError):
            await job
        assert time.monotonic() - began < 3
        assert engine.worker is None and engine.state == "idle"

    run(scene)


def test_cancelling_a_job_whose_engine_is_still_starting_ends_it_at_once(engine):
    from irisecho_core.engines.base import EngineStartError

    async def scene():
        engine.delay = 60.0
        ctx, _ = context()
        job = asyncio.create_task(engine.run(ctx))
        while not (engine.worker and engine.worker.proc):
            await asyncio.sleep(0.01)
        began = time.monotonic()
        ctx.cancelled.set()
        await engine.cancel(ctx.job_id)
        with pytest.raises(EngineStartError):
            await job
        assert time.monotonic() - began < 3
        assert engine.state == "idle" and engine.running_job is None

    run(scene)


def test_a_job_cancelled_before_the_engine_knew_of_it_is_not_sent_to_the_worker(engine):
    async def scene():
        ctx, inside = context()
        ctx.cancelled.set()  # as when the cancel came while the GPU was being freed
        try:
            with pytest.raises(asyncio.CancelledError):
                await engine.run(ctx)
            assert not inside.is_set() and engine.worker.alive
        finally:
            await engine.release()

    run(scene)
