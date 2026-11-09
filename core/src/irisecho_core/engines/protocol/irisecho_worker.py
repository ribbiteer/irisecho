# SPDX-License-Identifier: AGPL-3.0-or-later
"""Worker side of the engine protocol. Standard library only.

Engine workers run inside their own environments, where irisecho_core is not
installed; they import this one file instead. The protocol is JSON lines:

    core -> worker   {"id": "...", "op": "speak", "params": {...}}
    worker -> core   {"event": "ready"}
                     {"id": "...", "event": "progress", "progress": 0.4, "message": "..."}
                     {"id": "...", "event": "result", "data": {...}}
                     {"id": "...", "event": "error", "message": "...", "trace": "..."}

The worker's real stdout is reserved for the protocol. Anything a library
prints (or writes from C code) is redirected to stderr, which core logs.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from collections.abc import Callable

_out = None
_lock = threading.Lock()


def _claim_stdout() -> None:
    global _out
    if _out is not None:
        return
    fd = os.dup(1)
    os.dup2(2, 1)  # stray prints, including from C extensions, now go to stderr
    sys.stdout = sys.stderr
    _out = os.fdopen(fd, "w", encoding="utf-8", buffering=1)


def send(message: dict) -> None:
    with _lock:
        _out.write(json.dumps(message) + "\n")
        _out.flush()


class Reporter:
    def __init__(self, request_id: str):
        self.id = request_id

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        send({"id": self.id, "event": "progress", "progress": fraction, "message": message})


Handler = Callable[[dict, Reporter], dict]


def torch_device(asked: str) -> str:
    """The device to load a model on: the one IrisEcho asked for, or an error.

    IrisEcho asks for "cuda" only on a machine whose engines were installed for an
    NVIDIA card. If PyTorch cannot use the card there, running on the processor
    instead would take minutes to hours without a word about why."""
    import torch  # every worker's environment has it; this file is imported without it too

    if asked == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "This engine cannot use the graphics card: its PyTorch "
            f"({getattr(torch, '__version__', 'unknown')}) finds no CUDA device. "
            "Update the NVIDIA driver, or reinstall this engine."
        )
    return asked


def _requests():
    """The lines the core sends, read on a thread of their own.

    The core holds the other end of stdin for as long as it lives. A handler can
    run for minutes, and the loop below reads nothing meanwhile; this thread
    does, so a worker whose core is gone ends at once, not when its job does.

    The pipe is moved off stdin first, and stdin pointed at the null device:
    on Windows a program started from a handler hangs while starting if its
    stdin is a pipe that this thread is blocked reading.
    """
    lifeline = os.fdopen(os.dup(0), "r", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    inbox: queue.Queue[str | None] = queue.Queue()
    left = threading.Event()

    def read() -> None:
        try:
            for raw in lifeline:
                inbox.put(raw)
        except (OSError, ValueError):
            pass
        inbox.put(None)
        # An idle worker takes that and returns from `serve`; one inside a
        # handler cannot, and nobody is left to read what it would make.
        if not left.wait(2):
            os._exit(0)

    threading.Thread(target=read, daemon=True, name="irisecho-requests").start()
    while True:
        raw = inbox.get()
        if raw is None:
            left.set()
            return
        yield raw


def serve(handlers: dict[str, Handler]) -> None:
    _claim_stdout()
    send({"event": "ready", "pid": os.getpid()})
    for raw in _requests():
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except ValueError:
            continue
        rid = request.get("id", "")
        op = request.get("op")
        if op == "exit":
            send({"id": rid, "event": "result", "data": {}})
            break
        handler = handlers.get(op)
        if handler is None:
            send({"id": rid, "event": "error", "message": f"unknown op {op!r}"})
            continue
        try:
            data = handler(request.get("params") or {}, Reporter(rid))
            send({"id": rid, "event": "result", "data": data or {}})
        except Exception as e:
            send(
                {
                    "id": rid,
                    "event": "error",
                    "message": str(e) or type(e).__name__,
                    "trace": traceback.format_exc(),
                }
            )
