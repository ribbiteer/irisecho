# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from irisecho_core import lan

H = {"X-IrisEcho": "1"}
LAN_BASE = "http://studio-pc:7789"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    from irisecho_core.server import create_app

    return create_app(token="t0ken")


@pytest.fixture
def local(api):
    with TestClient(api, base_url="http://127.0.0.1") as c:
        c.get("/")
        yield c


@pytest.fixture
def phone(api, local):
    """A client on the LAN listener that has not paired."""
    with TestClient(lan.LanScope(api), base_url=LAN_BASE) as c:
        yield c


def pair(api, phone, agent="Mozilla/5.0 (Linux; Android 15) Chrome/140.0 Mobile Safari/537.36"):
    code, _ = api.state.pairing.create()
    r = phone.get(f"/pair?code={code}", headers={"User-Agent": agent}, follow_redirects=False)
    assert r.status_code == 303
    assert lan.DEVICE_COOKIE in phone.cookies
    return code


def test_unpaired_phone_gets_the_pairing_page_not_the_app(phone):
    r = phone.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pair"
    assert "irisecho_session" not in r.cookies  # the local session is never handed out
    page = phone.get("/pair")
    assert page.status_code == 200 and "Pair this device" in page.text
    assert phone.get("/api/bootstrap").status_code == 401


def test_local_session_cookie_is_worthless_on_the_lan(phone):
    phone.cookies.set("irisecho_session", "t0ken")
    assert phone.get("/api/bootstrap").status_code == 401


def test_pairing_is_lan_only(local):
    assert local.get("/pair").status_code == 404


def test_pairing_then_allowed_and_forbidden_calls(api, phone, local):
    pair(api, phone)
    boot = phone.get("/api/bootstrap").json()
    assert boot["remote"] is True
    assert boot["system"]["data_dir"] == ""
    assert boot["settings"]["outputs_path"] == "" and boot["settings"]["linked_model_dirs"] == []
    assert local.get("/api/bootstrap").json()["remote"] is False
    # Making things works (an unknown model proves the guard let the call through).
    assert phone.post("/api/jobs", json={"model": "nope"}, headers=H).status_code == 404
    # Everything about the computer itself does not.
    for method, path in [
        ("post", "/api/settings/rescan"),
        ("patch", "/api/settings"),
        ("put", "/api/credentials/huggingface"),
        ("post", "/api/open/outputs"),
        ("post", "/api/licenses/flux-dev-nc/accept"),
        ("post", "/api/models/kokoro/prepare"),
        ("delete", "/api/models/kokoro/files"),
        ("post", "/api/engines/comfy/install"),
        ("get", "/api/lan"),
        ("post", "/api/lan"),
        ("post", "/api/lan/pair-code"),
        ("post", "/api/jobs/abc123/outputs/0/reveal"),
    ]:
        r = getattr(phone, method)(path, headers=H)
        assert r.status_code == 403, (method, path, r.status_code)
    # A paired phone still cannot skip the request header.
    assert phone.post("/api/jobs", json={"model": "nope"}).status_code == 403


def test_the_app_page_is_served_to_a_paired_phone_without_the_local_cookie(api, phone):
    pair(api, phone)
    r = phone.get("/")
    assert r.status_code == 200
    assert "irisecho_session" not in r.cookies


def test_code_is_single_use_and_wrong_codes_lock_out(api, phone):
    code, _ = api.state.pairing.create()
    assert phone.get(f"/pair?code={code}", follow_redirects=False).status_code == 303
    phone.cookies.clear()
    assert phone.get(f"/pair?code={code}").status_code == 401  # already used
    for _ in range(lan.MAX_WRONG):
        phone.get("/pair?code=WRONGCODE")
    fresh, _ = api.state.pairing.create()
    r = phone.get(f"/pair?code={fresh}")
    assert r.status_code == 401 and "Too many" in r.text  # locked, even for a good code


