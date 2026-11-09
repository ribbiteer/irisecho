# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job queue keeps going whatever one job does, and a cancel always holds."""

import asyncio
import time

import pytest

from irisecho_core.registry import ModelSpec, Variant


class FakeEngine:
    """An engine whose `run` waits until the test lets it go."""

    def __init__(self, engine_id: str):
        self.id = self.name = engine_id
        self.state, self.detail = "idle", ""
        self.ran: list[str] = []
        self.started = asyncio.Event()
        self.go = asyncio.Event()
        self.go.set()
        self.releasing = asyncio.Event()
        self.released = asyncio.Event()
        self.released.set()
        self.writes = False

    def supported(self) -> bool:
        return True

    def installed(self) -> bool:
        return True

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "state": self.state}

    async def run(self, ctx) -> list[dict]:
        self.ran.append(ctx.job_id)
        self.started.set()
        await self.go.wait()
        if not self.writes:
            return []
        path = ctx.output(0, "txt")
        path.write_text("made", encoding="utf-8")
        return [{"path": str(path), "type": "text"}]

    async def release(self) -> None:
        self.releasing.set()
        await self.released.wait()

    async def cancel(self, job_id: str) -> None:
        pass  # like a worker engine that has not been told about the job yet

    async def shutdown(self) -> None:
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    from irisecho_core import hardware
    from irisecho_core.app import App

    monkeypatch.setattr(hardware, "vram_usage", lambda max_age=2.0: [])
    app = App()
    app.store.needs_scan = lambda: False
    license_id = next(lic.id for lic in app.registry.licenses.values() if not lic.accept)
    for model_id, kind, engine in (
        ("fake", "image", "fake"),
        ("other", "image", "other"),
        ("hidden", "prompt", "fake"),
    ):
        spec = ModelSpec(
            id=model_id,
            name=model_id,
            kind=kind,
            engine=engine,
            license=license_id,
            summary="",
            variants=(Variant(when={}, files=()),),
        )
        monkeypatch.setitem(app.registry.models, model_id, spec)
    return app


def play(app, scene):
    """Run `scene(fake, other)` against a started app with two fake engines."""

    async def go():
        fake, other = FakeEngine("fake"), FakeEngine("other")
        app.engines = {"fake": fake, "other": other}
        await app.start()
        try:
            return await asyncio.wait_for(scene(fake, other), 20)
        finally:
            fake.go.set()
            other.released.set()
            await app.stop()

    return asyncio.run(go())


async def settled(app, job_id: str, *statuses: str) -> dict:
    for _ in range(500):
        job = app.db.get(job_id)
        if job["status"] in statuses:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"{job_id} stayed {job['status']}")


def test_a_job_for_a_model_that_is_gone_fails_and_the_next_one_runs(app):
    async def scene(fake, other):
        app.db.insert(
            {
                "id": "ghost",
                "model": "no-such-model",
                "kind": "image",
                "params": {},
                "status": "queued",
                "created": time.time(),
            }
        )
        app.queue.put_nowait("ghost")
        after = app.submit("fake", {})["id"]
        assert (await settled(app, after, "done", "failed"))["status"] == "done"
        ghost = app.db.get("ghost")
        assert ghost["status"] == "failed" and "no-such-model" in ghost["error"]
        assert not app._runner.done()

    play(app, scene)


def test_a_job_that_breaks_outside_its_own_handling_does_not_end_the_queue(app, monkeypatch):
    async def scene(fake, other):
        real = app._run_job
        calls = []

        async def breaks_once(job):
            calls.append(job["id"])
            if len(calls) == 1:
                raise RuntimeError("nobody foresaw this")
            await real(job)

        monkeypatch.setattr(app, "_run_job", breaks_once)
        first = app.submit("fake", {})["id"]
        second = app.submit("fake", {})["id"]
        assert (await settled(app, second, "done", "failed"))["status"] == "done"
        broken = app.db.get(first)
        assert broken["status"] == "failed" and broken["error"] == "nobody foresaw this"
        assert app.current_job is None and not app._runner.done()

    play(app, scene)


