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

from irisecho_core import controls, hardware, instance, metadata, paths, registry, settings
from irisecho_core.db import HIDDEN_KINDS, Database
from irisecho_core.downloads import DownloadManager
from irisecho_core.engines import build_engines
from irisecho_core.engines.base import Engine, EngineStartError, RunContext
from irisecho_core.events import EventBus, Throttle
from irisecho_core.registry import ModelSpec
from irisecho_core.store import Store

# Uploads no job mentions are removed after this long. The wait is long because a person
# can come back to a form they left open, and an agent stages files in the folder before
# the jobs that use them. Each one removed is written to logs/core.log.
UPLOAD_KEEP = 30 * 24 * 3600
UPLOAD_PRUNE_EVERY = 6 * 3600


class NotReady(Exception):
    """A model cannot run yet; `needs` says what the person has to do."""

    def __init__(self, message: str, needs: list[str]):
        super().__init__(message)
        self.needs = needs


class FileInUse(Exception):
    """A model file could not be deleted because another program has it open."""


class Busy(Exception):
    """A job is using the GPU."""


class UnknownModel(KeyError):
    """No model has this id."""

    def __str__(self) -> str:  # KeyError would show the message in quotes
        return str(self.args[0])


class NoSuchJob(KeyError):
    """No job has this id."""

    def __str__(self) -> str:
        return "no such job"


class JobConflict(Exception):
    """The job is not in a state that allows this; `status` says what it is in."""

    def __init__(self, message: str, status: str):
        super().__init__(message)
        self.status = status


class _Cancelled(Exception):
    """The person cancelled the job. Not asyncio's CancelledError, which is the app quitting."""