def test_codes_expire(api, phone, monkeypatch):
    code, _ = api.state.pairing.create()
    now = lan.time.time()
    monkeypatch.setattr(lan.time, "time", lambda: now + lan.CODE_TTL + 1)
    assert phone.get(f"/pair?code={code}").status_code == 401


def test_codes_are_typed_forgivingly(api, phone):
    code, _ = api.state.pairing.create()
    spaced = f"{code[:4].lower()} - {code[4:].lower()}"
    r = phone.post("/pair", data={"code": spaced}, follow_redirects=False)
    assert r.status_code == 303


def test_revoking_a_device_ends_its_access(api, phone, local):
    pair(api, phone)
    assert phone.get("/api/bootstrap").status_code == 200
    listed = local.get("/api/lan").json()["devices"]
    assert len(listed) == 1 and listed[0]["name"] == "Android · Chrome"
    assert local.delete(f"/api/lan/devices/{listed[0]['id']}", headers=H).json() == {"ok": True}
    assert phone.get("/api/bootstrap").status_code == 401
    assert local.get("/api/lan").json()["devices"] == []


def test_foreign_host_names_are_refused_on_the_lan(api, phone):
    pair(api, phone)
    assert phone.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code == 403
    assert phone.get("/api/bootstrap", headers={"Host": "203.0.113.9:7789"}).status_code == 200
    assert phone.get("/api/bootstrap", headers={"Host": "studio.local"}).status_code == 200


def test_events_socket_needs_a_paired_device(api, phone):
    with pytest.raises(WebSocketDisconnect):
        with phone.websocket_connect("/api/events"):
            pass
    pair(api, phone)
    cookie = f"{lan.DEVICE_COOKIE}={phone.cookies[lan.DEVICE_COOKIE]}"
    with phone.websocket_connect("/api/events", headers={"cookie": cookie}) as ws:
        ws.close()
    # The computer's own session cookie does not open it either.
    with pytest.raises(WebSocketDisconnect):
        with phone.websocket_connect("/api/events", headers={"cookie": "irisecho_session=t0ken"}):
            pass


def test_tokens_are_stored_only_as_hashes(api, phone, tmp_path):
    pair(api, phone)
    token = phone.cookies[lan.DEVICE_COOKIE]
    saved = (tmp_path / "devices.json").read_text(encoding="utf-8")
    assert token not in saved and json.loads(saved)[0]["token_hash"] == lan._hash(token)


def test_allow_list_and_host_rules():
    assert lan.device_may("GET", "/api/jobs/abc123/outputs/0")
    assert lan.device_may("POST", "/api/jobs/abc123/favorite")
    assert not lan.device_may("POST", "/api/jobs/abc123/outputs/0/reveal")
    assert not lan.device_may("GET", "/api/credentials/huggingface")
    assert not lan.device_may("GET", "/api/lan")
    assert lan.host_ok("203.0.113.5:7789") and lan.host_ok("[fe80::1]:7789")
    assert lan.host_ok("desk") and lan.host_ok("desk.local:7789")
    assert not lan.host_ok("evil.example") and not lan.host_ok("")


def test_pair_code_endpoint_needs_the_listener(local):
    assert local.post("/api/lan/pair-code", headers=H).status_code == 409


def test_listener_starts_serves_and_stops(api, local):
    async def go():
        listener = api.state.listener
        await listener.start()
        assert listener.active and not listener.error
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.port}") as c:
                r = await c.get("/", follow_redirects=False)
                assert r.status_code == 303 and r.headers["location"] == "/pair"
                assert (await c.get("/api/bootstrap")).status_code == 401
        finally:
            await listener.stop()
        assert not listener.active

    asyncio.run(go())


def test_qr_svg_scales_instead_of_cropping():
    from irisecho_core.server import qr_svg

    svg = qr_svg("http://studio-pc:7789/pair?code=ABCDEFGH")
    head = svg.split(">", 1)[0]
    assert "viewBox=" in head and " width=" not in head and " height=" not in head
