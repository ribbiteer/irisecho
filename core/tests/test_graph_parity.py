# SPDX-License-Identifier: AGPL-3.0-or-later
"""A job that sets no advanced control must build the same graph as before controls existed.

The snapshot in data/graph_parity.json was written from the builders as they were
before any control was added. Regenerate it only when a graph is meant to change
for everyone: IRISECHO_WRITE_PARITY=1 uv run pytest core/tests/test_graph_parity.py
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from irisecho_core import registry
from irisecho_core.engines.comfy import graphs

SNAPSHOT = Path(__file__).parent / "data" / "graph_parity.json"
HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")


# Models added after the snapshot was taken; their graphs have tests of their own
# (test_new_models.py). Nothing they add may change the graphs of the models above.
ADDED_LATER = {"z-image-control", "seedvr2-7b", "pixal3d-single", "wan22-camera"}


def cases() -> list[tuple[str, str, dict, dict]]:
    """(case id, model id, images, params) for every comfy model and the params its form sends."""
    out = []
    for mid, m in registry.default().models.items():
        if m.engine != "comfy" or m.kind == "views3d" or mid in ADDED_LATER:
            continue
        kind = m.kind
        if kind == "image":
            out += [
                (f"{mid}/square", mid, {}, {"aspect": "1:1"}),
                (f"{mid}/wide", mid, {}, {"aspect": "16:9"}),
            ]
            if mid.startswith("flux"):
                out.append(
                    (f"{mid}/redraw", mid, {"image1": "in.png"}, {"aspect": "1:1", "denoise": 0.2})
                )
                out.append(
                    (
                        f"{mid}/redraw-size",
                        mid,
                        {"image1": "in.png"},
                        {"denoise": 0.2, "width": 1920, "height": 1088},
                    )
                )
        elif kind == "edit":
            out.append((f"{mid}/one", mid, {"image1": "a.png"}, {}))
            if mid.startswith("qwen"):
                out.append((f"{mid}/two", mid, {"image1": "a.png", "image2": "b.png"}, {}))
        elif kind == "upscale":
            out += [
                (f"{mid}/x2", mid, {"image1": "a.png"}, {"scale": 2, "retain": False}),
                (f"{mid}/x4-keep", mid, {"image1": "a.png"}, {"scale": 4, "retain": True}),
            ]
        elif kind == "video-upscale":
            out += [
                (f"{mid}/default", mid, {"video": "c.mp4"}, {}),
                (f"{mid}/720", mid, {"video": "c.mp4"}, {"short_side": 720}),
            ]
        elif kind == "video":
            video = m.settings["video"]
            frames = video.get("frames", [])
            imgs: list[dict] = []
            if not video.get("needs_frame"):
                imgs.append({})
            if "start" in frames:
                imgs.append({"start": "s.png"})
            if "end" in frames:
                imgs.append({"end": "e.png"})
                if "start" in frames:
                    imgs.append({"start": "s.png", "end": "e.png"})
            for im in imgs:
                for size in video.get("sizes", ["standard"]):
                    tag = "+".join(sorted(im)) or "text"
                    out.append(
                        (
                            f"{mid}/{tag}/{size}",
                            mid,
                            im,
                            {"aspect": "16:9", "seconds": 4, "size": size},
                        )
                    )
                out.append(
                    (
                        f"{mid}/{tag or 'text'}/smooth",
                        mid,
                        im,
                        {"aspect": "9:16", "seconds": 2, "smooth": True},
                    )
                )
        elif kind == "model3d":
            imgs = {"front": "f.png", "back": "b.png"} if mid == "pixal3d" else {"front": "f.png"}
            for res, detail in ((1024, "standard"), (1536, "high")):
                out.append(
                    (
                        f"{mid}/{detail}",
                        mid,
                        imgs,
                        {"detail": detail, "resolution": res, "faces": 500000},
                    )
                )
            out.append(
                (
                    f"{mid}/plain",
                    mid,
                    imgs,
                    {"resolution": 1024, "faces": 20000, "textures": False, "openings": "keep"},
                )
            )
    return out


def build(mid: str, images: dict, params: dict) -> list:
    m = registry.default().models[mid]
    graphs_out = []
    for v in m.variants:
        if v.when.get("backend", "cuda") != "cuda":
            continue
        builder = graphs.BUILDERS[v.workflow]
        names = {f: f"{f}.safetensors" for f in v.files}
        for spec in registry.default().expand(v.files):
            names[spec.id] = spec.filename
        g, save = builder(
            prompt="a red fox in the snow",
            seed=7,
            aspect=params.get("aspect", "1:1"),
            names=names,
            settings=m.settings,
            hw=HW,
            prefix="p",
            images=images,
            params=params,
        )
        graphs_out.append({"workflow": v.workflow, "when": v.when, "graph": g, "save": save})
    return graphs_out


def all_graphs() -> dict:
    return {cid: build(mid, images, params) for cid, mid, images, params in cases()}


def test_graphs_without_controls_are_unchanged():
    now = json.loads(json.dumps(all_graphs()))
    if os.environ.get("IRISECHO_WRITE_PARITY") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(
            json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        pytest.skip("snapshot written")
    before = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(now) == sorted(before), "the set of parity cases changed"
    for cid in before:
        assert now[cid] == before[cid], f"{cid}: graph changed without any control set"


def test_views_graphs_are_unchanged():
    """The 3D studio's view helper (not a builder in BUILDERS)."""
    m = registry.default().models["object-views"]
    names = {s.id: s.filename for s in registry.default().expand(m.variants[0].files)}
    now = {
        "level": graphs.views_level(names=names, image="a.png", seed=3, prefix="p"),
        "back": graphs.views_back(names=names, image="a.png", attempt=0, seed=3, prefix="p"),
    }
    now = json.loads(json.dumps(now))
    path = SNAPSHOT.with_name("views_parity.json")
    if os.environ.get("IRISECHO_WRITE_PARITY") == "1":
        path.write_text(
            json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        pytest.skip("snapshot written")
    assert now == json.loads(path.read_text(encoding="utf-8"))
