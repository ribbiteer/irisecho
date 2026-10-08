# SPDX-License-Identifier: AGPL-3.0-or-later
"""The IrisEcho application: models, engines, downloads and the job queue.

The HTTP server and the command line both drive one of these.
"""

from __future__ import annotations

import asyncio
import re
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from irisecho_core import hardware, paths, registry, settings
from irisecho_core.db import HIDDEN_KINDS, Database
from irisecho_core.downloads import DownloadManager
from irisecho_core.engines import build_engines
from irisecho_core.engines.base import Engine, EngineStartError, RunContext
from irisecho_core.events import EventBus, Throttle
from irisecho_core.registry import ModelSpec
from irisecho_core.store import Store


class NotReady(Exception):
    """A model cannot run yet; `needs` says what the person has to do."""

    def __init__(self, message: str, needs: list[str]):
        super().__init__(message)
        self.needs = needs


class Busy(Exception):
    """A job is using the GPU."""


class App:
    def __init__(self) -> None:
        self.settings = settings.load()
        self.hw = hardware.detect()
        self.registry = registry.default()
        self.store = Store(
            self.registry, self.settings.models_path, self.settings.linked_model_dirs
        )
        self.bus = EventBus()
        self.downloads = DownloadManager(self.store, self.bus)
        self.db = Database(paths.data_dir() / "state.db")
        self.engines: dict[str, Engine] = build_engines(self)
        self.install_tasks: dict[str, asyncio.Task] = {}
        self.install_logs: dict[str, deque[str]] = {}
        self.scan_task: asyncio.Task | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.contexts: dict[str, RunContext] = {}
        self.resident: Engine | None = None
        self.current_job: str | None = None
        self.loaded_model: str | None = None
        self._gpu = asyncio.Lock()  # held while a job runs or the GPU is being freed
        self._runner: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self.bus.bind(asyncio.get_running_loop())
        self.db.interrupted()
        self._runner = asyncio.create_task(self._run_queue())
        if self.store.needs_scan():
            # New models (or new folders) since the last look: check linked folders again.
            self.scan_links()

    async def stop(self) -> None:
        if self._runner:
            self._runner.cancel()
        for engine in self.engines.values():
            try:
                await engine.shutdown()
            except Exception:
                pass

    # --- models ------------------------------------------------------------

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.registry.models[model_id]
        except KeyError:
            raise KeyError(f"unknown model {model_id}") from None

    def model_status(self, model: ModelSpec) -> dict:
        variant = model.variant_for(self.hw)
        lic = self.registry.licenses[model.license]
        engine = self.engines.get(model.engine)
        files = self.registry.expand(variant.files) if variant else []
        statuses = [self.store.status(f) for f in files]
        needs = []
        if not variant or not engine or not engine.supported():
            needs.append("unsupported")
        else:
            if lic.accept and lic.id not in self.settings.accepted_licenses:
                needs.append("license")
            if not engine.installed():
                needs.append("engine")
            if any(s.state not in ("ready", "linked") for s in statuses):
                needs.append("download")
        total = sum(f.size for f in files)
        have = sum(s.have for s in statuses)
        downloads = [
            self.downloads.items[f.id].public() for f in files if f.id in self.downloads.items
        ]
        return {
            "id": model.id,
            "name": model.name,
            "kind": model.kind,
            "engine": model.engine,
            "summary": model.summary,
            "prompt_style": model.settings.get("prompt_style"),
            "page": files[0].page if files else "",
            "license": {
                "id": lic.id,
                "name": lic.name,
                "url": lic.url,
                "commercial": lic.commercial,
                "accept": lic.accept,
                "summary": lic.summary,
                "condition": lic.condition,
                "gated": lic.gated,
                "accepted": lic.id in self.settings.accepted_licenses,
            },
            "available": variant is not None,
            "preview": bool(variant and variant.preview),
            "ready": not needs,
            "needs": needs,
            "size": total,
            "have": have,
            "files": [s.public() for s in statuses],
            "downloads": downloads,
            "installing": model.engine in self.install_tasks,
            "options": self.model_options(model),
        }

    def model_options(self, model: ModelSpec) -> dict:
        if model.engine == "kokoro":
            from irisecho_core.engines.kokoro import voice_options

            return {"voices": voice_options()}
        if "video" in model.settings:
            return {"video": model.settings["video"]}
        if "model3d" in model.settings:
            return {"model3d": model.settings["model3d"]}
        return {}

    def models(self) -> list[dict]:
        return [self.model_status(m) for m in self.registry.models.values()]

    def accept_license(self, license_id: str) -> None:
        if license_id not in self.registry.licenses:
            raise KeyError(license_id)
        if license_id not in self.settings.accepted_licenses:
            accepted = [*self.settings.accepted_licenses, license_id]
            settings.update(self.settings, {"accepted_licenses": accepted})
        self.bus.publish("models", {})

    def prepare(self, model_id: str) -> dict:
        """Install the engine and download the files a model needs."""
        model = self.model(model_id)
        status = self.model_status(model)
        if "unsupported" in status["needs"]:
            raise NotReady(f"{model.name} cannot run on this machine.", ["unsupported"])
        if "license" in status["needs"]:
            raise NotReady(f"Accept the {status['license']['name']} first.", ["license"])
        if "engine" in status["needs"]:
            self.install_engine(model.engine)
        self.downloads.request(self.registry.model_files(model, self.hw))
        return self.model_status(model)

    def remove_model(self, model_id: str) -> int:
        """Delete a model's downloaded files that no other ready model still uses.

        Files in linked folders are never touched. Returns the bytes freed.
        """
        model = self.model(model_id)
        mine = self.registry.model_files(model, self.hw)
        shared: set[str] = set()
        for other in self.registry.models.values():
            if other.id == model.id:
                continue
            files = self.registry.model_files(other, self.hw)
            if files and self.store.all_ready(files):
                shared.update(f.id for f in files)
        freed = 0
        for spec in mine:
            if spec.id in shared:
                continue
            self.downloads.cancel(spec.id)
            if self.store.status(spec).state in ("ready", "partial"):
                freed += self.store.status(spec).have
                self.store.remove(spec)
            self.downloads.items.pop(spec.id, None)
        self.bus.publish("models", {})
        return freed

    # --- engines -----------------------------------------------------------

    def install_engine(self, engine_id: str) -> None:
        engine = self.engines[engine_id]
        if engine_id in self.install_tasks or engine.installed():
            return
        log = self.install_logs.setdefault(engine_id, deque(maxlen=400))
        log.clear()
        throttle = Throttle(0.5)
        # Kept on disk as well: a failed install is often only looked at after a retry or a restart.
        log_file = paths.sub("logs") / f"install-{engine_id}.log"

        def write(line: str) -> None:
            log.append(line)
            try:
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
            if throttle.ready():
                self.bus.publish("engine", {**engine.public(), "log": line})

        async def task() -> None:
            engine.state, engine.detail = "installing", ""
            self.bus.publish("engine", engine.public())
            write(f"--- {datetime.now().isoformat(timespec='seconds')} installing {engine.name}")
            try:
                await engine.install(write)
                engine.state = "idle"
                write("--- installed")
            except Exception as e:
                engine.state, engine.detail = "error", str(e)
                write(traceback.format_exc())
            finally:
                self.install_tasks.pop(engine_id, None)
                self.bus.publish("engine", engine.public())
                self.bus.publish("models", {})

        self.install_tasks[engine_id] = asyncio.create_task(task())

    def engines_public(self) -> list[dict]:
        out = []
        for e in self.engines.values():
            data = e.public()
            data["installing"] = e.id in self.install_tasks
            data["resident"] = self.resident is e
            out.append(data)
        return out

    # --- linked model folders ---------------------------------------------

    def set_linked_dirs(self, dirs: list[str]) -> None:
        clean = [str(Path(d)) for d in dirs if d and Path(d).is_dir()]
        settings.update(self.settings, {"linked_model_dirs": clean})
        self.store.linked_dirs = [Path(d) for d in clean]
        self.bus.publish("models", {})
        self.scan_links()

    @property
    def scanning(self) -> bool:
        return self.scan_task is not None and not self.scan_task.done()

    def scan_links(self) -> None:
        if self.scanning:
            # The running scan finishes with the folders it began with, then goes
            # round again if they changed meanwhile (see task below).
            return
        throttle = Throttle(0.5)
        announce = Throttle(1.0)

        def progress(file_id: str, done: int, total: int, all_done: int, all_total: int) -> None:
            if throttle.ready():
                self.bus.publish(
                    "scan",
                    {
                        "file": file_id,
                        "done": done,
                        "total": total,
                        "overall_done": all_done,
                        "overall_total": all_total,
                    },
                )

        def linked(file_id: str) -> None:
            # Models turn usable while the scan is still going.
            if announce.ready():
                self.bus.publish("models", {})

        async def task() -> None:
            found: dict[str, str] = {}
            error = ""
            self.bus.publish("scan", {"state": "scanning"})
            try:
                while True:
                    found |= await asyncio.to_thread(self.store.scan_links, progress, linked)
                    if not self.store.needs_scan():
                        break
                    self.bus.publish("scan", {"state": "scanning", "overall_total": 0})
            except Exception as e:
                error = str(e) or type(e).__name__
                with (paths.sub("logs") / "scan.log").open("a", encoding="utf-8") as log:
                    log.write(f"--- {datetime.now().isoformat()}\n{traceback.format_exc()}\n")
            still = sum(1 for f in found if f in self.store.links)
            self.bus.publish("scan", {"state": "done", "found": still, "error": error})
            self.bus.publish("models", {})

        self.scan_task = asyncio.create_task(task())

    # --- jobs --------------------------------------------------------------

    def submit(self, model_id: str, params: dict) -> dict:
        model = self.model(model_id)
        status = self.model_status(model)
        if status["needs"]:
            raise NotReady(f"{model.name} is not ready yet.", status["needs"])
        job = {
            "id": uuid.uuid4().hex[:12],
            "model": model.id,
            "kind": model.kind,
            "params": params,
            "status": "queued",
            "progress": None,
            "message": None,
            "error": None,
            "created": time.time(),
            "started": None,
            "finished": None,
            "outputs": [],
        }
        self.db.insert(job)
        if model.kind in HIDDEN_KINDS:
            self.db.prune(model.kind, keep=40)
        self.queue.put_nowait(job["id"])
        self._emit_job(job["id"])
        return self.db.get(job["id"])

    def _emit_job(self, job_id: str) -> None:
        job = self.db.get(job_id)
        if job:
            self.bus.publish("job", job)

    async def cancel(self, job_id: str) -> bool:
        job = self.db.get(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return False
        if job["status"] == "queued":
            self.db.update(job_id, status="cancelled", finished=time.time())
        else:
            ctx = self.contexts.get(job_id)
            if ctx:
                ctx.cancelled.set()
            engine = self.engines[self.model(job["model"]).engine]
            await engine.cancel(job_id)
        self._emit_job(job_id)
        return True

    def delete_job(self, job_id: str) -> bool:
        job = self.db.get(job_id)
        if not job or job["status"] in ("queued", "running"):
            return False
        for out in job["outputs"]:
            Path(out["path"]).unlink(missing_ok=True)
            if out.get("preview"):
                Path(out["preview"]).unlink(missing_ok=True)
        self.db.delete(job_id)
        self.bus.publish("job.deleted", {"id": job_id})
        return True

    def stem_for(self, job: dict) -> tuple[Path, str]:
        text = job["params"].get("prompt") or job["params"].get("text") or job["model"]
        words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-").split("-")
        slug = "-".join(words[:6])[:48].strip("-") or job["model"]
        day = datetime.fromtimestamp(job["created"]).strftime("%Y-%m-%d")
        return self.settings.outputs_path / day, f"{slug}-{job['id'][:6]}"

    async def _run_queue(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.db.get(job_id)
            if not job or job["status"] != "queued":
                continue
            self.current_job = job_id
            try:
                async with self._gpu:
                    await self._run_job(job)
            finally:
                self.current_job = None
                self.contexts.pop(job_id, None)

    async def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        model = self.model(job["model"])
        variant = model.variant_for(self.hw)
        engine = self.engines[model.engine]
        self.db.update(job_id, status="running", started=time.time(), progress=None)
        self._emit_job(job_id)
        throttle = Throttle(0.2)

        def report(progress: float | None, message: str | None) -> None:
            fields = {}
            if progress is not None:
                fields["progress"] = max(0.0, min(1.0, float(progress)))
            if message is not None:
                fields["message"] = message
            if fields:
                self.db.update(job_id, **fields)
                if throttle.ready(force=message is not None):
                    self._emit_job(job_id)

        try:
            if variant is None:
                raise NotReady(f"{model.name} cannot run on this machine.", ["unsupported"])
            specs = self.registry.expand(variant.files)
            files = {}
            for spec in specs:
                path = self.store.locate(spec)
                if path is None:
                    raise NotReady(f"{spec.filename} is missing.", ["download"])
                files[spec.id] = path
            out_dir, stem = self.stem_for(job)
            ctx = RunContext(
                job_id=job_id,
                params=job["params"],
                model=model,
                variant=variant,
                files=files,
                specs={s.id: s for s in specs},
                out_dir=out_dir,
                stem=stem,
                report=report,
                category_dirs=self.store.category_dirs,
            )
            self.contexts[job_id] = ctx
            if self.resident is not engine:
                if self.resident is not None:
                    report(None, f"Freeing the GPU from {self.resident.name}")
                    await self.resident.release()
                self.resident = engine
                self.bus.publish("engines", {})
            self.loaded_model = model.id
            outputs = await engine.run(ctx)
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
            self.db.update(
                job_id,
                status="done",
                progress=1.0,
                message=None,
                finished=time.time(),
                outputs=outputs,
            )
        except asyncio.CancelledError:
            self.db.update(job_id, status="cancelled", message=None, finished=time.time())
        except Exception as e:
            ctx = self.contexts.get(job_id)
            if ctx and ctx.cancelled.is_set():
                self.db.update(job_id, status="cancelled", message=None, finished=time.time())
            else:
                self.db.update(
                    job_id,
                    status="failed",
                    error=str(e) or type(e).__name__,
                    message=None,
                    finished=time.time(),
                )
                with (paths.sub("logs") / "jobs.log").open("a", encoding="utf-8") as log:
                    log.write(f"--- {job_id} {model.id}\n{traceback.format_exc()}\n")
                if isinstance(e, EngineStartError):
                    self._fail_waiting(engine)
        self._emit_job(job_id)

    def _fail_waiting(self, engine: Engine) -> None:
        """An engine would not start: do not make every job queued for it wait to find that out."""
        for job in self.db.queued():
            if self.model(job["model"]).engine != engine.id:
                continue
            self.db.update(
                job["id"],
                status="failed",
                error=f"{engine.name} did not start, so this was not tried.",
                finished=time.time(),
            )
            self._emit_job(job["id"])

    async def unload(self) -> None:
        """Free the GPU for other programs. Refused while a job runs; a job queued
        meanwhile starts once this is done and loads its model again."""
        if self.current_job or self._gpu.locked():
            raise Busy("A job is running. Try again when the queue is empty.")
        async with self._gpu:
            if self.resident is not None:
                await self.resident.release()
                self.resident = None
                self.loaded_model = None
                self.bus.publish("engines", {})

    def system(self) -> dict:
        return {
            "hardware": self.hw.public(),
            "vram": hardware.vram_usage(),
            "queue": {"current": self.current_job, "waiting": self.queue.qsize()},
            "resident": self.resident.id if self.resident else None,
            "loaded_model": self.loaded_model if self.resident else None,
            "data_dir": str(paths.data_dir()),
        }
