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


def test_flux_redraws_a_picture_at_a_given_size():
    g = flux_graph({"image1": "in.png"}, width=1920, height=1088)
    assert g["init"]["inputs"]["image"] == "in.png"
    assert g["init_scale"]["class_type"] == "ImageScale"
    assert (g["init_scale"]["inputs"]["width"], g["init_scale"]["inputs"]["height"]) == (1920, 1088)
    assert g["latent"] == {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["init_scale", 0], "vae": ["vae", 0]},
    }
    assert g["sigmas"]["inputs"]["denoise"] == 0.2
    assert (g["shift"]["inputs"]["width"], g["shift"]["inputs"]["height"]) == (1920, 1088)


def test_flux_redraw_keeps_the_pictures_aspect_by_default():
    g = flux_graph({"image1": "in.png"}, denoise=0.3)
    assert g["init_scale"]["class_type"] == "ImageScaleToTotalPixels"
    assert g["init_scale"]["inputs"]["resolution_steps"] == 16
    assert g["sigmas"]["inputs"]["denoise"] == 0.3


def test_flux_ignores_denoise_without_a_picture():
    assert flux_graph(denoise=0.3)["sigmas"]["inputs"]["denoise"] == 1.0


def test_flux_size_snaps_to_multiples_of_16():
    g = flux_graph(width=1100, height=700)
    assert (g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]) == (1088, 688)


@pytest.mark.parametrize(
    "params",
    [
        {"width": 2048, "height": 2048},
        {"width": 128, "height": 128},
        {"width": 1920},
        {"width": "wide", "height": 768},
    ],
)
def test_flux_refuses_sizes_outside_what_fits(params):
    with pytest.raises(ValueError, match="width and height"):
        flux_graph(**params)


@pytest.mark.parametrize("denoise", [0, 0.01, 1.5])
def test_flux_refuses_denoise_out_of_range(denoise):
    with pytest.raises(ValueError, match="denoise"):
        flux_graph({"image1": "in.png"}, denoise=denoise)


def test_flux_pictures_are_staged():
    assert graphs.IMAGE_INPUTS["flux-nunchaku"] == ("image1",)


def wan_graph(model_id="wan22-i2v", hw=HW, **params):
    model = registry.default().models[model_id]
    names = {f: f"{f}.bin" for f in model.variants[0].files}
    return graphs.wan22(
        prompt="The camera pushes in.",
        seed=1,
        aspect="16:9",
        names=names,
        settings=model.settings,
        hw=hw,
        prefix="x",
        images={"start": "s.png"},
        params={"seconds": 4, **params},
    )[0]


def test_wan_keeps_the_v1_distill_by_default():
    g = wan_graph()
    assert g["lora_high"]["inputs"]["lora_name"] == "wan-i2v-lora-high.bin"
    assert g["lora_low"]["inputs"]["lora_name"] == "wan-i2v-lora-low.bin"


def test_wan_camera_moves_model_uses_the_1022_distill():
    g = wan_graph("wan22-i2v-1022")
    assert g["lora_high"]["inputs"]["lora_name"] == "wan-i2v-lora-1022-high.bin"
    assert g["lora_low"]["inputs"]["lora_name"] == "wan-i2v-lora-1022-low.bin"


@pytest.mark.parametrize(
    ("size", "dims"),
    [(None, (848, 480)), ("standard", (848, 480)), ("large", (1024, 576)), ("720p", (1280, 720))],
)
def test_wan_sizes(size, dims):
    inputs = wan_graph(size=size)["frames"]["inputs"]
    assert (inputs["width"], inputs["height"]) == dims
    assert inputs["length"] == 65


def test_wan_refuses_an_unknown_size():
    with pytest.raises(ValueError, match="size"):
        wan_graph(size="1080p")


def test_wan_720p_needs_a_12_gb_card():
    small = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.6)], vram_mb=8192)
    with pytest.raises(ValueError, match="12 GB"):
        wan_graph(hw=small, size="720p")
    assert wan_graph(hw=small, size="large")


def test_comfy_release_waits_until_memory_is_back():
    import asyncio

    from irisecho_core.engines.comfy import until_released

    held = [8 << 30, 6 << 30, 100 << 20]

    async def stats():
        return {"devices": [{"torch_vram_total": held.pop(0)}]}

    assert asyncio.run(until_released(stats, every=0.001))
    assert not held


def test_comfy_release_gives_up_after_a_while():
    import asyncio

    from irisecho_core.engines.comfy import until_released

    async def stats():
        return {"devices": [{"torch_vram_total": 8 << 30}]}

    assert not asyncio.run(until_released(stats, wait=0.01, every=0.001))
