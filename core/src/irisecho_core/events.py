# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-process publish/subscribe for live updates (jobs, downloads, engines)."""

from __future__ import annotations

import asyncio
import threading
import time


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, type_: str, data: dict) -> None:
        """Safe to call from any thread."""
        event = {"type": type_, "time": time.time(), "data": data}
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client loses events rather than stalling everyone.
                pass


class Throttle:
    """Let an update through at most every `interval` seconds (plus the last one)."""

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self._last = 0.0

    def ready(self, force: bool = False) -> bool:
        now = time.monotonic()
        if force or now - self._last >= self.interval:
            self._last = now
            return True
        return False