def test_a_job_cancelled_while_queued_never_runs(app):
    async def scene(fake, other):
        fake.go.clear()
        first = app.submit("fake", {})["id"]
        second = app.submit("fake", {})["id"]
        third = app.submit("fake", {})["id"]
        await fake.started.wait()
        assert await app.cancel(second)
        assert app.system()["queue"] == {"current": first, "waiting": 1}
        fake.go.set()
        await settled(app, third, "done")
        assert app.db.get(second)["status"] == "cancelled"
        assert fake.ran == [first, third]
        assert app.system()["queue"] == {"current": None, "waiting": 0}

    play(app, scene)


def test_a_cancel_while_the_gpu_is_held_by_something_else_holds(app):
    async def scene(fake, other):
        async with app._gpu:  # as `unload` or a change of card holds it
            job = app.submit("fake", {})["id"]
            while app.current_job != job:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)  # the runner is now waiting for the lock
            assert await app.cancel(job)
        after = app.submit("fake", {})["id"]
        await settled(app, after, "done")
        assert app.db.get(job)["status"] == "cancelled"
        assert fake.ran == [after]

    play(app, scene)


def test_a_cancel_while_the_gpu_is_being_freed_stops_the_job_before_it_starts(app):
    async def scene(fake, other):
        app.resident = other
        other.released.clear()
        job = app.submit("fake", {})["id"]
        await other.releasing.wait()
        assert await app.cancel(job)
        other.released.set()
        assert (await settled(app, job, "cancelled", "done"))["status"] == "cancelled"
        assert fake.ran == []

    play(app, scene)


def test_what_a_cancelled_job_still_made_is_removed(app):
    async def scene(fake, other):
        fake.writes = True
        fake.go.clear()
        job = app.submit("fake", {"prompt": "a pond"})["id"]
        await fake.started.wait()
        assert await app.cancel(job)
        fake.go.set()
        done = await settled(app, job, "cancelled", "done")
        assert done["status"] == "cancelled" and done["outputs"] == []
        out_dir, stem = app.stem_for(done)
        assert not (out_dir / f"{stem}.txt").exists()
        # A job nobody cancelled keeps its file.
        kept = await settled(app, app.submit("fake", {"prompt": "a pond"})["id"], "done")
        assert (out_dir / f"{app.stem_for(kept)[1]}.txt").exists()

    play(app, scene)


def test_quitting_is_not_recorded_as_the_person_cancelling(app):
    async def scene(fake, other):
        fake.go.clear()
        running = app.submit("fake", {})["id"]
        waiting = app.submit("fake", {})["id"]
        await fake.started.wait()
        runner = app._runner
        await app.stop()
        assert runner.done()
        assert app.db.get(running)["status"] == "interrupted"
        fake.go.set()
        await asyncio.sleep(0.05)
        assert app.db.get(waiting)["status"] == "queued"  # marked interrupted at the next start
        assert fake.ran == [running]

    play(app, scene)


def test_an_engine_that_raises_cancelled_for_a_cancelled_job_is_a_cancel(app):
    async def scene(fake, other):
        async def run(ctx):
            fake.started.set()
            await ctx.cancelled.wait()
            raise asyncio.CancelledError  # what the image engine does

        fake.run = run
        job = app.submit("fake", {})["id"]
        await fake.started.wait()
        assert await app.cancel(job)
        assert (await settled(app, job, "cancelled", "interrupted"))["status"] == "cancelled"
        after = app.submit("other", {})["id"]
        await settled(app, after, "done")

    play(app, scene)


def test_working_jobs_dropped_from_history_take_their_files_along(app, tmp_path):
    async def scene(fake, other):
        files = []
        for i in range(41):
            path = tmp_path / f"views-{i}.png"
            path.write_bytes(b"x")
            files.append(path)
            app.db.insert(
                {
                    "id": f"old{i}",
                    "model": "hidden",
                    "kind": "prompt",
                    "params": {},
                    "status": "done",
                    "created": 1000.0 + i,
                    "outputs": [{"path": str(path), "type": "image"}],
                }
            )
        job = app.submit("hidden", {})["id"]
        await settled(app, job, "done")
        assert app.db.get("old0") is None and not files[0].exists()
        assert app.db.get("old1") and all(f.exists() for f in files[1:])

    play(app, scene)
