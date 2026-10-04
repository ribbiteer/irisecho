# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

import pytest

from irisecho_core import registry
from irisecho_core.engines.comfy import graphs

HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282)


def flux_graph(images=None, **params):
    model = registry.default().models["flux-krea"]
    names = {f: f"{f}.safetensors" for f in model.variants[0].files}
    return graphs.flux(
        prompt="a fox",
        seed=1,
        aspect="16:9",
        names=names,
        settings=model.settings,
        hw=HW,
        prefix="x",
        images=images or {},
        params=params,
    )[0]


def test_krea_runs_its_developers_step_count():
    g = flux_graph()
    assert g["sigmas"]["inputs"]["steps"] == 28
    assert g["sigmas"]["inputs"]["denoise"] == 1.0
    assert g["latent"]["class_type"] == "EmptySD3LatentImage"
    assert (g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]) == (1360, 768)
