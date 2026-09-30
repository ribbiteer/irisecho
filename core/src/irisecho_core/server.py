# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local HTTP API and web UI.

Security model (local mode):

* binds to 127.0.0.1 only;
* rejects requests whose Host header is not this machine (blocks DNS rebinding);
* the page is served with a random per-run token in an HttpOnly, SameSite=Strict
  cookie, which every API call must carry;
* state-changing calls must also send the `X-IrisEcho` header, which a page on
  another origin cannot add without a CORS preflight this server never grants.

Phone and LAN access (lan.py) is a second, opt-in listener served by the same
app. Requests on it are marked in the ASGI scope; they need a paired device's
cookie instead, and may only make the calls on lan.DEVICE_ALLOWED.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import segno
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from irisecho_core import __version__, credentials, guide, lan, paths, settings
from irisecho_core.app import App, NotReady

WEB = Path(__file__).parent / "web"
COOKIE = "irisecho_session"
UPLOAD_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
}
MAX_UPLOAD = 64 << 20


class Guard(BaseHTTPMiddleware):
    def __init__(self, app, token: str, devices: lan.Devices):
        super().__init__(app)
        self.token = token
        self.devices = devices

    async def dispatch(self, request: Request, call_next):
        on_lan = bool(request.scope.get("irisecho_lan"))
        host_header = request.headers.get("host") or ""
        request.state.device = None
        if on_lan:
            if not lan.host_ok(host_header):
                return Response("Forbidden host", status_code=403)
            request.state.device = self.devices.find(request.cookies.get(lan.DEVICE_COOKIE))
        else:
            host = host_header.split(":")[0].strip("[]").lower()
            if host not in ("127.0.0.1", "localhost", "::1"):
                return Response("Forbidden host", status_code=403)
        path = request.url.path
        if path.startswith("/api/"):
            if on_lan:
                if request.state.device is None:
                    return JSONResponse(
                        {"error": "This device is not paired.", "pair": True}, status_code=401
                    )
                if not lan.device_may(request.method, path):
                    return JSONResponse(
                        {"error": "That can only be done on the computer running IrisEcho."},
                        status_code=403,
                    )
            elif request.cookies.get(COOKIE) != self.token:
                return JSONResponse({"error": "Reload the page to reconnect."}, status_code=401)
            if request.method not in ("GET", "HEAD") and request.headers.get("x-irisecho") != "1":
                return JSONResponse({"error": "missing request header"}, status_code=403)
        return await call_next(request)


def qr_svg(text: str) -> str:
    """A QR code as inline SVG that scales to whatever box it is put in.

    segno's inline SVG has a fixed width and height and no viewBox, so a page that
    resizes it crops the code instead of shrinking it. Four modules of quiet zone
    are what scanners expect.
    """
    svg = segno.make(text, error="m").svg_inline(scale=1, border=4, dark="#0E0B16", light="#ffffff")
    size = re.search(r'width="(\d+)" height="(\d+)"', svg)
    assert size
    return svg.replace(
        size.group(0),
        f'viewBox="0 0 {size.group(1)} {size.group(2)}" shape-rendering="crispEdges"',
        1,
    )


def pair_page(message: str = "", code: str = "") -> HTMLResponse:
    note = f'<p class="err">{message}</p>' if message else ""
    return HTMLResponse(
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Pair with IrisEcho</title><style>"
        "body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0E0B16;"
        "color:#F3F0FA;font:16px/1.5 system-ui,sans-serif}main{width:min(92vw,380px)}"
        "h1{font-size:26px;margin:0 0 6px}p{color:#F3F0FAB3;margin:0 0 18px}"
        "input,button{width:100%;box-sizing:border-box;font:inherit;padding:14px;border-radius:12px;"
        "border:1px solid #ffffff2e}input{background:#141020;color:inherit;letter-spacing:.25em;"
        "text-transform:uppercase;text-align:center;font-size:22px}"
        "button{margin-top:12px;background:#6B5BD6;color:#fff;border:0;font-weight:600}"
        ".err{color:#F7A1A9}small{display:block;margin-top:18px;color:#F3F0FA77}"
        "</style><main><h1>Pair this device</h1>"
        "<p>On the computer running IrisEcho, open Settings, then Phone and network access, "
        "and choose Pair a phone. Type the code shown there.</p>"
        f"{note}<form method=post action=/pair>"
        f"<input name=code value='{code}' autocomplete=off autocapitalize=characters "
        "maxlength=12 placeholder='CODE' aria-label='Pairing code' autofocus>"
        "<button>Pair</button></form>"
        "<small>This connection is not encrypted. Use it only on a network you trust.</small>"
        "</main></html>",
        headers={"Cache-Control": "no-store"},
        status_code=401 if message else 200,
    )


