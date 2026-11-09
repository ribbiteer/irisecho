# SPDX-License-Identifier: AGPL-3.0-or-later
"""One IrisEcho at a time on a data folder.

Whoever runs the engines (the app's server, or a command working on its own)
holds `<data>/irisecho.lock` for as long as it lives. The lock is the operating
system's, on an open file: it goes when the process goes, however that happens,
so it is never left behind the way a pid file is.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LOCK_NAME = "irisecho.lock"


class InstanceRunning(RuntimeError):
    """Another IrisEcho holds this data folder."""

    def __init__(self, folder: Path):
        super().__init__(
            f"IrisEcho is already running on this data folder ({folder}). "
            "Use that one, or close it first."
        )
        self.folder = folder


def _try_lock(fd: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            # One byte, far past anything a file holds. Held by the handle, so a
            # second one is refused even inside the same process.
            os.lseek(fd, 1 << 30, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


class InstanceLock:
    def __init__(self, fd: int, folder: Path):
        self._fd: int | None = fd
        self.folder = folder

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # the lock goes with the handle
            self._fd = None


def acquire(folder: Path, patience: float = 0.5) -> InstanceLock:
    """Take the lock on `folder`, or raise InstanceRunning.

    `patience` covers another process that is only looking (see `held`)."""
    deadline = time.monotonic() + patience
    while True:
        # Not inherited by the programs started from here: an engine left behind
        # must not keep the folder locked.
        fd = os.open(folder / LOCK_NAME, os.O_RDWR | os.O_CREAT)
        if _try_lock(fd):
            return InstanceLock(fd, folder)
        os.close(fd)
        if time.monotonic() >= deadline:
            raise InstanceRunning(folder)
        time.sleep(0.05)


def held(folder: Path) -> bool:
    """Whether some process holds the lock on `folder` right now."""
    try:
        acquire(folder, patience=0).release()
    except InstanceRunning:
        return True
    except OSError:
        return False
    return False
