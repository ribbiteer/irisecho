# SPDX-License-Identifier: AGPL-3.0-or-later
"""Advanced controls of the Video, Upscale and 3D studios reach the graphs they belong to."""

from types import SimpleNamespace

import pytest

from irisecho_core import controls, registry
from irisecho_core.controls import ControlError
from irisecho_core.engines import comfy
from irisecho_core.engines.comfy import graphs

HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")


def model(mid: str):
    return registry.default().models[mid]


def names_for(mid: str) -> dict:
    reg = registry.default()
    out = {f: f"{f}.safetensors" for f in model(mid).variants[0].files}
    out |= {s.id: s.filename for s in reg.expand(model(mid).variants[0].files)}
    return out


def build(mid: str, images=None, **params):
    m = model(mid)
    builder = graphs.BUILDERS[m.variants[0].workflow]
    return builder(
        prompt="a fox",
        seed=5,
        aspect="16:9",
        names=names_for(mid),
        settings=m.settings,
        hw=HW,
        prefix="x",
        images=images or {},
        params={"seconds": 3, **params},
    )[0]


def checked(mid: str, **params) -> dict:
    out, warnings = controls.check(model(mid), params)
    assert warnings == []
    return out


# --- every control is in the registry with the range and default the graph uses -----

EXPECTED = {
    "ltx25": {
        "motion": (18, 0, 50),
        "start_strength": (0.7, 0.5, 1.0),
        "end_strength": (0.7, 0.3, 1.0),
        "sound": (True, None, None),
        "sampler": ("euler_ancestral", None, None),
        "interpolation": (2, None, None),
    },
    "wan22-i2v-1022": {"distill_high": (1.0, 0.5, 1.4), "distill_low": (1.0, 0.5, 1.4)},
    "wan22-t2v": {"distill_high": (1.0, 0.5, 1.4), "distill_low": (1.0, 0.5, 1.4)},
    "hunyuan15-i2v": {"steps": (8, None, None), "sr_noise": (0.7, 0.4, 0.9)},
    "seedvr2": {"color": ("lab", None, None), "tile": (512, None, None)},
    "seedvr2-video": {"color": ("lab", None, None)},
    "trellis2": {
        "triangles": (500_000, 5_000, 1_000_000),
        "texture_size": (2048, None, None),
        "smoothing": (3, 0, 8),
        "crease": (180, None, None),
        "keep_small": (0.01, None, None),
        "steps": (12, 8, 30),
        "guidance": (7.5, 3, 10),
    },
    "object-views": {"back_words": ("", None, None)},
}


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_controls_have_the_expected_defaults_and_ranges(mid):
    for name, (default, lo, hi) in EXPECTED[mid].items():
        c = model(mid).control(name)
        assert c is not None, f"{mid} has no {name} control"
        assert c.default == default
        assert (c.min, c.max) == (lo, hi)


def test_pixal3d_has_the_same_3d_controls_as_trellis2():
    assert [c.name for c in model("pixal3d").controls] == [
        c.name for c in model("trellis2").controls
    ]


def test_wan_loras_are_pairs_of_the_wan_family():
    for mid in ("wan22-i2v", "wan22-i2v-1022", "wan22-t2v"):
        c = model(mid).control("loras")
        assert c.pair and c.family == "wan14b"
    assert (
        model("wan22-i2v").control("distill_high") is None
    )  # the merged files have no distill LoRA


def test_ranges_are_enforced_from_the_registry():
    with pytest.raises(ControlError):
        checked("ltx25", motion=51)
    with pytest.raises(ControlError):
        checked("ltx25", start_strength=0.4)
    with pytest.raises(ControlError):
        checked("wan22-t2v", distill_high=1.5)
    with pytest.raises(ControlError):
        checked("hunyuan15-i2v", steps=10)
    with pytest.raises(ControlError):
        checked("trellis2", triangles=4_000)
    with pytest.raises(ControlError):
        checked("trellis2", crease=45)
    assert checked("hunyuan15-i2v", steps="12") == {"steps": 12}
    assert checked("trellis2", keep_small=0, crease="60")["crease"] == 60


# --- LTX -----------------------------------------------------------------------------


def test_ltx_motion_and_strengths():
    g = build("ltx25", {"start": "s.png", "end": "e.png"})
    assert g["first"]["inputs"]["img_compression"] == 18
    assert g["first_in"]["inputs"]["strength"] == 0.7
    assert g["last_in"]["inputs"]["strength"] == 0.7
    g = build(
        "ltx25",
        {"start": "s.png", "end": "e.png"},
        motion=30,
        start_strength=0.55,
        end_strength=0.9,
    )
    assert g["first"]["inputs"]["img_compression"] == 30
    assert g["last"]["inputs"]["img_compression"] == 30
    assert g["first_in"]["inputs"]["strength"] == 0.55
    assert g["last_in"]["inputs"]["strength"] == 0.9
    assert g["first_in2"]["inputs"]["strength"] == 1.0  # the refining pass is not exposed


