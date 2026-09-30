#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate every IrisEcho ComfyUI graph against the installed engine's node schemas.

    uv run python scripts/check_comfy_graphs.py

Starts the private ComfyUI (it must be installed), reads /object_info, and
checks each workflow builder's graph: node classes exist, required inputs are
present, no undeclared inputs are passed (ComfyUI silently drops those), and
choice values such as sampler names are valid.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from irisecho_core.app import App
from irisecho_core.engines.comfy import graphs


def check(graph: dict, info: dict) -> list[str]:
    problems = []
    for nid, node in graph.items():
        cls = node["class_type"]
        spec = info.get(cls)
        if spec is None:
            problems.append(f"{nid}: unknown node {cls}")
            continue
        req = spec["input"].get("required", {})
        opt = spec["input"].get("optional", {})
        declared = {**req, **opt}
        for name in req:
            if name not in node["inputs"]:
                problems.append(f"{nid} {cls}: missing required input {name}")
        for name, value in node["inputs"].items():
            if "." in name and name.split(".", 1)[0] in declared:
                continue  # sub-field of a dynamic combo, e.g. resize_type.multiplier
            if name not in declared:
                problems.append(f"{nid} {cls}: undeclared input {name}")
                continue
            kind = declared[name][0]
            if isinstance(kind, list) and not isinstance(value, list) and value not in kind:
                # File choices depend on what is on disk; only flag fixed enums.
                if not any(str(k).endswith((".safetensors", ".gguf")) for k in kind) and kind:
                    problems.append(f"{nid} {cls}: {name}={value!r} not in {kind[:8]}")
            if kind == "COMBO" and isinstance(declared[name][1], dict):
                options = declared[name][1].get("options") or []
                if options and not isinstance(value, list) and value not in options:
                    if not any(str(k).endswith((".safetensors", ".gguf")) for k in options):
                        problems.append(f"{nid} {cls}: {name}={value!r} not in {options[:8]}")
    return problems


async def main() -> int:
    app = App()
    await app.start()
    engine = app.engines["comfy"]
    try:
        await engine.ensure_running(lambda p, m: m and print(m))
        async with httpx.AsyncClient(timeout=120) as client:
            info = (await client.get(engine.url("/object_info"))).json()
        failed = 0
        for model in app.registry.models.values():
            if model.engine != "comfy":
                continue
            for variant in model.variants:
                builder = graphs.BUILDERS[variant.workflow]
                names = {f.id: f.filename for f in app.registry.expand(variant.files)}
                images = {k: "check.png" for k in graphs.IMAGE_INPUTS.get(variant.workflow, ())}
                graph, _ = builder(
                    prompt="test",
                    seed=1,
                    aspect="16:9",
                    names=names,
                    settings=model.settings,
                    hw=app.hw,
                    prefix="check",
                    images=images,
                    params={"scale": 2, "retain": True, "seconds": 3, "smooth": True},
                )
                problems = check(graph, info)
                label = f"{model.id} [{variant.workflow}, {variant.when or 'any'}]"
                if problems:
                    failed += 1
                    print(f"FAIL {label}")
                    for p in problems:
                        print(f"     {p}")
                else:
                    print(f"ok   {label}")
        return 1 if failed else 0
    finally:
        await app.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
