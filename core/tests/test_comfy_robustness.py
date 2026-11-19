# SPDX-License-Identifier: AGPL-3.0-or-later
"""The image engine going away mid-job, a cancel that lands too early, and what a failed
job leaves in ComfyUI's folders. No ComfyUI: its websocket and HTTP API are stood in for."""

import asyncio
import json
from collections import deque
from types import SimpleNamespace

import httpx
import pytest
import websockets

from irisecho_core.engines import comfy
from irisecho_core.engines.comfy import ComfyEngine

OOM = "torch.OutOfMemoryError: Allocation on device"


class Socket:
    """A websocket that says what it is given, then ends, or fails with `then`."""

    def __init__(self, messages, then=None):
        self.messages, self.then = list(messages), then

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return json.dumps(self.messages.pop(0))
        if self.then:
            raise self.then
        raise StopAsyncIteration


class Process:
    def __init__(self, returncode=None):
        self.returncode = returncode

    async def wait(self):
        return self.returncode


@pytest.fixture
def engine(tmp_path):
    engine = ComfyEngine.__new__(ComfyEngine)
    engine.io, engine.state, engine.port = tmp_path / "io", "ready", 1
    engine.proc, engine._log_task = Process(), None
    engine.prompt_of, engine.log_tail = {}, deque(maxlen=80)
    engine.dynamic_vram = True
    # Stand-ins for starting and stopping the process. A restarted engine dies again
    # when the test sets engine.dies_again, as a dead one does in the tests below.
    engine.started, engine.stopped, engine.dies_again = [], 0, True

    async def ensure_running(report, cancelled=None, dynamic_vram=False):
        engine.started.append(dynamic_vram)
        engine.proc = Process()
        if engine.dies_again:
            dies(engine)

    async def stop():
        engine.stopped += 1
        engine.proc = None

    engine.ensure_running, engine.stop = ensure_running, stop
    return engine


@pytest.fixture
def api(monkeypatch):
    """Stand in for ComfyUI's HTTP API: api(handler) answers every request with handler."""
    real = httpx.AsyncClient

    def use(handler):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
        )

    return use


def accepted(request):
    return httpx.Response(200, json={"prompt_id": "p1"})


def context(job_id="j1"):
    return SimpleNamespace(
        job_id=job_id,
        cancelled=asyncio.Event(),
        report=lambda progress, message: None,
        model=SimpleNamespace(name="A model"),
    )


def dies(engine):
    """The engine process ends, having printed why."""
    engine.proc.returncode = 137
    engine.log_tail.append(OOM)


STARTED = {"type": "executing", "data": {"node": "sample", "prompt_id": "p1"}}


def test_an_engine_that_dies_mid_job_says_what_it_printed(engine, api, monkeypatch):
    api(accepted)
    closed = websockets.ConnectionClosedError(None, None)  # "no close frame received or sent"
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: Socket([STARTED], closed))
    dies(engine)
    with pytest.raises(RuntimeError, match="stopped in the middle of the job") as caught:
        asyncio.run(engine._execute(context(), {}))
    assert OOM in str(caught.value) and "close frame" not in str(caught.value)
    assert engine.prompt_of == {}


def test_a_connection_that_just_ends_is_not_taken_for_a_finished_job(engine, api, monkeypatch):
    asked = []

    def handler(request):
        asked.append(request.url.path)
        return accepted(request)

    api(handler)
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: Socket([STARTED]))
    dies(engine)
    with pytest.raises(RuntimeError, match=OOM):
        asyncio.run(engine._execute(context(), {}))
    # Sent, and sent once more to a fresh engine; never asking a dead one for its history.
    assert asked == ["/prompt", "/prompt"]


def test_an_engine_that_is_gone_before_the_job_is_sent(engine, api, monkeypatch):
    def refused(request):
        raise httpx.ConnectError("All connection attempts failed")

    api(refused)
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: Socket([]))
    dies(engine)
    with pytest.raises(RuntimeError, match="stopped in the middle of the job") as caught:
        asyncio.run(engine._execute(context(), {}))
    assert OOM in str(caught.value)


def test_an_answer_that_is_not_json_is_a_rejected_job(engine, api, monkeypatch):
    api(lambda request: httpx.Response(500, text="Internal Server Error"))
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: Socket([]))
    with pytest.raises(RuntimeError, match="rejected the job: Internal Server Error"):
        asyncio.run(engine._execute(context(), {}))


