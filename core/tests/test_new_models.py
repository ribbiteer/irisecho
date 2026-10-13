# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four models added in 0.2.1: Z-Image with a guide picture, SeedVR2 7B, Pixal3D from
one picture, and Wan 2.2 camera control. Their graphs are complete and wired as the
ComfyUI templates wire them; the models they sit beside are held by test_graph_parity."""

from types import SimpleNamespace

import pytest

from irisecho_core import controls, registry
from irisecho_core.engines.comfy import graphs

HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")


def model(mid: str):
    return registry.default().models[mid]


def names_for(mid: str, variant: int = 0) -> dict:
    reg = registry.default()
    return {s.id: s.filename for s in reg.expand(model(mid).variants[variant].files)}


def build(mid: str, images=None, aspect="16:9", variant: int = 0, **params):
    m = model(mid)
    builder = graphs.BUILDERS[m.variants[variant].workflow]
    return builder(
        prompt="a fox",
        seed=5,
        aspect=aspect,
        names=names_for(mid, variant),
        settings=m.settings,
        hw=HW,
        prefix="x",
        images=images or {},
        params=params,
    )


def check_links(g: dict):
    for nid, node in g.items():
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                assert value[0] in g, f"{nid}.{key} reads from missing node {value[0]}"


def classes(g: dict) -> list[str]:
    return [n["class_type"] for n in g.values()]


# --- registry ---------------------------------------------------------------------------


def test_new_models_are_registered():
    reg = registry.default()
    kinds = {
        "z-image-control": "image",
        "seedvr2-7b": "upscale",
        "pixal3d-single": "model3d",
        "wan22-camera": "video",
    }
    for mid, kind in kinds.items():
        m = reg.models[mid]
        assert m.kind == kind and m.engine == "comfy"
        assert m.license in reg.licenses
        for v in m.variants:
            assert v.workflow in graphs.BUILDERS
            for f in reg.expand(v.files):
                assert f.pinned and f.size > 0


def test_new_models_add_no_download_to_old_ones():
    """Registry variants need all their files, so old models keep exactly their lists."""
    before = {
        "z-image-turbo": ["zimage-int4", "qwen3-4b", "flux-ae"],
        "seedvr2": ["seedvr2-3b", "seedvr2-vae"],
        "pixal3d": ["pixal3d-mv-int8", "trellis2-shape-vae", "trellis2-texture-vae", "dinov3-naf"],
    }
    for mid, files in before.items():
        assert list(model(mid).variants[0].files)[: len(files)] == files
    assert "birefnet" in model("pixal3d").variants[0].files


def test_z_image_control_files():
    for v, dit in zip(
        model("z-image-control").variants, ("zimage-int4", "zimage-fp4"), strict=True
    ):
        assert list(v.files) == [
            dit,
            "qwen3-4b",
            "flux-ae",
            "zimage-fun-union",
            "da3-small",
            "sdpose-wholebody",
        ]
    assert {v.when["quant"] for v in model("z-image-control").variants} == {"int4", "fp4"}


def test_seedvr2_7b_uses_its_own_model_file():
    assert list(model("seedvr2-7b").variants[0].files) == ["seedvr2-7b", "seedvr2-vae"]
    assert [c.name for c in model("seedvr2-7b").controls] == [
        c.name for c in model("seedvr2").controls
    ]


def test_licences_name_the_sd2_heritage_of_the_pose_reader():
    lic = registry.default().licenses[model("z-image-control").license]
    assert lic.commercial and "Stable Diffusion 2" in lic.summary


def test_guide_and_camera_are_base_params_not_controls():
    for mid, name in (("z-image-control", "guide"), ("z-image-control", "guide_type")):
        assert name in controls.BASE["image"] and model(mid).control(name) is None
    assert "camera" in controls.BASE["video"] and model("wan22-camera").control("camera") is None
    out, warnings = controls.check(
        model("z-image-control"), {"guide": "a.png", "guide_type": "pose", "guide_strength": 0.5}
    )
    assert warnings == [] and out["guide_strength"] == 0.5
    out, warnings = controls.check(model("wan22-camera"), {"camera": "zoom_in", "start": "s.png"})
    assert warnings == []


def test_control_ranges_and_defaults():
    c = model("z-image-control").control("guide_strength")
    assert (c.default, c.min, c.max) == (0.75, 0.3, 1.0)
    with pytest.raises(controls.ControlError):
        controls.check(model("z-image-control"), {"guide_strength": 1.5})
    c = model("wan22-camera").control("camera_speed")
    assert c.default == 1.0
    with pytest.raises(controls.ControlError):
        controls.check(model("wan22-camera"), {"camera_speed": 11})


# --- Z-Image with a guide picture ---------------------------------------------------------


@pytest.mark.parametrize("variant", [0, 1])
@pytest.mark.parametrize("kind", graphs.GUIDE_TYPES)
def test_z_image_control_graph(kind, variant):
    g, save = build(
        "z-image-control", {"guide": "g.png"}, guide_type=kind, variant=variant, aspect="16:9"
    )
    check_links(g)
    assert g[save]["class_type"] == "SaveImage"
    assert g["unet"]["class_type"] == "NunchakuZImageDiTLoader"
    assert g["patch_file"]["inputs"]["name"] == "Z-Image-Turbo-Fun-Controlnet-Union.safetensors"
    # Nunchaku's example: loader -> ControlNet patch -> AuraFlow shift -> sampler
    assert g["control"]["inputs"]["model"] == ["unet", 0]
    assert g["shift"]["inputs"]["model"] == ["control", 0]
    assert g["guider"]["inputs"]["model"] == ["shift", 0]
    assert g["control"]["inputs"]["strength"] == 0.75
    expected = {
        "canny": ["Canny"],
        "depth": ["LoadDA3Model", "DA3Inference", "DA3Render"],
        "pose": ["CheckpointLoaderSimple", "SDPoseKeypointExtractor", "SDPoseDrawKeypoints"],
        "as_is": [],
    }[kind]
    present = classes(g)
    for cls in ("Canny", "LoadDA3Model", "CheckpointLoaderSimple"):
        assert (cls in present) == (cls in expected)
    assert all(c in present for c in expected)
    if kind == "as_is":
        assert g["control"]["inputs"]["image"] == ["guide_fit", 0]
    else:
        assert g["control"]["inputs"]["image"] == ["map", 0]
    # the guide is cut to the job's shape; the latent has the same size
    fit, latent = g["guide_fit"]["inputs"], g["latent"]["inputs"]
    assert fit["crop"] == "center"
    assert (fit["width"], fit["height"]) == (latent["width"], latent["height"])
    assert latent["width"] > latent["height"]
    assert g["sigmas"]["inputs"]["steps"] == 9
    assert g["sampler"]["inputs"]["sampler_name"] == "res_multistep"


def test_z_image_control_follows_the_job_size_and_controls():
    g, _ = build(
        "z-image-control",
        {"guide": "g.png"},
        aspect="1:1",
        megapixels=2.0,
        guide_strength=0.5,
        steps=7,
        shift=4.0,
        sampler="euler",
        scheduler="beta",
    )
    assert g["latent"]["inputs"]["width"] == g["latent"]["inputs"]["height"] == 1440
    assert g["control"]["inputs"]["strength"] == 0.5
    assert g["shift"]["inputs"]["shift"] == 4.0
    assert g["sigmas"]["inputs"]["steps"] == 7
    assert g["sigmas"]["inputs"]["scheduler"] == "beta"
    assert g["sampler"]["inputs"]["sampler_name"] == "euler"


def test_z_image_control_loras_sit_between_the_model_and_the_patch():
    g, _ = build(
        "z-image-control",
        {"guide": "g.png"},
        loras=[
            {"file": "a.safetensors", "strength": 0.8},
            {"file": "b.safetensors", "strength": 1},
        ],
    )
    assert g["lora1"]["inputs"]["model"] == ["unet", 0]
    assert g["lora2"]["inputs"]["model"] == ["lora1", 0]
    assert g["control"]["inputs"]["model"] == ["lora2", 0]


def test_z_image_control_needs_a_guide_and_a_known_type():
    with pytest.raises(ValueError, match="guide picture"):
        build("z-image-control", {})
    with pytest.raises(ValueError, match="guide type"):
        build("z-image-control", {"guide": "g.png"}, guide_type="sketch")


def test_z_image_control_default_type_is_canny():
    g, _ = build("z-image-control", {"guide": "g.png"})
    assert g["map"]["class_type"] == "Canny"
    assert (g["map"]["inputs"]["low_threshold"], g["map"]["inputs"]["high_threshold"]) == (
        0.1,
        0.32,
    )


def test_z_image_control_reads_the_guide_upload():
    assert graphs.IMAGE_INPUTS["z-image-control"] == ("guide",)
    assert "z-image-control" not in graphs.DYNAMIC_VRAM  # Nunchaku's loader needs real weights


# --- SeedVR2 7B ---------------------------------------------------------------------------


def test_seedvr2_7b_graph_uses_the_7b_file():
    g, save = build("seedvr2-7b", {"image1": "a.png"}, scale=2)
    check_links(g)
    assert g["unet"]["inputs"]["unet_name"] == "seedvr2_7b_int8_convrot.safetensors"
    assert g["vae"]["inputs"]["vae_name"] == "ema_vae_fp16.safetensors"
    g3, _ = build("seedvr2", {"image1": "a.png"}, scale=2)
    assert g3["unet"]["inputs"]["unet_name"] == "seedvr2_3b_int8_convrot.safetensors"
    del g["unet"], g3["unet"]
    assert g == g3  # nothing else differs


def test_seedvr2_7b_controls_reach_the_graph():
    g, _ = build("seedvr2-7b", {"image1": "a.png"}, scale=3, color="wavelet", tile=768)
    assert g["post"]["inputs"]["color_correction_method"] == "wavelet"
    assert g["encode"]["inputs"]["tile_size"] == 768


# --- Pixal3D from one picture ---------------------------------------------------------------


def test_pixal3d_single_graph():
    g, save = build("pixal3d-single", {"front": "f.png"})
    check_links(g)
    assert g[save]["class_type"] == "SaveGLB"
    assert g["unet"]["inputs"]["unet_name"] == "pixal3d_int8_convrot.safetensors"
    assert g["moge"]["inputs"]["model_name"] == "moge_2_vitl_normal_fp16.safetensors"
    assert g["crop"]["inputs"]["pad_factor"] == 1.1
    # MoGe reads the crop, and its horizontal field of view feeds the conditioning
    assert g["geometry"]["inputs"]["image"] == ["crop", 0]
    assert g["fov"]["inputs"]["axis"] == "horizontal" and g["fov"]["inputs"]["unit"] == "degrees"
    cond = g["cond"]
    assert cond["class_type"] == "Pixal3DConditioning"
    assert cond["inputs"]["camera_angle_x"] == ["fov", 0]
    assert cond["inputs"]["image"] == ["crop", 0]
    # the shared finishing: every stage samples with Pixal3D's model
    for name in ("sample_structure", "sample_shape", "sample_detail", "sample_texture"):
        assert g[name]["inputs"]["steps"] == 12
    assert g["sample_texture"]["inputs"]["model"] == ["unet", 0]
    assert g["thumb"]["class_type"] == "SaveImage"


def test_pixal3d_single_shares_the_core_controls():
    assert [c.name for c in model("pixal3d-single").controls] == [
        c.name for c in model("pixal3d").controls
    ]
    g, _ = build("pixal3d-single", {"front": "f.png"}, faces=20_000, textures=False, steps=16)
    assert "sample_texture" not in g
    assert g["decimate"]["inputs"]["target_face_count"] == 20_000
    assert g["sample_shape"]["inputs"]["steps"] == 16
    g, _ = build("pixal3d-single", {"front": "f.png"}, triangles=75_000, texture_size=1024)
    assert g["decimate"]["inputs"]["target_face_count"] == 75_000
    assert g["bake"]["inputs"]["texture_size"] == 1024


def test_pixal3d_single_is_a_3d_workflow_with_one_picture():
    assert "pixal3d-single" in graphs.MODEL3D
    assert graphs.IMAGE_INPUTS["pixal3d-single"] == ("front",)
    assert "pixal3d-single" in graphs.PROMPT_OPTIONAL
    with pytest.raises(ValueError, match="picture"):
        build("pixal3d-single", {})


def test_the_other_3d_graphs_do_not_gain_moge():
    for mid in ("trellis2", "pixal3d"):
        g, _ = build(mid, {"front": "f.png", "back": "b.png"})
        assert "LoadMoGeModel" not in classes(g)


def test_cli_offers_pixal3d_single():
    from irisecho_core import cli

    with pytest.raises(SystemExit) as stop:
        cli.main(["3d", "x.png", "--model", "nonsense"])
    assert stop.value.code == 2
    # a valid choice gets past argument parsing (the picture is missing, which is reported)
    assert cli.main(["3d", "no-such-file.png", "--model", "pixal3d-single"]) != 2


# --- Wan 2.2 camera control -----------------------------------------------------------------


def test_wan22_camera_graph():
    g, save = build("wan22-camera", {"start": "s.png"}, camera="zoom_in", seconds=3)
    check_links(g)
    assert g[save]["class_type"] == "SaveVideo"
    names = names_for("wan22-camera")
    assert g["unet_high"]["inputs"]["unet_name"] == names["wan-fun-camera-high"]
    assert g["unet_low"]["inputs"]["unet_name"] == names["wan-fun-camera-low"]
    # the template's own pair: the high-noise LoRA on the high expert, the low on the low
    assert g["lora_high"]["inputs"]["model"] == ["unet_high", 0]
    assert g["lora_high"]["inputs"]["lora_name"] == names["wan-i2v-lora-v1-high"]
    assert g["lora_low"]["inputs"]["model"] == ["unet_low", 0]
    assert g["lora_low"]["inputs"]["lora_name"] == names["wan-i2v-lora-v1-low"]
    assert g["shift_high"]["inputs"] == {"model": ["lora_high", 0], "shift": 8.0}
    assert g["shift_low"]["inputs"] == {"model": ["lora_low", 0], "shift": 8.0}
    high, low = g["sample_high"]["inputs"], g["sample"]["inputs"]
    assert (high["steps"], high["cfg"], high["start_at_step"], high["end_at_step"]) == (
        4,
        1.0,
        0,
        2,
    )
    assert (low["steps"], low["cfg"], low["start_at_step"]) == (4, 1.0, 2)
    assert high["add_noise"] == "enable" and low["add_noise"] == "disable"
    assert high["model"] == ["shift_high", 0] and low["model"] == ["shift_low", 0]
    assert low["latent_image"] == ["sample_high", 0]
    assert high["positive"] == ["frames", 0] and high["latent_image"] == ["frames", 2]
    # the start frame and the camera reach the conditioning
    frames = g["frames"]["inputs"]
    assert frames["start_image"] == ["start", 0]
    assert frames["camera_conditions"] == ["camera", 0]
    assert g["video"]["inputs"]["fps"] == 16


def test_wan22_camera_embedding_matches_the_clip():
    g, _ = build("wan22-camera", {"start": "s.png"}, camera="pan_left", seconds=5, size="large")
    cam, frames = g["camera"]["inputs"], g["frames"]["inputs"]
    assert cam["camera_pose"] == "Pan Left"
    for key in ("width", "height", "length"):
        assert cam[key] == frames[key]
    assert frames["length"] % 4 == 1
    assert cam["speed"] == 1.0


def test_wan22_camera_speed_and_smooth():
    g, _ = build("wan22-camera", {"start": "s.png"}, camera="clockwise", camera_speed=2.5)
    assert g["camera"]["inputs"]["camera_pose"] == "ClockWise (CW)"
    assert g["camera"]["inputs"]["speed"] == 2.5
    g, _ = build("wan22-camera", {"start": "s.png"}, smooth=True, interpolation=4)
    assert g["interp"]["inputs"]["multiplier"] == 4
    assert g["video"]["inputs"]["fps"] == 64


def test_wan22_camera_every_move_maps_to_the_nodes_option():
    options = {
        "Static",
        "Pan Up",
        "Pan Down",
        "Pan Left",
        "Pan Right",
        "Zoom In",
        "Zoom Out",
        "Anti Clockwise (ACW)",
        "ClockWise (CW)",
    }
    assert set(graphs.CAMERA_MOVES.values()) == options
    for move, option in graphs.CAMERA_MOVES.items():
        g, _ = build("wan22-camera", {"start": "s.png"}, camera=move)
        assert g["camera"]["inputs"]["camera_pose"] == option


def test_wan22_camera_add_on_loras_chain_per_expert():
    g, _ = build(
        "wan22-camera",
        {"start": "s.png"},
        loras=[{"high": "h.safetensors", "low": "l.safetensors", "strength": 0.7}],
    )
    assert g["xlora_high1"]["inputs"]["model"] == ["lora_high", 0]
    assert g["xlora_low1"]["inputs"]["model"] == ["lora_low", 0]
    assert g["shift_high"]["inputs"]["model"] == ["xlora_high1", 0]
    assert g["shift_low"]["inputs"]["model"] == ["xlora_low1", 0]


def test_wan22_camera_needs_a_start_frame_and_a_known_move():
    with pytest.raises(ValueError, match="first frame"):
        build("wan22-camera", {})
    with pytest.raises(ValueError, match="camera move"):
        build("wan22-camera", {"start": "s.png"}, camera="orbit")


def test_wan22_camera_settings_like_the_other_wan_models():
    video = model("wan22-camera").settings["video"]
    assert video["frames"] == ["start"] and video["needs_frame"] is True
    assert video["fps"] == 16 and set(video["sizes"]) == set(graphs.VIDEO_TIERS)
    assert set(video["pace"]) == set(video["sizes"])
    assert graphs.IMAGE_INPUTS["wan22-camera"] == ("start",)
    assert "wan22-camera" in graphs.DYNAMIC_VRAM
