# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phone and LAN access: an opt-in second listener with paired devices.

Off by default. When switched on, a second HTTP listener accepts connections
from the local network. Nothing on it works until a device pairs:

* Settings shows a short-lived, single-use code (also as a QR code);
* the phone sends it to /pair and receives a device cookie;
* every API call on the LAN listener must carry a valid device cookie, and only
  a small allow-list of calls is open to devices at all (making things, not
  changing settings, tokens or folders);
* devices are listed in Settings and can be revoked, which also closes their
  live connections.

The normal loopback listener and its per-run session cookie are untouched, and
its cookie is worthless on the LAN listener. The LAN listener speaks plain
HTTP: anyone who can see the network traffic can see what the phone sees.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from irisecho_core import paths

DEVICE_COOKIE = "irisecho_device"
DEFAULT_PORT = 7789
CODE_TTL = 300  # seconds a pairing code stays valid
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 8
MAX_WRONG = 5  # wrong codes allowed per LOCKOUT window, across all callers
LOCKOUT = 60

# What a paired device may call. Everything else is for the computer itself.
DEVICE_ALLOWED = [
    ("GET", r"/api/bootstrap"),
    ("GET", r"/api/system"),
    ("GET", r"/api/models"),
    ("GET", r"/api/engines"),
    ("GET", r"/api/settings"),
    ("GET", r"/api/jobs"),
    ("POST", r"/api/jobs"),
    ("GET", r"/api/jobs/[0-9a-f]+"),
    ("DELETE", r"/api/jobs/[0-9a-f]+"),
    ("POST", r"/api/jobs/[0-9a-f]+/(cancel|favorite)"),
    ("GET", r"/api/jobs/[0-9a-f]+/outputs/\d+"),
    ("GET", r"/api/jobs/[0-9a-f]+/outputs/\d+/(preview|export)"),
    ("POST", r"/api/uploads"),
    ("GET", r"/api/uploads/[0-9a-f]+\.[a-z0-9]+"),
]
_ALLOWED = [(m, re.compile(p + "$")) for m, p in DEVICE_ALLOWED]


def device_may(method: str, path: str) -> bool:
    method = "GET" if method == "HEAD" else method
    return any(m == method and rx.match(path) for m, rx in _ALLOWED)


def host_ok(host_header: str) -> bool:
    """Hosts a LAN request may name: an address, a bare machine name or a .local name.

    A public DNS name has no business reaching this listener (DNS rebinding).
    """
    text = (host_header or "").strip().lower()
    host = text[1 : text.index("]")] if text.startswith("[") and "]" in text else text.split(":")[0]
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return "." not in host or host.endswith(".local")


def lan_addresses() -> list[str]:
    """This computer's private IPv4 addresses, the one used to reach the network first."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent  # identity-scan: allow
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for addr in found:
        ip = ipaddress.ip_address(addr)
        if (ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")) and not ip.is_loopback:
            if not ip.is_link_local and addr not in out:
                out.append(addr)
    return out


def device_name(user_agent: str) -> str:
    ua = user_agent or ""
    os_name = next(
        (
            n
            for key, n in (
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Android", "Android"),
                ("Windows", "Windows PC"),
                ("Macintosh", "Mac"),
                ("Linux", "Linux"),
            )
            if key in ua
        ),
        "Device",
    )
    browser = next(
        (
            n
            for key, n in (
                ("Edg/", "Edge"),
                ("Firefox/", "Firefox"),
                ("CriOS/", "Chrome"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if key in ua
        ),
        "",
    )
    return f"{os_name} · {browser}" if browser else os_name


@dataclass
class Device:
    id: str
    name: str
    token_hash: str
    created: float
    last_seen: float

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "last_seen": self.last_seen,
        }


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    """Paired devices, kept as JSON in the data folder (tokens only as hashes)."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.data_dir() / "devices.json"
        self.items: dict[str, Device] = {}
        self._touched: dict[str, float] = {}
        try:
            for raw in json.loads(self.path.read_text(encoding="utf-8")):
                self.items[raw["id"]] = Device(**raw)
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".devices-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([d.__dict__ for d in self.items.values()], f, indent=1)
        os.replace(tmp, self.path)
        if os.name != "nt":
            self.path.chmod(0o600)

    def add(self, name: str) -> tuple[Device, str]:
        token = secrets.token_urlsafe(32)
        now = time.time()
        device = Device(secrets.token_hex(6), name, _hash(token), now, now)
        self.items[device.id] = device
        self._save()
        return device, token

    def find(self, token: str | None) -> Device | None:
        if not token:
            return None
        digest = _hash(token)
        for device in self.items.values():
            if secrets.compare_digest(device.token_hash, digest):
                now = time.time()
                if now - self._touched.get(device.id, 0) > 30:
                    self._touched[device.id] = now
                    device.last_seen = now
                    self._save()
                return device
        return None

    def revoke(self, device_id: str) -> bool:
        if self.items.pop(device_id, None) is None:
            return False
        self._save()
        return True

    def public(self) -> list[dict]:
        return sorted((d.public() for d in self.items.values()), key=lambda d: -d["created"])


class PairingCodes:
    """Short-lived single-use codes, with a lockout against guessing."""

    def __init__(self) -> None:
        self.codes: dict[str, float] = {}
        self.wrong: list[float] = []

    def create(self) -> tuple[str, int]:
        self._expire()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        self.codes[code] = time.time() + CODE_TTL
        return code, CODE_TTL

    def _expire(self) -> None:
        now = time.time()
        self.codes = {c: t for c, t in self.codes.items() if t > now}
        self.wrong = [t for t in self.wrong if now - t < LOCKOUT]

    def locked(self) -> bool:
        self._expire()
        return len(self.wrong) >= MAX_WRONG

    def redeem(self, code: str) -> bool:
        self._expire()
        if self.locked():
            return False
        code = re.sub(r"[\s-]", "", code or "").upper()
        if code in self.codes:
            del self.codes[code]
            return True
        self.wrong.append(time.time())
        return False


class LanScope:
    """ASGI wrapper for the LAN listener: marks every request as coming from it.

    The mark lives in the ASGI scope, which a client cannot set.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["irisecho_lan"] = True
        await self.app(scope, receive, send)


class _QuietServer(uvicorn.Server):
    """A uvicorn server that shares the process, and its signals, with another."""

    def install_signal_handlers(self) -> None:
        pass

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class LanListener:
    """Starts and stops the LAN listener while the server keeps running."""

    def __init__(self, asgi) -> None:
        self.asgi = LanScope(asgi)
        self.server: _QuietServer | None = None
        self.task: asyncio.Task | None = None
        self.port: int | None = None
        self.error = ""

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def _bind(self) -> socket.socket:
        last: OSError | None = None
        for port in (DEFAULT_PORT, 0):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("0.0.0.0", port))  # noqa: S104 - the point of this listener
                sock.listen(64)
                sock.set_inheritable(True)
                return sock
            except OSError as e:
                sock.close()
                last = e
        raise last or OSError("no port available")

    async def start(self) -> None:
        if self.active:
            return
        self.error = ""
        try:
            sock = self._bind()
        except OSError as e:
            self.error = f"Could not open a network port: {e.strerror or e}"
            return
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(
            self.asgi,
            log_level="warning",
            lifespan="off",
            ws_ping_interval=None,
            access_log=False,
        )
        self.server = _QuietServer(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]))

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 5)
            if not self.task.done():
                self.task.cancel()
        self.server, self.task, self.port = None, None, None