def test_ltx_without_sound_has_no_audio_decode_and_still_saves():
    g = build("ltx25", {"start": "s.png"})
    assert g["sound"]["class_type"] == "LTXVAudioVAEDecode"
    assert g["video"]["inputs"]["audio"] == ["sound", 0]
    quiet = build("ltx25", {"start": "s.png"}, sound=False)
    assert "sound" not in quiet and "audio" not in quiet["video"]["inputs"]
    assert quiet["save"]["inputs"]["video"] == ["video", 0]
    for node in quiet.values():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                assert value[0] in quiet


def test_ltx_sampler_choice_reaches_both_stages():
    g = build("ltx25", {"start": "s.png"})
    assert g["sampler1"]["inputs"]["sampler_name"] == "euler_ancestral"
    g = build("ltx25", {"start": "s.png"}, sampler="euler")
    assert g["sampler1"]["inputs"]["sampler_name"] == g["sampler2"]["inputs"]["sampler_name"]
    assert g["sampler2"]["inputs"]["sampler_name"] == "euler"


# --- Wan -----------------------------------------------------------------------------


def test_wan_distill_strengths_are_set_per_expert():
    g = build("wan22-i2v-1022", {"start": "s.png"})
    assert g["lora_high"]["inputs"]["strength_model"] == 1.0
    g = build("wan22-i2v-1022", {"start": "s.png"}, distill_high=0.7, distill_low=1.2)
    assert g["lora_high"]["inputs"]["strength_model"] == 0.7
    assert g["lora_low"]["inputs"]["strength_model"] == 1.2


def test_wan_loras_follow_the_distill_on_their_own_expert():
    loras = [
        {"high": "cam-high.safetensors", "low": "cam-low.safetensors", "strength": 0.8},
        {"low": "style-low.safetensors", "strength": 1.0},
    ]
    g = build("wan22-i2v-1022", {"start": "s.png"}, loras=loras)
    assert g["xlora_high1"]["inputs"]["model"] == ["lora_high", 0]
    assert g["xlora_high1"]["inputs"]["lora_name"] == "cam-high.safetensors"
    assert g["xlora_high1"]["inputs"]["strength_model"] == 0.8
    assert g["xlora_low1"]["inputs"]["model"] == ["lora_low", 0]
    assert g["xlora_low2"]["inputs"]["model"] == ["xlora_low1", 0]
    assert "xlora_high2" not in g
    assert g["shift_high"]["inputs"]["model"] == ["xlora_high1", 0]
    assert g["shift_low"]["inputs"]["model"] == ["xlora_low2", 0]


def test_wan_merged_model_takes_loras_straight_on_the_experts():
    g = build(
        "wan22-i2v",
        {"start": "s.png"},
        loras=[{"high": "h.safetensors", "low": "l.safetensors", "strength": 1.0}],
    )
    assert g["xlora_high1"]["inputs"]["model"] == ["unet_high", 0]
    assert g["xlora_low1"]["inputs"]["model"] == ["unet_low", 0]
    assert g["shift_low"]["inputs"]["model"] == ["xlora_low1", 0]


# --- Hunyuan -------------------------------------------------------------------------


def test_hunyuan_steps_and_sr_noise():
    g = build("hunyuan15-i2v", {"start": "s.png"}, size="720p")
    assert g["sample"]["inputs"]["steps"] == 8
    assert g["sr"]["inputs"]["noise_augmentation"] == 0.7
    g = build("hunyuan15-i2v", {"start": "s.png"}, size="720p", steps=12, sr_noise=0.5)
    assert g["sample"]["inputs"]["steps"] == 12
    assert g["sr"]["inputs"]["noise_augmentation"] == 0.5
    assert g["sample_sr"]["inputs"]["steps"] == 6  # the super-resolution pass keeps its recipe


# --- smoothing -----------------------------------------------------------------------


@pytest.mark.parametrize("mid", ["ltx25", "wan22-i2v", "hunyuan15-i2v"])
def test_interpolation_multiplier_and_fps(mid):
    base = {"start": "s.png"}
    assert build(mid, base, smooth=True)["interp"]["inputs"]["multiplier"] == 2
    fps = model(mid).settings["video"]["fps"]
    g = build(mid, base, smooth=True, interpolation=4)
    assert g["interp"]["inputs"]["multiplier"] == 4
    assert g["video"]["inputs"]["fps"] == fps * 4
    # without smoothing the multiplier is not used
    assert "interp" not in build(mid, base, interpolation=4)


def test_interpolation_only_applies_when_smooth_is_on():
    assert model("ltx25").control("interpolation").needs == "smooth"


# --- upscale -------------------------------------------------------------------------