def test_a_job_that_finishes_still_returns_what_it_saved(engine, api, monkeypatch):
    saved = {"save": {"images": [{"filename": "j1_00001_.png"}]}}

    def handler(request):
        if request.url.path == "/history/p1":
            done = {"status": {"status_str": "success"}, "outputs": saved}
            return httpx.Response(200, json={"p1": done})
        return accepted(request)

    api(handler)
    done = {"type": "execution_success", "data": {"prompt_id": "p1"}}
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: Socket([STARTED, done]))
    assert asyncio.run(engine._execute(context(), {})) == saved


SAVED = {"save": {"images": [{"filename": "j1_00001_.png"}]}}
DONE = {"type": "execution_success", "data": {"prompt_id": "p1"}}
CUDA_OOM = {
    "type": "execution_error",
    "data": {
        "prompt_id": "p1",
        "exception_type": "torch.AcceleratorError",
        "exception_message": "CUDA error: out of memory\nSearch for `cudaErrorMemoryAllocation'",
    },
}


def finishes(request):
    if request.url.path == "/history/p1":
        done = {"status": {"status_str": "success"}, "outputs": SAVED}
        return httpx.Response(200, json={"p1": done})
    return accepted(request)


def sockets(monkeypatch, *runs):
    """One websocket per attempt: each a list of messages, ending as Socket ends them."""
    queue = list(runs)
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: queue.pop(0))
    return queue


def test_an_engine_that_dies_mid_job_runs_it_again_on_a_fresh_one(engine, api, monkeypatch):
    api(finishes)
    closed = websockets.ConnectionClosedError(None, None)
    left = sockets(monkeypatch, Socket([STARTED], closed), Socket([STARTED, DONE]))
    dies(engine)
    engine.dies_again = False
    told = []
    ctx = context()
    ctx.report = lambda progress, message: told.append(message)
    assert asyncio.run(engine._execute(ctx, {})) == SAVED
    assert left == [] and engine.started == [True]  # in the memory mode it ran in
    assert "The image engine stopped; starting it again" in told


def test_out_of_memory_runs_the_job_again_on_a_fresh_engine(engine, api, monkeypatch):
    api(finishes)
    engine.dies_again = False
    sockets(monkeypatch, Socket([STARTED, CUDA_OOM]), Socket([STARTED, DONE]))
    assert asyncio.run(engine._execute(context(), {})) == SAVED
    assert engine.stopped == 1 and engine.started == [True]


def test_out_of_memory_twice_says_so_plainly(engine, api, monkeypatch):
    api(finishes)
    sockets(monkeypatch, Socket([STARTED, CUDA_OOM]), Socket([STARTED, CUDA_OOM]))
    with pytest.raises(RuntimeError, match="Ran out of graphics memory, also after") as caught:
        asyncio.run(engine._execute(context(), {}))
    assert "CUDA error: out of memory" in str(caught.value)
    assert "cudaErrorMemoryAllocation" not in str(caught.value)  # the first line only
    assert engine.started == [True]  # one fresh start, not more


def test_a_cancelled_job_is_not_run_again(engine, api, monkeypatch):
    api(finishes)
    sockets(monkeypatch, Socket([STARTED, CUDA_OOM]), Socket([STARTED, DONE]))
    ctx = context()
    ctx.cancelled.set()
    with pytest.raises(RuntimeError, match="Ran out of graphics memory"):
        asyncio.run(engine._execute(ctx, {}))
    assert engine.started == []


def test_an_engine_that_is_still_running_is_not_restarted(engine, api, monkeypatch):
    """A connection lost to a live engine is not a crash: nothing is run twice."""
    monkeypatch.setattr(asyncio, "wait_for", _times_out)
    api(finishes)
    closed = websockets.ConnectionClosedError(None, None)
    sockets(monkeypatch, Socket([STARTED], closed), Socket([STARTED, DONE]))
    with pytest.raises(RuntimeError, match="stopped answering"):
        asyncio.run(engine._execute(context(), {}))
    assert engine.started == []


async def _times_out(awaitable, timeout):
    if asyncio.iscoroutine(awaitable):
        awaitable.close()
    raise TimeoutError


