# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core side of the worker protocol (see protocol/irisecho_worker.py)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path

from irisecho_core import hardware, paths
from irisecho_core.engines import uvenv
from irisecho_core.engines.base import (
    Engine,
    EngineLog,
    EngineStartError,
    RunContext,
    output_lines,
)

PROTOCOL_DIR = Path(__file__).parent / "protocol"
Progress = Callable[[float | None, str | None], None]
# A call whose worker has sent nothing, neither progress nor a reply, for this long
# is taken for hung. A healthy worker is silent for minutes while a large model loads
# from a slow disk or one long step runs, so this is far above that.
CALL_IDLE_TIMEOUT = 30 * 60.0


class WorkerError(RuntimeError):
    def __init__(self, message: str, trace: str = ""):
        super().__init__(message)
        self.trace = trace


class WorkerProcess:
    def __init__(
        self,
        name: str,
        python: Path,
        script: Path,
        env: dict[str, str] | None = None,
        idle_timeout: float = CALL_IDLE_TIMEOUT,
    ):
        self.name = name
        self.python = python
        self.script = script
        self.env = env or {}
        self.idle_timeout = idle_timeout
        self._heard = 0.0  # when the worker last sent a protocol line
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_tail: deque[str] = deque(maxlen=60)
        self._pending: dict[str, tuple[asyncio.Future, Progress | None]] = {}
        self._ready: asyncio.Future | None = None
        self._readers: list[asyncio.Task] = []
        self.log_path = paths.sub("logs") / f"{name}.log"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, timeout: float = 600) -> None:
        env = os.environ.copy()
        env.update(self.env)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(PROTOCOL_DIR), env.get("PYTHONPATH")])
        )
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Workers never fetch anything on their own; IrisEcho downloads and verifies.
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["DO_NOT_TRACK"] = "1"
        self._ready = asyncio.get_running_loop().create_future()
        self.proc = await asyncio.create_subprocess_exec(
            str(self.python),
            "-u",
            str(self.script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=1 << 22,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        self._readers = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]
        try:
            await asyncio.wait_for(self._ready, timeout)
        except Exception:
            await self.stop()
            raise

    async def _read_stdout(self) -> None:
        proc = self.proc
        assert proc and proc.stdout
        err: WorkerError | None = None
        try:
            async for raw in proc.stdout:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                self._heard = time.monotonic()
                event = msg.get("event")
                if event == "ready":
                    if self._ready and not self._ready.done():
                        self._ready.set_result(True)
                    continue
                entry = self._pending.get(msg.get("id", ""))
                if not entry:
                    continue
                fut, on_progress = entry
                if event == "progress" and on_progress:
                    on_progress(msg.get("progress"), msg.get("message"))
                elif event == "result" and not fut.done():
                    fut.set_result(msg.get("data") or {})
                elif event == "error" and not fut.done():
                    fut.set_exception(
                        WorkerError(msg.get("message", "worker error"), msg.get("trace", ""))
                    )
        except Exception as e:
            # For one, a line longer than the stream takes. The reply it carried is
            # lost and nothing reads the next one, so the worker cannot be kept.
            err = WorkerError(f"{self.name} sent something that could not be read ({e}).")
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        # The process ended: fail everything still waiting, once its last words are in.
        others = [t for t in self._readers if t is not asyncio.current_task()]
        if others:
            await asyncio.wait(others, timeout=2)
        err = err or WorkerError(self._exit_message())
        if self._ready and not self._ready.done():
            self._ready.set_exception(err)
        for fut, _ in self._pending.values():
            if not fut.done():
                fut.set_exception(err)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        log = EngineLog(self.log_path)
        try:
            async for line in output_lines(self.proc.stderr):
                self.stderr_tail.append(line)
                log.write(line)
        finally:
            log.close()

    def _tail(self) -> str:
        tail = [line for line in self.stderr_tail if line.strip()][-8:]
        return "\n" + "\n".join(tail) if tail else ""

    def _exit_message(self) -> str:
        return f"{self.name} stopped unexpectedly." + self._tail()

    async def call(self, op: str, params: dict, on_progress: Progress | None = None) -> dict:
        if not self.alive:
            raise WorkerError(f"{self.name} is not running")
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = (fut, on_progress)
        assert self.proc and self.proc.stdin
        self._heard = time.monotonic()
        try:
            try:
                self.proc.stdin.write(
                    (json.dumps({"id": rid, "op": op, "params": params}) + "\n").encode()
                )
                await self.proc.stdin.drain()
            except OSError as e:
                # The worker is gone: what it last said explains more than "Broken pipe".
                fut.cancel()
                if self._readers:
                    await asyncio.wait(self._readers, timeout=2)
                await self.stop()
                raise WorkerError(self._exit_message()) from e
            while not fut.done():
                quiet = time.monotonic() - self._heard
                if quiet >= self.idle_timeout:
                    fut.cancel()
                    await self.stop()
                    raise WorkerError(
                        f"{self.name} reported nothing for {int(self.idle_timeout // 60)} minutes"
                        " and was stopped." + self._tail()
                    )
                await asyncio.wait({fut}, timeout=self.idle_timeout - quiet)
            return fut.result()
        finally:
            self._pending.pop(rid, None)

    async def stop(self) -> None:
        if not self.proc:
            return
        if self.proc.returncode is None:
            # A worker that is starting or inside a call reads nothing until it is
            # done, so asking it to exit would only be five seconds of waiting.
            idle = not self._pending and (self._ready is None or self._ready.done())
            try:
                if idle:
                    assert self.proc.stdin
                    self.proc.stdin.write(b'{"op": "exit"}\n')
                    await self.proc.stdin.drain()
                    await asyncio.wait_for(self.proc.wait(), 5)
            except Exception:
                pass
            if self.proc.returncode is None:
                self.proc.kill()
                await self.proc.wait()
        for t in self._readers:
            t.cancel()
        # Not left to the stdout reader, which was just cancelled and may not have got there.
        err = WorkerError(self._exit_message())
        if self._ready and not self._ready.done():
            self._ready.set_exception(err)
        for fut, _ in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(err)
        self.proc = None


class WorkerEngine(Engine):
    """An engine that runs a Python worker script in its own environment."""

    python_version = "3.12"
    worker_script: Path
    # An engine whose worker is rightly silent for longer sets its own.
    idle_timeout = CALL_IDLE_TIMEOUT

    def __init__(self, hardware):
        super().__init__(hardware)
        self.venv = self.home / ".venv"
        self.worker: WorkerProcess | None = None
        self._lock = asyncio.Lock()
        self.running_job: str | None = None

    def worker_env(self) -> dict[str, str]:
        return {}

    async def ensure_worker(self, report: Progress) -> WorkerProcess:
        async with self._lock:
            if self.worker and self.worker.alive:
                return self.worker
            self.state = "starting"
            report(None, f"Starting {self.name}")
            self.worker = WorkerProcess(
                self.id,
                uvenv.venv_python(self.venv),
                self.worker_script,
                {**hardware.gpu_env(self.hw), **self.worker_env()},
                self.idle_timeout,
            )
            try:
                await self.worker.start()
            except Exception as e:
                self.state = "idle"
                why = str(e) or "It took too long."
                raise EngineStartError(f"{self.name} did not start.\n{why}") from e
            self.state = "ready"
            return self.worker

    async def call(self, ctx: RunContext, op: str, params: dict) -> dict:
        # Set before the worker starts, so a job can be cancelled while it is still starting.
        self.running_job = ctx.job_id
        try:
            worker = await self.ensure_worker(ctx.report)
            if ctx.cancelled.is_set():
                # Cancelled before this job was the running one, so `cancel` did nothing.
                raise asyncio.CancelledError
            self.state = "busy"
            try:
                return await worker.call(op, params, ctx.report)
            finally:
                self.state = "ready" if worker.alive else "idle"
        finally:
            self.running_job = None

    async def release(self) -> None:
        if self.worker:
            await self.worker.stop()
            self.worker = None
        self.state = "idle"

    async def cancel(self, job_id: str) -> None:
        # Worker jobs cannot be interrupted mid-call; the process is killed, which
        # frees the GPU at once.
        if self.running_job == job_id:
            await self.release()