def create_app(app: App | None = None, token: str | None = None) -> FastAPI:
    core = app or App()
    session = token or secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await core.start()
        if core.settings.lan_enabled:
            await listener.start()
        yield
        await listener.stop()
        await core.stop()

    api = FastAPI(
        title="IrisEcho", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )
    devices = lan.Devices()
    codes = lan.PairingCodes()
    listener = lan.LanListener(api)
    device_sockets: dict[str, set[WebSocket]] = {}
    api.add_middleware(Guard, token=session, devices=devices)
    api.state.core = core
    api.state.token = session
    api.state.devices = devices
    api.state.listener = listener
    api.state.pairing = codes

    def not_ready(e: NotReady) -> JSONResponse:
        return JSONResponse({"error": str(e), "needs": e.needs}, status_code=409)

    # --- overview ----------------------------------------------------------

    def remote(request: Request) -> bool:
        return request.state.device is not None

    def public_settings(request: Request) -> dict:
        data = core.settings.public()
        if remote(request):  # a phone has no use for the computer's folder names
            for key in ("models_dir", "outputs_dir", "models_path", "outputs_path"):
                data[key] = ""
            data["linked_model_dirs"] = []
        return data

    def public_system(request: Request) -> dict:
        data = core.system()
        if remote(request):
            data["data_dir"] = ""
        return data

    def public_guide(request: Request) -> dict:
        if remote(request):  # the command line is for the computer itself
            return {"cli": "", "path": ""}
        return {"cli": guide.cli_command(), "path": str(paths.data_dir() / guide.NAME)}

    @api.get("/api/bootstrap")
    async def bootstrap(request: Request):
        return {
            "version": __version__,
            "remote": remote(request),
            "system": public_system(request),
            "settings": public_settings(request),
            "engines": core.engines_public(),
            "models": core.models(),
            "jobs": core.db.list(limit=80),
            "downloads": core.downloads.snapshot(),
            "scan": {"state": "scanning" if core.scanning else "idle"},
            "guide": public_guide(request),
            "credentials": {
                "huggingface": bool(credentials.get()),
                "keychain": credentials.available(),
            },
        }

    @api.get("/api/system")
    async def system(request: Request):
        return public_system(request)

    # --- models ------------------------------------------------------------

    @api.get("/api/models")
    async def models():
        return core.models()

    @api.post("/api/models/{model_id}/prepare")
    async def prepare(model_id: str):
        try:
            return core.prepare(model_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        except NotReady as e:
            return not_ready(e)

    @api.delete("/api/models/{model_id}/files")
    async def remove_model(model_id: str):
        try:
            return {"freed": core.remove_model(model_id)}
        except KeyError as e:
            raise HTTPException(404, str(e)) from None

    @api.post("/api/licenses/{license_id}/accept")
    async def accept(license_id: str):
        try:
            core.accept_license(license_id)
        except KeyError:
            raise HTTPException(404, "unknown license") from None
        return {"ok": True}

    @api.post("/api/downloads/{file_id:path}/cancel")
    async def cancel_download(file_id: str):
        return {"ok": core.downloads.cancel(file_id)}

    # --- engines -----------------------------------------------------------

    @api.get("/api/engines")
    async def engines():
        return core.engines_public()

    @api.post("/api/engines/{engine_id}/install")
    async def install(engine_id: str):
        if engine_id not in core.engines:
            raise HTTPException(404, "unknown engine")
        core.install_engine(engine_id)
        return {"ok": True}

    @api.get("/api/engines/{engine_id}/log")
    async def engine_log(engine_id: str):
        return {"lines": list(core.install_logs.get(engine_id, []))}

    # --- jobs --------------------------------------------------------------

    @api.get("/api/jobs")
    async def jobs(
        kind: str | None = None,
        favorite: bool | None = None,
        limit: int = 100,
        before: float | None = None,
        after: float | None = None,
        model: str | None = None,
        q: str | None = None,
    ):
        return core.db.list(
            kind=kind,
            favorite=favorite,
            limit=min(limit, 500),
            before=before,
            after=after,
            model=model,
            q=q,
        )

    @api.post("/api/jobs")
    async def submit(request: Request):
        body = await request.json()
        try:
            return core.submit(body["model"], body.get("params") or {})
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        except NotReady as e:
            return not_ready(e)

    @api.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        found = core.db.get(job_id)
        if not found:
            raise HTTPException(404, "no such job")
        return found

    @api.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        return {"ok": await core.cancel(job_id)}

    @api.post("/api/jobs/{job_id}/favorite")
    async def favorite(job_id: str, request: Request):
        body = await request.json()
        core.db.update(job_id, favorite=int(bool(body.get("favorite"))))
        core._emit_job(job_id)
        return {"ok": True}

    @api.delete("/api/jobs/{job_id}")
    async def delete(job_id: str):
        return {"ok": core.delete_job(job_id)}

    def output_path(job_id: str, n: int) -> Path:
        found = core.db.get(job_id)
        if not found or n >= len(found["outputs"]):
            raise HTTPException(404, "no such output")
        path = Path(found["outputs"][n]["path"])
        if not path.is_file():
            raise HTTPException(410, "the file has been moved or deleted")
        return path

    @api.get("/api/jobs/{job_id}/outputs/{n}")
    def output(job_id: str, n: int, download: bool = False):
        path = output_path(job_id, n)
        return FileResponse(
            path,
            filename=path.name if download else None,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @api.post("/api/jobs/{job_id}/outputs/{n}/reveal")
    def reveal(job_id: str, n: int):
        reveal_file(output_path(job_id, n))
        return {"ok": True}

    def reveal_file(path: Path) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    @api.post("/api/open/guide")
    def open_guide():
        """Show the guide for scripts and coding agents in the file manager."""
        reveal_file(guide.write())
        return {"ok": True}

    @api.post("/api/open/{target}")
    async def open_folder(target: str):
        folders = {
            "outputs": core.settings.outputs_path,
            "models": core.settings.models_path,
            "data": paths.data_dir(),
            "logs": paths.sub("logs"),
        }
        folder = folders.get(target)
        if folder is None:
            raise HTTPException(404, "unknown folder")
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opens a folder IrisEcho owns
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return {"ok": True}

    # --- uploads (reference images, voice samples) -------------------------

    @api.post("/api/uploads")
    async def upload(file: UploadFile):
        ext = UPLOAD_TYPES.get(file.content_type or "")
        if not ext:
            raise HTTPException(
                415, "Use a PNG, JPEG or WebP image, or a WAV, MP3, FLAC or OGG sound."
            )
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "That file is larger than 64 MB.")
        name = f"{uuid.uuid4().hex}{ext}"
        (paths.sub("uploads") / name).write_bytes(data)
        return {"id": name, "type": file.content_type, "size": len(data)}

    @api.get("/api/uploads/{name}")
    def get_upload(name: str):
        path = paths.sub("uploads") / Path(name).name
        if not path.is_file():
            raise HTTPException(404, "no such upload")
        return FileResponse(path)

    # --- settings ----------------------------------------------------------

    @api.get("/api/settings")
    async def get_settings(request: Request):
        return public_settings(request)

    @api.patch("/api/settings")
    async def patch_settings(request: Request):
        body = await request.json()
        allowed = {"outputs_dir", "models_dir", "strip_metadata"}
        changes = {k: v for k, v in body.items() if k in allowed}
        for key in ("outputs_dir", "models_dir"):
            if changes.get(key) and not Path(changes[key]).is_absolute():
                raise HTTPException(400, "Use a full folder path.")
        settings.update(core.settings, changes)
        if "models_dir" in changes:
            core.store.root = core.settings.models_path
            core.bus.publish("models", {})
        return core.settings.public()

    @api.post("/api/settings/linked-dirs")
    async def linked_dirs(request: Request):
        body = await request.json()
        dirs = [str(d) for d in body.get("dirs") or []]
        missing = [d for d in dirs if not Path(d).is_dir()]
        if missing:
            raise HTTPException(400, f"Folder not found: {missing[0]}")
        core.set_linked_dirs(dirs)
        return core.settings.public()

    @api.post("/api/settings/rescan")
    async def rescan():
        core.scan_links()
        return {"ok": True}

    @api.put("/api/credentials/huggingface")
    async def set_hf_token(request: Request):
        body = await request.json()
        token = (body.get("token") or "").strip()
        if not token.startswith("hf_"):
            raise HTTPException(400, "Hugging Face tokens start with hf_.")
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                "https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {token}"}
            )
        if r.status_code != 200:
            raise HTTPException(400, "Hugging Face did not accept that token.")
        credentials.store(credentials.HF, token)
        return {"ok": True, "account": r.json().get("name")}

    @api.delete("/api/credentials/huggingface")
    async def delete_hf_token():
        credentials.delete(credentials.HF)
        return {"ok": True}

    # --- phone and LAN access ----------------------------------------------

    def lan_status() -> dict:
        addrs = lan.lan_addresses()
        return {
            "enabled": core.settings.lan_enabled,
            "active": listener.active,
            "port": listener.port,
            "error": listener.error,
            "addresses": addrs,
            "urls": [f"http://{a}:{listener.port}/" for a in addrs] if listener.active else [],
            "devices": devices.public(),
        }

    @api.get("/api/lan")
    async def lan_get():
        return lan_status()

    @api.post("/api/lan")
    async def lan_set(request: Request):
        body = await request.json()
        enabled = bool(body.get("enabled"))
        settings.update(core.settings, {"lan_enabled": enabled})
        if enabled:
            await listener.start()
        else:
            await listener.stop()
        return lan_status()

    @api.post("/api/lan/pair-code")
    async def lan_pair_code():
        if not listener.active:
            raise HTTPException(409, "Turn on network access first.")
        addrs = lan.lan_addresses()
        if not addrs:
            raise HTTPException(409, "This computer is not on a network.")
        code, ttl = codes.create()
        urls = [f"http://{a}:{listener.port}/pair?code={code}" for a in addrs]
        return {
            "code": code,
            "expires_in": ttl,
            "url": urls[0],
            "address": f"http://{addrs[0]}:{listener.port}/pair",
            "qr": qr_svg(urls[0]),
        }

    @api.delete("/api/lan/devices/{device_id}")
    async def lan_revoke(device_id: str):
        ok = devices.revoke(device_id)
        for ws in list(device_sockets.pop(device_id, set())):
            try:
                await ws.close(code=4401)
            except RuntimeError:
                pass
        return {"ok": ok}

    async def redeem(request: Request, code: str) -> Response:
        if not request.scope.get("irisecho_lan"):
            raise HTTPException(404, "not found")
        if codes.locked():
            return pair_page("Too many wrong codes. Wait a minute and try again.")
        if not codes.redeem(code):
            return pair_page("That code was not right, or it has expired.")
        device, token = devices.add(lan.device_name(request.headers.get("user-agent", "")))
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            lan.DEVICE_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=60 * 60 * 24 * 365 * 5,
            path="/",
        )
        return resp

    @api.get("/pair")
    async def pair_get(request: Request, code: str = ""):
        if not request.scope.get("irisecho_lan"):
            raise HTTPException(404, "not found")
        if code:
            return await redeem(request, code)
        if request.state.device:
            return RedirectResponse("/", status_code=303)
        return pair_page()

    @api.post("/pair")
    async def pair_post(request: Request):
        form = await request.form()
        return await redeem(request, str(form.get("code") or ""))

    # --- live events -------------------------------------------------------

    @api.websocket("/api/events")
    async def events(ws: WebSocket):
        device = None
        if ws.scope.get("irisecho_lan"):
            device = devices.find(ws.cookies.get(lan.DEVICE_COOKIE))
            if device is None or not lan.host_ok(ws.headers.get("host") or ""):
                await ws.close(code=4401)
                return
        else:
            host = (ws.headers.get("host") or "").split(":")[0].lower()
            if ws.cookies.get(COOKIE) != session or host not in ("127.0.0.1", "localhost"):
                await ws.close(code=4401)
                return
        await ws.accept()
        if device:
            device_sockets.setdefault(device.id, set()).add(ws)
        q = core.bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    await ws.send_text(json.dumps(event))
                except TimeoutError:
                    await ws.send_text('{"type":"ping"}')
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            core.bus.unsubscribe(q)
            if device:
                device_sockets.get(device.id, set()).discard(ws)

    # --- the web UI --------------------------------------------------------

    def index(request: Request) -> Response:
        if request.scope.get("irisecho_lan") and request.state.device is None:
            return RedirectResponse("/pair", status_code=303)
        page = WEB / "index.html"
        if page.is_file():
            html = page.read_text(encoding="utf-8")
        else:
            html = (
                "<!doctype html><meta charset=utf-8><title>IrisEcho</title>"
                "<body style='font-family:sans-serif;background:#0E0B16;"
                "color:#F3F0FA;padding:3rem'>"
                "<h1>IrisEcho is running</h1><p>The interface has not been built. "
                "Run <code>npm run build</code> in <code>ui/</code>.</p>"
            )
        resp = HTMLResponse(html, headers={"Cache-Control": "no-store"})
        if not request.scope.get("irisecho_lan"):
            resp.set_cookie(COOKIE, session, httponly=True, samesite="strict", path="/")
        return resp

    @api.get("/agents.md")
    def agents_guide(request: Request):
        if request.scope.get("irisecho_lan"):  # it names folders on this computer
            raise HTTPException(404, "not found")
        return Response(
            guide.text(),
            media_type="text/markdown; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @api.get("/")
    def root(request: Request):
        return index(request)

    @api.get("/{path:path}")
    def static(path: str, request: Request):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        target = (WEB / path).resolve()
        if target.is_file() and target.is_relative_to(WEB.resolve()):
            return FileResponse(target, headers={"Cache-Control": "public, max-age=3600"})
        return index(request)

    return api


def write_server_info(port: int, token: str) -> Path:
    info = paths.data_dir() / "server.json"
    info.write_text(
        json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8"
    )
    if sys.platform != "win32":
        info.chmod(0o600)
    return info