def test_other_errors_are_not_taken_for_memory():
    assert comfy.memory_error({"exception_message": "CUDA error: out of memory"})
    assert comfy.memory_error({"exception_type": "torch.OutOfMemoryError"})
    assert comfy.memory_error(
        {"exception_message": "Allocation on device 0 would exceed allowed memory"}
    )
    assert not comfy.memory_error({"exception_message": "mat1 and mat2 shapes cannot be"})
    assert not comfy.memory_error({})


def test_3d_steps_down_on_its_own_without_a_restart(engine, api, monkeypatch):
    api(finishes)
    sockets(monkeypatch, Socket([STARTED, CUDA_OOM]))
    with pytest.raises(RuntimeError, match="CUDA error: out of memory") as caught:
        asyncio.run(engine._execute(context(), {}, retry_oom=False))
    assert comfy.out_of_memory(caught.value)  # what _run_model3d looks for
    assert engine.started == [] and engine.stopped == 0


def test_a_cancel_that_missed_a_starting_prompt_is_sent_again(engine, api, monkeypatch):
    monkeypatch.setattr(comfy, "CANCEL_CHECK_EVERY", 0.01)
    engine.prompt_of["j1"] = "p1"
    running = [[[0, "p1", {}]], [[0, "p1", {}]], []]  # what /queue shows, look after look
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"queue_running": running.pop(0), "queue_pending": []})
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    api(handler)
    asyncio.run(engine.cancel("j1"))
    interrupt = ("/interrupt", {"prompt_id": "p1"})
    assert sent == [("/queue", {"delete": ["p1"]}), interrupt, interrupt, interrupt]
    assert running == []


def test_a_cancel_stops_asking_once_the_job_has_ended(engine, api, monkeypatch):
    monkeypatch.setattr(comfy, "CANCEL_CHECK_EVERY", 0.01)
    engine.prompt_of["j1"] = "p1"
    sent = []

    def handler(request):
        sent.append(request.url.path)
        engine.prompt_of.clear()  # as when the interrupt ended the job
        return httpx.Response(200, json={})

    api(handler)
    asyncio.run(engine.cancel("j1"))
    assert sent == ["/queue", "/interrupt"]


def test_a_failed_job_leaves_nothing_of_its_own_behind(engine):
    inputs, saved = engine.io / "input", engine.io / "output" / "irisecho"
    mine = [
        inputs / "irisecho_j1_image1.png",
        inputs / "irisecho_j1_mask.png",
        saved / "j1_00001_.mp4",
        saved / "j1-0-front_00001_.png",
        saved / "j1-thumb_00001_.png",
    ]
    others = [
        inputs / "irisecho_j10_image1.png",  # another job's, whose id only starts the same
        inputs / "a-picture.png",
        saved / "j10_00001_.png",
        engine.io / "output" / "j1_00001_.png",
    ]

    async def fails(ctx):
        for path in mine + others:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        raise RuntimeError("The image engine failed.")

    engine._run = fails
    with pytest.raises(RuntimeError, match="engine failed"):
        asyncio.run(engine.run(context("j1")))
    assert not any(path.exists() for path in mine)
    assert all(path.exists() for path in others)


def watchdog(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.delenv("IRISECHO_PARENT_PID", raising=False)  # no thread, no hook
    path = Path(comfy.__file__).parent / "watchdog_node.py"
    spec = importlib.util.spec_from_file_location("irisecho_watchdog_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_watchdog_ends_the_engine_when_comfyuis_job_thread_dies(monkeypatch, capsys):
    module = watchdog(monkeypatch)
    ended = []
    monkeypatch.setattr(module.os, "_exit", ended.append)
    monkeypatch.setattr(module, "_previous_hook", lambda args: None)
    worker = SimpleNamespace(name="Thread-1 (prompt_worker)")
    other = SimpleNamespace(name="Thread-3 (download)")
    module._on_thread_exception(SimpleNamespace(thread=other, exc_type=RuntimeError))
    assert ended == []
    module._on_thread_exception(SimpleNamespace(thread=worker, exc_type=RuntimeError))
    assert ended == [module.WORKER_DIED]
    assert "job thread stopped with RuntimeError" in capsys.readouterr().out
