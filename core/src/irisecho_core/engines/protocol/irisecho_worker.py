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


def serve(handlers: dict[str, Handler]) -> None:
    _claim_stdout()
    send({"event": "ready", "pid": os.getpid()})
    for raw in sys.stdin:
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
