# SPDX-License-Identifier: AGPL-3.0-or-later
"""ACE-Step 1.5: music beds, jingles and stingers.

Installed from a pinned source archive and synced from the project's own
uv.lock, so the environment is exactly the one its authors test.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import httpx

from irisecho_core.engines import audioformat, audiosource, uvenv
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.worker import WorkerEngine

SOURCE = ("ACE-Step/ACE-Step-1.5", "6d467e4b5081ccb0abf1ec1bf4fdf9051a2d34b0")
LM = "acestep-5Hz-lm-0.6B"
MAX_TAKES = 4


def group_root(ctx: RunContext, group: str) -> Path:
    """The folder a file group was laid out under (ACE-Step reads folders, not files).

    Every file of the group must sit at <root>/<subdir>/<name>; files found in
    a linked folder only count if that folder has the same layout.
    """
    roots = set()
    for fid, path in ctx.files.items():
        if not fid.startswith(group + "/"):
            continue
        spec = ctx.specs[fid]
        rel = Path(spec.category.split("/", 1)[1]) if "/" in spec.category else Path()
        depth = len((rel / spec.subdir).parts) + 1
        roots.add(path.parents[depth - 1])
    if len(roots) != 1:
        raise RuntimeError(
            f"The files for {ctx.model.name} are split between folders. Remove the model on "
            "the Models page and set it up again to download a complete copy."
        )
    return roots.pop()


def _bounded(model, name: str, value, kind=float):
    value = kind(value)
    c = model.control(name)
    if c is not None and c.min is not None and c.max is not None:
        value = max(kind(c.min), min(kind(c.max), value))
    return value


def compose_options(model, p: dict) -> dict:
    """The advanced music settings a job set (and only those), checked against the registry."""
    out: dict = {}
    if p.get("key"):
        out["key"] = str(p["key"]).strip()
    if p.get("time_signature") not in (None, ""):
        out["time_signature"] = str(p["time_signature"]).strip()
    if p.get("vocal_language"):
        out["vocal_language"] = str(p["vocal_language"]).strip()
    if p.get("steps") is not None:
        out["steps"] = _bounded(model, "steps", p["steps"], int)
    if p.get("shift") is not None:
        out["shift"] = _bounded(model, "shift", p["shift"])
    if p.get("lm_temperature") is not None:
        out["lm_temperature"] = _bounded(model, "lm_temperature", p["lm_temperature"])
    if p.get("lm_top_p") is not None:
        out["lm_top_p"] = _bounded(model, "lm_top_p", p["lm_top_p"])
    if p.get("raw"):
        out["raw"] = True
    return out


TASKS = ("cover", "repaint", "extend")
MAX_EXTEND = 240.0  # seconds added by one extend, in step with the longest song the studio makes


def source_options(model, p: dict) -> dict:
    """What a job that starts from a recording adds to the request ({} for a plain job).

    `task` is cover (the same song in a new style), repaint (new music for one part)
    or extend (new music after the end). The worker turns extend into a repaint past
    the end of the recording, which ACE-Step pads with silence itself.
    """
    task = p.get("task") or ""
    source = p.get("source")
    if not task and not source:
        return {}
    if not task:
        raise ValueError(
            "Choose what to do with the recording: cover it, repaint a part, or extend it."
        )
    if task not in TASKS:
        raise ValueError(f"Unknown task {task}; use cover, repaint or extend.")
    if not source:
        raise ValueError("Add the recording to start from first.")
    out: dict = {"task": task, "source": str(audiosource.upload_path(source))}
    if task == "cover":
        if p.get("cover_strength") is not None:
            out["cover_strength"] = _bounded(model, "cover_strength", p["cover_strength"])
        if p.get("cover_noise") is not None:
            out["cover_noise"] = _bounded(model, "cover_noise", p["cover_noise"])
    elif task == "repaint":
        if p.get("region") is None:
            raise ValueError("Give the part to repaint as a start and an end, in seconds.")
        out["region"] = list(audiosource.region(p["region"]))
    else:
        if p.get("extend_by") is None:
            raise ValueError("Say how many seconds to add.")
        by = audiosource.seconds(p["extend_by"], "Seconds to add")
        if not 1 <= by <= MAX_EXTEND:
            raise ValueError(f"Seconds to add must be between 1 and {MAX_EXTEND:g}.")
        out["extend_by"] = round(by, 3)
    return out


class AceStepEngine(WorkerEngine):
    id = "ace-step"
    name = "ACE-Step"
    install_version = 1
    worker_script = Path(__file__).parent / "worker.py"

    def __init__(self, hardware):
        super().__init__(hardware)
        self.src = self.home / "src"

    def supported(self) -> bool:
        return self.hw.backend in ("cuda", "mps")

    def install_key(self) -> str:
        return f"{super().install_key()}:{SOURCE[1][:12]}"

    def worker_env(self) -> dict[str, str]:
        return {"PYTHONPATH": str(self.src), "TOKENIZERS_PARALLELISM": "false"}

    async def install(self, log) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        log(f"Downloading {SOURCE[0]} @ {SOURCE[1][:10]}")
        async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
            r = await client.get(f"https://codeload.github.com/{SOURCE[0]}/zip/{SOURCE[1]}")
            r.raise_for_status()
        if self.src.exists():
            shutil.rmtree(self.src)
        tmp = self.home / "src.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(tmp)
        (inner,) = list(tmp.iterdir())
        inner.rename(self.src)
        tmp.rmdir()

        if self.venv.exists():
            shutil.rmtree(self.venv)
        env = uvenv.uv_env()
        env["UV_PROJECT_ENVIRONMENT"] = str(self.venv)
        log("Installing ACE-Step and its pinned dependencies (several GB, one time)")
        await uvenv.run(
            [uvenv.uv_bin(), "sync", "--frozen", "--no-dev", "--python", "3.12"],
            log,
            cwd=self.src,
            env=env,
        )
        await uvenv.pip_install(self.venv, ["soundfile>=0.13"], log)
        self.mark_installed()

    def device(self) -> str:
        return {"cuda": "cuda", "mps": "mps"}.get(self.hw.backend, "cpu")

    async def run(self, ctx: RunContext) -> list[dict]:
        p = ctx.params
        caption = (p.get("prompt") or "").strip()
        if not caption:
            raise ValueError("Describe the music with a few style tags.")
        takes = max(1, min(MAX_TAKES, int(p.get("takes") or 1)))
        # The song planner (5 Hz LM) is on unless asked off, as in ACE-Step's own app.
        thinking = p.get("thinking") is not False
        # Cover, repaint and extend start from the recording, not from a plan (ACE-Step
        # skips the planner for them, inference.py:612), so it is not even loaded.
        source = source_options(ctx.model, p)
        if source:
            thinking = False
        root = group_root(ctx, "ace-step-core")
        lm_root = group_root(ctx, "ace-step-lm")
        await self.call(
            ctx,
            "load",
            {
                "checkpoints": str(root),
                "project": str(self.src),
                "device": self.device(),
                "lm": LM,
                "lm_root": str(lm_root),
                "thinking": thinking,
            },
        )
        _, ext, fmt = audioformat.pick(p)
        outs = [str(ctx.output(i, ext)) for i in range(takes)]
        request = {
            "caption": caption,
            "lyrics": (p.get("lyrics") or "").strip(),
            "duration": float(p.get("duration") or 30),
            "bpm": int(p["bpm"]) if p.get("bpm") else None,
            "seed": p.get("seed"),
            "thinking": thinking,
            "loop": bool(p.get("loop")) and bool(p.get("bpm")),
            "outs": outs,
            **fmt,
            **compose_options(ctx.model, p),
            **source,
        }
        result = await self.call(ctx, "compose", request)
        return [
            {"path": o["path"], "type": "audio", "duration": o["duration"], "seed": o.get("seed")}
            for o in result["takes"]
        ]
