# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installed into IrisEcho's private ComfyUI as custom_nodes/irisecho_watchdog.

Ends ComfyUI as soon as the IrisEcho process that started it is gone, so a
crashed or force-quit IrisEcho never leaves a GPU-holding ComfyUI behind.
Defines no nodes.
"""

import os
import threading
import time

NODE_CLASS_MAPPINGS = {}


def _watch(parent: int) -> None:
    import psutil

    while True:
        time.sleep(2)
        if not psutil.pid_exists(parent):
            os._exit(0)


_parent = os.environ.get("IRISECHO_PARENT_PID")
if _parent and _parent.isdigit():
    threading.Thread(
        target=_watch, args=(int(_parent),), daemon=True, name="irisecho-watchdog"
    ).start()
