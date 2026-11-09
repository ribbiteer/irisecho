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
import sys
import time

time.sleep(float(os.environ.get("STUB_START_DELAY", "0")))

from irisecho_worker import serve


def echo(params, reporter):
    return params


def hang(params, reporter):
    reporter.progress(0.1, "in the handler")
    time.sleep(60)
    return {}


def quiet(params, reporter):
    print("stuck while loading", file=sys.stderr, flush=True)
    time.sleep(60)
    return {}


def slow(params, reporter):
    for i in range(6):
        time.sleep(0.2)
        reporter.progress(i / 6, "working")
    return {"done": True}


def die(params, reporter):
    print("CUDA out of memory", file=sys.stderr, flush=True)
    os._exit(3)


def huge(params, reporter):
    return {"data": "x" * (5 << 20)}  # one line, longer than the core reads


def chatty(params, reporter):
    for _ in range(2000):
        print("y" * 500, file=sys.stderr)  # a megabyte: far more than a pipe holds
    print("the last line", file=sys.stderr, flush=True)
    return {"done": True}


serve(
    {
        "echo": echo,
        "hang": hang,
        "quiet": quiet,
        "slow": slow,
        "die": die,
        "huge": huge,
        "chatty": chatty,
    }
)
"""


@pytest.fixture
def script(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    path = tmp_path / "stub_worker.py"
    path.write_text(STUB, encoding="utf-8")
    return path


def process(script, delay: float = 0, **options):
    from irisecho_core.engines.worker import WorkerProcess

    return WorkerProcess(
        "stub", Path(sys.executable), script, {"STUB_START_DELAY": str(delay)}, **options
    )


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


def test_a_worker_that_reports_nothing_is_stopped_and_quoted(script):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        worker = process(script, idle_timeout=0.5)
        await worker.start()
        with pytest.raises(WorkerError, match="reported nothing") as caught:
            await worker.call("quiet", {})
        assert "stuck while loading" in str(caught.value)
        assert not worker.alive

    run(scene)


def test_a_worker_that_keeps_reporting_is_left_alone(script):
    async def scene():
        worker = process(script, idle_timeout=0.8)
        await worker.start()
        try:  # 1.2 s of work, never 0.8 s without a word
            assert await worker.call("slow", {}, lambda p, m: None) == {"done": True}
        finally:
            await worker.stop()

    run(scene)


def test_the_limit_is_generous_and_an_engine_can_set_its_own(script):
    from irisecho_core.engines import worker

    assert worker.CALL_IDLE_TIMEOUT >= 20 * 60
    assert process(script).idle_timeout == worker.CALL_IDLE_TIMEOUT
    assert worker.WorkerEngine.idle_timeout == worker.CALL_IDLE_TIMEOUT


def test_a_worker_that_dies_inside_a_call_says_what_it_last_printed(script):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        worker = process(script)
        await worker.start()
        with pytest.raises(WorkerError, match="stopped unexpectedly") as caught:
            await worker.call("die", {})
        assert "CUDA out of memory" in str(caught.value)

    run(scene)


def test_writing_to_a_dead_worker_is_not_a_broken_pipe(script):
    from irisecho_core.engines.worker import WorkerError

    def broken(data):
        raise BrokenPipeError(32, "Broken pipe")

    async def scene():
        worker = process(script)
        await worker.start()
        worker.proc.kill()  # not yet noticed, so the call goes on to write
        worker.proc.stdin = SimpleNamespace(write=broken)
        with pytest.raises(WorkerError, match="stub stopped unexpectedly"):
            await worker.call("echo", {})
        assert not worker.alive and not worker._pending

    run(scene)


def test_a_line_too_long_to_read_fails_the_call_and_ends_the_worker(script):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        worker = process(script)
        await worker.start()
        with pytest.raises(WorkerError, match="could not be read"):
            await worker.call("huge", {})
        assert not worker.alive  # nothing would read its next reply
        await worker.stop()

    run(scene)


def test_a_log_that_cannot_be_written_does_not_stop_the_worker(script, tmp_path):
    async def scene():
        worker = process(script)
        worker.log_path = tmp_path / "a-folder-where-the-log-should-be"
        worker.log_path.mkdir()
        await worker.start()
        try:
            # Undrained, the worker would block on its own printing and never answer.
            assert await worker.call("chatty", {}) == {"done": True}
            assert "the last line" in worker.stderr_tail
        finally:
            await worker.stop()

    run(scene)


def test_a_log_starts_over_at_its_limit_and_keeps_the_one_before(tmp_path):
    from irisecho_core.engines.base import EngineLog

    path = tmp_path / "engine.log"
    log = EngineLog(path, limit=1000)
    for i in range(200):
        log.write(f"line {i:04d} " + "z" * 40)
    log.close()
    assert path.stat().st_size <= 1100
    assert path.with_name("engine.log.1").stat().st_size <= 1100
    assert "line 0199" in path.read_text(encoding="utf-8")


def test_an_output_line_too_long_for_the_reader_does_not_end_the_drain():
    from irisecho_core.engines.base import output_lines

    async def scene():
        stream = asyncio.StreamReader(limit=64)
        stream.feed_data(b"one\n" + b"x" * 500 + b"\ntwo\n")
        stream.feed_eof()
        return [line async for line in output_lines(stream)]

    lines = run(scene)
    assert lines[0] == "one" and lines[-1] == "two"


# --- the device a worker loads on ----------------------------------------------------------------


@pytest.fixture
def torch_device(monkeypatch):
    """The workers' shared helper, with a stand-in torch whose CUDA can be switched off."""
    from irisecho_core.engines.worker import PROTOCOL_DIR

    monkeypatch.syspath_prepend(str(PROTOCOL_DIR))
    import irisecho_worker

    def with_cuda(available: bool):
        cuda = SimpleNamespace(is_available=lambda: available)
        monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda, __version__="0"))
        return irisecho_worker.torch_device

    return with_cuda


def test_cuda_that_was_asked_for_and_is_missing_is_an_error(torch_device):
    with pytest.raises(RuntimeError, match="cannot use the graphics card"):
        torch_device(False)("cuda")


def test_the_device_asked_for_is_the_device_used(torch_device):
    assert torch_device(True)("cuda") == "cuda"
    # A machine without an NVIDIA card is never asked for CUDA, and runs as before.
    assert torch_device(False)("cpu") == "cpu"
    assert torch_device(False)("mps") == "mps"


@pytest.mark.parametrize("name", ["kokoro", "chatterbox", "promptwriter", "stableaudio"])
def test_no_worker_falls_back_to_the_processor_on_its_own(name):
    from irisecho_core.engines.worker import PROTOCOL_DIR

    source = (PROTOCOL_DIR.parent / name / "worker.py").read_text(encoding="utf-8")
    assert 'torch_device(params["device"])' in source
    assert 'device = "cpu"' not in source


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


def test_a_job_whose_worker_goes_silent_fails_and_frees_the_engine(engine):
    from irisecho_core.engines.worker import WorkerError

    async def scene():
        engine.idle_timeout = 0.5
        ctx, _ = context()
        with pytest.raises(WorkerError, match="reported nothing"):
            await engine.run(ctx)
        assert engine.state == "idle" and not engine.worker.alive

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
