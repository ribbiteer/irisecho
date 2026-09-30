# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import time

import pytest


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    from irisecho_core.app import App

    return App()


def _queued(app, job_id, model):
    app.db.insert(
        {
            "id": job_id,
            "model": model,
            "kind": app.model(model).kind,
            "params": {},
            "status": "queued",
            "created": time.time(),
        }
    )


def test_an_engine_that_will_not_start_fails_everything_waiting_for_it(app):
    _queued(app, "a1", "z-image-turbo")
    _queued(app, "a2", "flux-schnell")
    _queued(app, "b1", "kokoro")
    app._fail_waiting(app.engines["comfy"])
    assert app.db.get("a1")["status"] == app.db.get("a2")["status"] == "failed"
    assert "did not start" in app.db.get("a1")["error"]
    assert app.db.get("b1")["status"] == "queued"  # another engine's job still has its turn


def test_folders_changed_during_a_scan_are_scanned_before_it_reports_done(app, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    scanned = []
    real = app.store.scan_links

    def slow_scan(progress=None, on_link=None):
        scanned.append([d.name for d in app.store.linked_dirs])
        found = real(progress, on_link)
        time.sleep(0.2)  # the folders change while this scan is still going
        return found

    app.store.scan_links = slow_scan

    async def go():
        app.bus.bind(asyncio.get_running_loop())
        events = app.bus.subscribe()
        app.set_linked_dirs([str(first)])
        await asyncio.sleep(0.05)  # the first scan is under way
        app.set_linked_dirs([str(first), str(second)])
        await app.scan_task
        done = []
        while not events.empty():
            e = events.get_nowait()
            if e["type"] == "scan" and e["data"].get("state") == "done":
                done.append(e["data"])
        return done

    done = asyncio.run(go())
    assert scanned == [["one"], ["one", "two"]]
    assert len(done) == 1 and not done[0]["error"]
    assert not app.store.needs_scan()


def test_a_scan_that_breaks_still_reports_that_it_ended(app, tmp_path):
    folder = tmp_path / "models"
    folder.mkdir()

    def broken(progress=None, on_link=None):
        raise RuntimeError("disk went away")

    app.store.scan_links = broken

    async def go():
        app.bus.bind(asyncio.get_running_loop())
        events = app.bus.subscribe()
        app.set_linked_dirs([str(folder)])
        await app.scan_task
        return [events.get_nowait() for _ in range(events.qsize())]

    events = asyncio.run(go())
    done = [e["data"] for e in events if e["type"] == "scan" and e["data"].get("state") == "done"]
    assert done == [{"state": "done", "found": 0, "error": "disk went away"}]
    assert not app.scanning