def test_seedvr2_color_and_tile():
    g = build("seedvr2", {"image1": "a.png"}, scale=2)
    assert g["post"]["inputs"]["color_correction_method"] == "lab"
    assert g["encode"]["inputs"]["tile_size"] == g["decode"]["inputs"]["tile_size"] == 512
    g = build("seedvr2", {"image1": "a.png"}, scale=2, color="wavelet", tile=1024)
    assert g["post"]["inputs"]["color_correction_method"] == "wavelet"
    assert g["encode"]["inputs"]["tile_size"] == g["decode"]["inputs"]["tile_size"] == 1024


def test_seedvr2_video_color_and_sound():
    g = build("seedvr2-video", {"video": "c.mp4"}, color="none")
    assert g["post"]["inputs"]["color_correction_method"] == "none"
    assert g["video"]["inputs"]["audio"] == ["parts", 1]
    assert g["video"]["inputs"]["fps"] == ["parts", 2]


def test_upscale_refuses_more_than_16_8_megapixels():
    check = comfy.check_upscale_size
    check(1024, 1024, 4, False)  # exactly 4096 x 4096
    check(2048, 2048, 2, False)
    with pytest.raises(ValueError, match="megapixels"):
        check(1025, 1024, 4, False)
    with pytest.raises(ValueError, match="4000 x 6000"):
        check(2000, 3000, 2, False)
    check(3000, 3000, 4, True)  # keeping the original size is allowed


def test_upscale_reads_the_size_from_the_file(tmp_path):
    from PIL import Image

    big = tmp_path / "big.png"
    Image.new("RGB", (2500, 2500)).save(big)
    with pytest.raises(ValueError, match="more than"):
        comfy.check_upscale_input(big, {"scale": 2})
    comfy.check_upscale_input(big, {"scale": 2, "retain": True})
    comfy.check_upscale_input(tmp_path / "missing.png", {"scale": 4})  # engine reports it


# --- 3D ------------------------------------------------------------------------------


def build3d(mid: str, **params):
    images = {"front": "f.png", "back": "b.png"}
    return build(mid, images, **params)


@pytest.mark.parametrize("mid", ["trellis2", "pixal3d"])
def test_3d_controls_reach_the_graph(mid):
    g = build3d(mid, triangles=33_000, texture_size=1024, smoothing=5, crease=60, keep_small=0)
    assert g["decimate"]["inputs"]["target_face_count"] == 33_000
    assert g["unwrap"]["inputs"]["resolution"] == 1024
    assert g["bake"]["inputs"]["texture_size"] == 1024
    assert g["bake_normal"]["inputs"]["resolution"] == 1024
    assert g["bake_ao"]["inputs"]["resolution"] == 1024  # unchanged
    assert g["remesh"]["inputs"]["smooth_iters"] == 5
    assert g["remesh"]["inputs"]["drop_small_components"] == 0
    assert g["smooth"]["inputs"]["crease_angle"] == 60.0


@pytest.mark.parametrize("mid", ["trellis2", "pixal3d"])
def test_3d_steps_and_guidance(mid):
    g = build3d(mid, steps=20, guidance=5.0)
    for name in ("sample_structure", "sample_shape", "sample_detail", "sample_texture"):
        assert g[name]["inputs"]["steps"] == 20
    for name in ("sample_structure", "sample_shape", "sample_detail"):
        assert g[name]["inputs"]["cfg"] == 5.0
    assert g["sample_texture"]["inputs"]["cfg"] == 1.0
    g = build3d(mid)
    assert g["sample_texture"]["inputs"]["steps"] == 12
    assert g["sample_shape"]["inputs"]["cfg"] == 7.5


def test_triangles_replace_the_triangle_choice():
    assert (
        build3d("trellis2", faces=20_000, triangles=75_000)["decimate"]["inputs"][
            "target_face_count"
        ]
        == 75_000
    )
    # without it the three presets still apply, and only they
    assert build3d("trellis2", faces=100_000)["decimate"]["inputs"]["target_face_count"] == 100_000
    with pytest.raises(ValueError):
        build3d("trellis2", faces=12_345)
    with pytest.raises(ValueError, match="between"):
        build3d("trellis2", triangles=100)


def test_back_words_follow_the_fixed_wording():
    names = names_for("object-views")
    plain = graphs.views_back(names=names, image="f.png", attempt=0, seed=3, prefix="x")
    assert plain["q_text"]["inputs"]["prompt"] == graphs.BACK_PROMPTS[0]
    same = graphs.views_back(
        names=names, image="f.png", attempt=0, seed=3, prefix="x", back_words="  "
    )
    assert same == plain
    g = graphs.views_back(
        names=names, image="f.png", attempt=1, seed=3, prefix="x", back_words=" has a zipper "
    )
    assert g["q_text"]["inputs"]["prompt"] == graphs.BACK_PROMPTS[1] + " has a zipper"