class App:
    def __init__(self) -> None:
        self.settings = settings.load()
        self.hw = hardware.detect(self.settings.gpu)
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
        self._instance: instance.InstanceLock | None = None
        self._pruned = float("-inf")

    # --- lifecycle ---------------------------------------------------------

    def claim(self) -> None:
        """Take the data folder for this App, or raise InstanceRunning.

        Held until `stop`, or until the process ends. `start` does it; the server
        does it sooner, before it writes down where it can be reached."""
        if self._instance is None:
            self._instance = instance.acquire(paths.data_dir())

    async def start(self) -> None:
        # First: the queued and running jobs below are another App's if one is running.
        self.claim()
        self.bus.bind(asyncio.get_running_loop())
        self.db.interrupted()
        await asyncio.to_thread(self.prune_uploads)
        self._runner = asyncio.create_task(self._run_queue())
        if self.store.needs_scan():
            # New models (or new folders) since the last look: check linked folders again.
            self.scan_links()

    async def stop(self) -> None:
        if self._runner:
            # Waited for, so no job is started while the engines below shut down.
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):
                pass
            self._runner = None
        for engine in self.engines.values():
            try:
                await engine.shutdown()
            except Exception as e:
                paths.note(
                    "jobs", f"{engine.id} did not shut down cleanly: {type(e).__name__}: {e}"
                )
        if self._instance:
            self._instance.release()
            self._instance = None

    def prune_uploads(self, keep: float = UPLOAD_KEEP, every: float = UPLOAD_PRUNE_EVERY) -> int:
        """Delete uploads that are old and that no job mentions. Returns how many.

        Age is the later of the file's modified and created/changed times: a copy an agent
        made keeps its old modified time. Anything a queued or running job uses is in the
        database, so it is kept like the settings of every finished job. Runs at most once
        every `every` seconds."""
        now = time.monotonic()
        if now - self._pruned < every:
            return 0
        self._pruned = now
        folder = paths.sub("uploads")
        cutoff = time.time() - keep
        old: dict[str, tuple[Path, int, float]] = {}
        for path in folder.iterdir():
            try:
                info = path.stat()
                if path.is_file() and paths.UPLOAD_NAME.fullmatch(path.name):
                    newest = max(info.st_mtime, info.st_ctime)
                    if newest < cutoff:
                        old[path.name] = (path, info.st_size, newest)
            except OSError:
                continue
        removed = 0
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        for name in sorted(set(old) - self.db.mentioning(old)):
            path, size, newest = old[name]
            days = (time.time() - newest) / 86400
            try:
                path.unlink()
                removed += 1
                paths.note(
                    "core",
                    f"{stamp} Removed the upload {name} ({size} bytes, {days:.0f} days old): "
                    "no job uses it.",
                )
            except OSError as e:
                paths.note("core", f"{stamp} Could not remove the old upload {name}: {e!r}")
        return removed

    # --- models ------------------------------------------------------------

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.registry.models[model_id]
        except KeyError:
            raise UnknownModel(f"unknown model {model_id}") from None

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
            "controls": [c.public() for c in model.controls],
            "limits": model.settings.get("limits") or {},
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
        if "edit" in model.settings:
            return {"edit": model.settings["edit"]}
        if "music" in model.settings:
            return {"music": model.settings["music"]}
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

    async def remove_model(self, model_id: str) -> int:
        """Delete a model's downloaded files that no other ready model still uses.

        Files in linked folders are never touched. Returns the bytes freed. A
        file another program holds open is left and reported with FileInUse.
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
        in_use: list[str] = []
        for spec in mine:
            if spec.id in shared:
                continue
            await self.downloads.cancel_and_wait(spec.id)  # it holds the .part file open
            have = self.store.status(spec)
            try:
                if have.state in ("ready", "partial"):
                    self.store.remove(spec)
                    freed += have.have
            except OSError:
                in_use.append(spec.filename)
            self.downloads.items.pop(spec.id, None)
        self.bus.publish("models", {})
        if in_use:
            raise FileInUse(
                f"{', '.join(in_use)} is in use by another program. Close it and try again."
            )
        return freed

    async def verify_model(self, model_id: str) -> list[dict]:
        """Check each of a model's files against its recorded hash. Changes nothing."""
        model = self.model(model_id)
        return [
            await asyncio.to_thread(self.store.verify, spec)
            for spec in self.registry.model_files(model, self.hw)
        ]

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
        params, warnings = controls.check(model, params)
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
            for old in self.db.prunable(model.kind, keep=40):
                self._discard_outputs(old["outputs"])
            self.db.prune(model.kind, keep=40)
        self.queue.put_nowait(job["id"])
        self._emit_job(job["id"])
        out = self.db.get(job["id"])
        if warnings:
            out = {**out, "warnings": warnings}
        return out

    def _emit_job(self, job_id: str) -> None:
        job = self.db.get(job_id)
        if job:
            self.bus.publish("job", job)

    async def cancel(self, job_id: str) -> bool:
        """Stop a queued or running job. NoSuchJob for an unknown id, JobConflict if it ended."""
        job = self.db.get(job_id)
        if not job:
            raise NoSuchJob(job_id)
        if job["status"] not in ("queued", "running"):
            raise JobConflict(f"That job has already finished ({job['status']}).", job["status"])
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

    def favorite(self, job_id: str, on: bool) -> None:
        if not self.db.get(job_id):
            raise NoSuchJob(job_id)
        self.db.update(job_id, favorite=int(on))
        self._emit_job(job_id)

    def delete_job(self, job_id: str) -> bool:
        """Remove a finished job and its files. NoSuchJob for an unknown id; JobConflict while
        it is queued or running; FileInUse (the row is kept) if a file is open elsewhere."""
        job = self.db.get(job_id)
        if not job:
            raise NoSuchJob(job_id)
        if job["status"] in ("queued", "running"):
            raise JobConflict(
                f"That job is still {job['status']}. Cancel it before deleting it.", job["status"]
            )
        in_use = self._remove_outputs(job["outputs"])
        if in_use:
            raise FileInUse(
                f"{', '.join(in_use)} is in use by another program. Close it and try again."
            )
        self.db.delete(job_id)
        self.bus.publish("job.deleted", {"id": job_id})
        return True

    @staticmethod
    def _remove_outputs(outputs: list[dict]) -> list[str]:
        """Remove every file it can; the names of the ones that would not go."""
        stuck: list[str] = []
        for out in outputs:
            for key in ("path", "preview"):
                if not out.get(key):
                    continue
                try:
                    Path(out[key]).unlink(missing_ok=True)
                except OSError:
                    stuck.append(Path(out[key]).name)
        return stuck

    def _discard_outputs(self, outputs: list[dict]) -> None:
        """Remove files nobody asked to keep; one that will not go is not worth failing over."""
        try:
            self._remove_outputs(outputs)
        except (OSError, KeyError, TypeError):
            pass

    @staticmethod
    def _log_job(head: str) -> None:
        try:
            with (paths.sub("logs") / "jobs.log").open("a", encoding="utf-8") as log:
                log.write(f"--- {head}\n{traceback.format_exc()}\n")
        except OSError:
            pass  # the job's own error is already in its row

    @staticmethod
    def _embed_settings(model: ModelSpec, params: dict, outputs: list[dict]) -> None:
        """The person asked for settings in their pictures (privacy switch off)."""
        for out in outputs:
            path = Path(out.get("path", ""))
            if out.get("type") != "image" or path.suffix.lower() != ".png":
                continue
            size = (out["width"], out["height"]) if out.get("width") else None
            try:
                metadata.embed(path, model, params, out.get("seed", params.get("seed")), size)
            except OSError:
                pass  # the picture itself is fine; only the extra text is missing

    def stem_for(self, job: dict) -> tuple[Path, str]:
        text = job["params"].get("prompt") or job["params"].get("text") or job["model"]
        words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-").split("-")
        slug = "-".join(words[:6])[:48].strip("-") or job["model"]
        day = datetime.fromtimestamp(job["created"]).strftime("%Y-%m-%d")
        return self.settings.outputs_path / day, f"{slug}-{job['id'][:6]}"

    async def _run_queue(self) -> None:
        try:
            while True:
                job_id = await self.queue.get()
                try:
                    job = self.db.get(job_id)
                    if not job or job["status"] != "queued":
                        continue
                    self.current_job = job_id
                    async with self._gpu:
                        # Read again: it may have been cancelled while the lock was awaited.
                        job = self.db.get(job_id)
                        if job and job["status"] == "queued":
                            await self._run_job(job)
                except Exception as e:
                    # One job that breaks in a way nobody foresaw must not end the queue.
                    self._job_broke(job_id, e)
                finally:
                    self.current_job = None
                    self.contexts.pop(job_id, None)
        except asyncio.CancelledError:
            raise
        except BaseException:
            self._log_job("the job queue stopped")
            raise

    def _job_broke(self, job_id: str, e: Exception) -> None:
        self._log_job(job_id)
        try:
            job = self.db.get(job_id)
            if job and job["status"] in ("queued", "running"):
                self.db.update(
                    job_id,
                    status="failed",
                    error=str(e) or type(e).__name__,
                    message=None,
                    finished=time.time(),
                )
            self._emit_job(job_id)
        except Exception:
            pass

    async def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        engine: Engine | None = None
        ctx: RunContext | None = None
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
            model = self.model(job["model"])
            variant = model.variant_for(self.hw)
            engine = self.engines[model.engine]
            self.db.update(job_id, status="running", started=time.time(), progress=None)
            self._emit_job(job_id)
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
                if ctx.cancelled.is_set():
                    # Cancelled while the GPU was being freed: the engine never heard of it.
                    raise _Cancelled
            self.loaded_model = model.id
            outputs = await engine.run(ctx)
            if ctx.cancelled.is_set():
                # Finished all the same; nobody wants what it made.
                self._discard_outputs(outputs)
                raise _Cancelled
            if not self.settings.strip_metadata:
                self._embed_settings(model, job["params"], outputs)
            self.db.update(
                job_id,
                status="done",
                progress=1.0,
                message=None,
                finished=time.time(),
                outputs=outputs,
            )
        except _Cancelled:
            self.db.update(job_id, status="cancelled", message=None, finished=time.time())
        except asyncio.CancelledError:
            # Engines raise this for a cancelled job too; when the task itself is being
            # cancelled the app is quitting, which is not the person's doing.
            if asyncio.current_task().cancelling() > 0:
                try:
                    self.db.update(job_id, status="interrupted", message=None)
                    self._emit_job(job_id)
                except Exception:
                    pass
                raise
            if ctx and ctx.cancelled.is_set():
                self.db.update(job_id, status="cancelled", message=None, finished=time.time())
            else:
                # Cancelled from inside the engine with nobody asking: this job failed,
                # and the queue goes on.
                self.db.update(
                    job_id,
                    status="failed",
                    error="The engine stopped before the job was finished.",
                    message=None,
                    finished=time.time(),
                )
                self._log_job(f"{job_id} {job['model']}")
        except Exception as e:
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
                self._log_job(f"{job_id} {job['model']}")
                if isinstance(e, EngineStartError) and engine is not None:
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

    def check_gpu(self, uuid: str) -> None:
        """ValueError unless `uuid` is empty (automatic) or names a card in this computer."""
        if uuid and uuid not in {g.uuid for g in self.hw.gpus}:
            raise ValueError("That graphics card was not found.")

    async def refresh_hardware(self) -> None:
        """Look at the graphics card again when the last look failed (detect spaces them out).

        Skipped while a job runs or the card is being changed; engines pick up a card that
        answers now, as they do after the person chooses another."""
        if not self.hw.problem or self.current_job or self._gpu.locked():
            return
        hw = await asyncio.to_thread(hardware.detect, self.settings.gpu)
        if hw is self.hw:
            return
        changed = hw != self.hw
        self.hw = hw
        for engine in self.engines.values():
            engine.hw = hw
        if changed:
            self.bus.publish("engines", {})
            self.bus.publish("models", {})

    async def set_gpu(self, uuid: str) -> None:
        """Run on another card (empty: pick automatically). Refused while a job runs.

        Every engine is stopped so the next job starts on the new card. A card of a
        different generation can need other model builds or engine installs; the
        Models page then says so as it does on any new machine.
        """
        self.check_gpu(uuid)
        if self.current_job or self._gpu.locked():
            raise Busy("A job is running. Try again when the queue is empty.")
        async with self._gpu:
            for engine in self.engines.values():
                await engine.release()
            self.resident = None
            self.loaded_model = None
            settings.update(self.settings, {"gpu": uuid})
            self.hw = hardware.detect(uuid)
            for engine in self.engines.values():
                engine.hw = self.hw
        self.bus.publish("engines", {})
        self.bus.publish("models", {})

    def system(self) -> dict:
        return {
            "hardware": self.hw.public(),
            "vram": hardware.vram_usage(),
            # Counted in the database: the queue still holds the ids of cancelled jobs.
            "queue": {
                "current": self.current_job,
                "waiting": self.db.waiting(besides=self.current_job),
            },
            "resident": self.resident.id if self.resident else None,
            "loaded_model": self.loaded_model if self.resident else None,
            "data_dir": str(paths.data_dir()),
        }
