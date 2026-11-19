# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installed into IrisEcho's private ComfyUI as custom_nodes/irisecho_watchdog.

Ends ComfyUI as soon as the IrisEcho process that started it is gone, so a
crashed or force-quit IrisEcho never leaves a GPU-holding ComfyUI behind.

Ends it too when ComfyUI's job thread (prompt_worker) dies. ComfyUI's web server
lives on without it: jobs are accepted and never run, while the loaded models keep
their memory. It happened on 2026-11-19 after a CUDA out-of-memory in an LTX-2.5
queue: ComfyUI's own recovery (unload_all_models) raised again inside its handler,
and the exception left the thread. Ending the process lets IrisEcho see the engine
go, start a fresh one and run the job again. Defines no nodes.
"""

import os
import sys
import threading
import time

NODE_CLASS_MAPPINGS = {}

# The exit code IrisEcho reads as "the job thread died" (EX_SOFTWARE).
WORKER_DIED = 70


def is_worker(thread) -> bool:
    return thread is not None and "prompt_worker" in (thread.name or "")


def _end(why: str) -> None:
    print(f"IrisEcho: {why}; ending the engine so a fresh one can take over.", flush=True)
    sys.stderr.flush()
    os._exit(WORKER_DIED)


_previous_hook = threading.excepthook


def _on_thread_exception(args) -> None:
    _previous_hook(args)  # the traceback goes to the log first
    if is_worker(args.thread):
        _end(f"ComfyUI's job thread stopped with {args.exc_type.__name__}")


def _watch(parent: int | None) -> None:
    import psutil

    seen_worker = False
    while True:
        time.sleep(2)
        if parent is not None and not psutil.pid_exists(parent):
            os._exit(0)
        alive = any(is_worker(t) for t in threading.enumerate())
        if alive:
            seen_worker = True
        elif seen_worker:
            # A thread can also end without an exception reaching the hook.
            _end("ComfyUI's job thread is gone")


_parent = os.environ.get("IRISECHO_PARENT_PID")
if _parent and _parent.isdigit():
    threading.excepthook = _on_thread_exception
    threading.Thread(
        target=_watch, args=(int(_parent),), daemon=True, name="irisecho-watchdog"
    ).start()
