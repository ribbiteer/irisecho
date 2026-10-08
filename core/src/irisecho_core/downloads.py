# SPDX-License-Identifier: AGPL-3.0-or-later
"""Download weight files: resumable, hash-verified, one host token at most.

States a file moves through:

    queued -> downloading -> verifying -> done
                          \\-> error | needs_token | needs_access | cancelled

`needs_token` means the host wants you signed in (a gated model and no token);
`needs_access` means the token works but you have not accepted that model's
terms on the host yet. Both are fixed by the person, not by retrying.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from irisecho_core import credentials
from irisecho_core.events import EventBus, Throttle
from irisecho_core.registry import FileSpec
from irisecho_core.store import Hasher, Store

CHUNK = 1 << 20
PARALLEL = 2
USER_AGENT = "IrisEcho (+https://github.com/ribbiteer/irisecho)"


@dataclass
class Download:
    id: str
    size: int
    state: str = "queued"
    done: int = 0
    speed: float = 0.0  # bytes/s
    error: str = ""
    page: str = ""
    _task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {
            "id": self.id,
            "size": self.size,
            "state": self.state,
            "done": self.done,
            "speed": round(self.speed),
            "error": self.error,
            "page": self.page,
        }


class DownloadManager:
    def __init__(self, store: Store, bus: EventBus):
        self.store = store
        self.bus = bus
        self.items: dict[str, Download] = {}
        self._sem = asyncio.Semaphore(PARALLEL)

    def snapshot(self) -> list[dict]:
        return [d.public() for d in self.items.values()]

    def _emit(self, d: Download) -> None:
        self.bus.publish("download", d.public())

    def request(self, specs: list[FileSpec]) -> list[Download]:
        started = []
        for spec in specs:
            if self.store.locate(spec):
                continue
            current = self.items.get(spec.id)
            if current and current.state in ("queued", "downloading", "verifying"):
                started.append(current)
                continue
            d = Download(id=spec.id, size=spec.size, page=spec.page)
            self.items[spec.id] = d
            if not spec.pinned:
                # Nothing to check the file against: say so now, not after a long download.
                d.state = "error"
                d.error = "IrisEcho has no usable record of this file. Update IrisEcho."
                self._emit(d)
                started.append(d)
                continue
            d._task = asyncio.create_task(self._run(spec, d))
            self._emit(d)
            started.append(d)
        return started

    def cancel(self, file_id: str) -> bool:
        d = self.items.get(file_id)
        if d and d._task and not d._task.done():
            d._task.cancel()
            return True
        return False

    async def move_to(self, root: Path) -> None:
        """Download into another models folder from now on.

        A download already running would finish in the old folder, leaving the
        new one short of that file with nothing fetching it, so running downloads
        are stopped, their partial files removed, and they start again in `root`.
        """
        running = [d for d in self.items.values() if d._task and not d._task.done()]
        for d in running:
            d._task.cancel()
        await asyncio.gather(*[d._task for d in running], return_exceptions=True)
        specs = [self.store.registry.files[d.id] for d in running]
        for spec in specs:
            with contextlib.suppress(OSError):  # still open in a hashing thread: left behind
                self.store.part_path(spec).unlink(missing_ok=True)
        self.store.root = root
        self.request(specs)

    async def wait(self, specs: list[FileSpec]) -> list[Download]:
        tasks = [self.items[s.id]._task for s in specs if s.id in self.items]
        await asyncio.gather(*[t for t in tasks if t], return_exceptions=True)
        return [self.items[s.id] for s in specs if s.id in self.items]

    async def _run(self, spec: FileSpec, d: Download) -> None:
        try:
            async with self._sem:
                await self._fetch(spec, d)
        except asyncio.CancelledError:
            d.state = "cancelled"
            self._emit(d)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 401:
                d.state = "needs_token"
                d.error = "This model needs a Hugging Face account token."
            elif code == 403:
                d.state = "needs_access"
                d.error = "Accept this model's terms on its Hugging Face page, then retry."
            else:
                d.state = "error"
                d.error = f"The host answered {code}."
            self._emit(d)
        except Exception as e:  # network errors, disk full, hash mismatch
            d.state = "error"
            d.error = str(e) or type(e).__name__
            self._emit(d)

    async def _fetch(self, spec: FileSpec, d: Download) -> None:
        final = self.store.own_path(spec)
        part = self.store.part_path(spec)
        part.parent.mkdir(parents=True, exist_ok=True)

        digest = Hasher(spec)
        offset = part.stat().st_size if part.exists() else 0
        if offset > spec.size:
            part.unlink()
            offset = 0
        if offset:
            d.state = "verifying"
            self._emit(d)
            await asyncio.to_thread(_hash_into, part, digest)

        headers = {"User-Agent": USER_AGENT}
        token = credentials.get()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if offset:
            headers["Range"] = f"bytes={offset}-"

        d.state = "downloading"
        d.done = offset
        self._emit(d)
        throttle = Throttle(0.3)
        window_start, window_bytes = time.monotonic(), 0

        # httpx drops the Authorization header when a redirect leaves the host.
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(30, read=120)
        ) as client:
            async with client.stream("GET", spec.url, headers=headers) as resp:
                resp.raise_for_status()
                if offset and resp.status_code != 206:
                    # The server ignored the range; start over.
                    offset, digest = 0, Hasher(spec)
                    d.done = 0
                mode = "ab" if offset else "wb"
                with open(part, mode) as f:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        f.write(chunk)
                        digest.update(chunk)
                        d.done += len(chunk)
                        window_bytes += len(chunk)
                        now = time.monotonic()
                        if now - window_start >= 1.0:
                            d.speed = window_bytes / (now - window_start)
                            window_start, window_bytes = now, 0
                        if throttle.ready():
                            self._emit(d)

        if d.done != spec.size:
            raise OSError(f"download stopped at {d.done} of {spec.size} bytes; retry to resume")
        d.state = "verifying"
        self._emit(d)
        if not digest.ok():
            part.unlink(missing_ok=True)
            raise ValueError("the file did not match its expected hash and was discarded")
        os.replace(part, final)
        d.state = "done"
        d.speed = 0
        self._emit(d)


def _hash_into(path, digest) -> None:
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            digest.update(chunk)
