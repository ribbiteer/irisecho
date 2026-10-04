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


def test_comfy_waits_for_the_history_record():
    import asyncio

    from irisecho_core.engines.comfy import finished_entry

    answers = [{}, {}, {"status": {"status_str": "success"}, "outputs": {"save": {}}}]

    async def history():
        return answers.pop(0)

    entry = asyncio.run(finished_entry(history, every=0.001))
    assert entry["status"]["status_str"] == "success" and not answers


def test_comfy_gives_up_on_a_missing_record():
    import asyncio

    from irisecho_core.engines.comfy import finished_entry

    calls = []

    async def history():
        calls.append(1)
        return {}

    assert asyncio.run(finished_entry(history, wait=0.01, every=0.001)) == {}
    assert len(calls) == 10


def video_upscale_graph(**params):
    model = registry.default().models["seedvr2-video"]
    names = {f: f"{f}.bin" for f in model.variants[0].files}
    return graphs.seedvr2_video(
        seed=1, names=names, prefix="x", images={"video": "clip.mp4"}, params=params
    )[0]


def test_video_upscale_keeps_the_clips_shape_at_1080():
    g = video_upscale_graph()
    assert g["load"]["inputs"]["file"] == "clip.mp4"
    assert g["resize"]["inputs"]["resize_type"] == "scale shorter dimension"
    assert g["resize"]["inputs"]["resize_type.shorter_size"] == 1080
    assert g["post"]["inputs"]["original_resized_images"] == ["even", 0]
    assert g["video"]["inputs"]["fps"] == ["parts", 2]


@pytest.mark.parametrize("short_side", [480, 1440])
def test_video_upscale_refuses_untested_sizes(short_side):
    with pytest.raises(ValueError, match="short_side"):
        video_upscale_graph(short_side=short_side)


def test_video_upscale_needs_a_clip_and_no_prompt():
    with pytest.raises(ValueError, match="clip"):
        graphs.seedvr2_video(seed=1, names={}, prefix="x", images={}, params={})
    assert "seedvr2-video" in graphs.PROMPT_OPTIONAL
    assert graphs.IMAGE_INPUTS["seedvr2-video"] == ("video",)


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


# Step, guidance and cfg values per model. The Flux ones are Nunchaku's example scripts
# (Krea runs its developers' 28 steps instead of 20). Qwen runs Nunchaku's Lightning
# files, 4 or 8 steps at cfg 1, so the quantized models stay fast on a 12 GB card.
NUNCHAKU_RECIPES = {
    "flux-schnell": {"steps": 4},
    "flux-dev": {"steps": 20, "guidance": 3.5},
    "flux-krea": {"steps": 28, "guidance": 4.5},
    "flux-kontext": {"steps": 20, "guidance": 2.5},
    "qwen-image": {"steps": 8, "cfg": 1.0, "shift": 3.0},
    "qwen-image-fast": {"steps": 4, "cfg": 1.0, "shift": 3.0},
    "qwen-edit": {"steps": 8, "cfg": 1.0},
    "qwen-edit-fast": {"steps": 4, "cfg": 1.0},
    "krea-2": {"steps": 8, "cfg": 1.0},
}


@pytest.mark.parametrize("model_id", NUNCHAKU_RECIPES)
def test_registry_follows_the_nunchaku_recipes(model_id):
    settings = registry.default().models[model_id].settings
    for key, value in NUNCHAKU_RECIPES[model_id].items():
        assert settings[key] == value


def _dit_paths(model_id):
    reg = registry.default()
    return [reg.files[v.files[0]].path for v in reg.models[model_id].variants]


@pytest.mark.parametrize(
    "model_id, marker",
    [
        ("qwen-image", "lightningv1.1-8steps"),
        ("qwen-image-fast", "lightningv1.0-4steps"),
        ("qwen-edit", "lightning-8steps-251115"),
        ("qwen-edit-fast", "lightning-4steps-251115"),
    ],
)
def test_qwen_runs_nunchakus_lightning_files(model_id, marker):
    assert all(marker in path for path in _dit_paths(model_id))


def test_qwen_image_lightning_graph_has_no_negative_pass():
    model = registry.default().models["qwen-image"]
    names = {f: f"{f}.safetensors" for f in model.variants[0].files}
    g = graphs.qwen_image(
        prompt="a sign",
        seed=1,
        aspect="1:1",
        names=names,
        settings=model.settings,
        hw=HW,
        prefix="x",
    )[0]
    assert g["guider"]["class_type"] == "BasicGuider"
    assert g["sigmas"]["inputs"]["steps"] == 8
    assert g["shift"]["inputs"]["shift"] == 3.0


def krea2_graph(aspect="16:9"):
    model = registry.default().models["krea-2"]
    names = {f: f"{f}.safetensors" for f in model.variants[0].files}
    return graphs.krea2(
        prompt="a fox",
        seed=1,
        aspect=aspect,
        names=names,
        settings=model.settings,
        hw=HW,
        prefix="x",
    )[0]


def test_krea2_runs_eight_steps_with_a_zeroed_negative():
    g = krea2_graph()
    assert g["clip"]["inputs"]["type"] == "krea2"
    assert g["negative"] == {
        "class_type": "ConditioningZeroOut",
        "inputs": {"conditioning": ["text", 0]},
    }
    assert g["sample"]["inputs"]["steps"] == 8
    assert g["sample"]["inputs"]["cfg"] == 1.0
    assert (g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]) == (1360, 768)
    assert g["decode"]["inputs"]["samples"] == ["sample", 0]


def test_krea2_is_offered_only_on_cuda_13_builds():
    model = registry.default().models["krea-2"]
    assert all(v.when["cuda_tag"] == "cu130" for v in model.variants)
    assert {v.files for v in model.variants} == {model.variants[0].files}
