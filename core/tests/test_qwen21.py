# SPDX-License-Identifier: AGPL-3.0-or-later
"""Qwen-Image 2.1 Turbo, for pictures and edits: wired as ComfyUI's own Turbo templates
(ManualSigmas, euler, cfg 1), opt-in under its research license, and adding nothing to
the models it sits beside (those are held by test_graph_parity)."""

from types import SimpleNamespace

import pytest

from irisecho_core import controls, registry
from irisecho_core.engines.comfy import graphs
from irisecho_core.engines.promptwriter.styles import STYLES, system_prompt

HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")
SHIPPED = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"


def model(mid: str):
    return registry.default().models[mid]


def build(mid: str, images=None, aspect="1:1", prompt="a fox", **params):
    m = model(mid)
    reg = registry.default()
    names = {s.id: s.filename for s in reg.expand(m.variants[0].files)}
    g, save = graphs.BUILDERS[m.variants[0].workflow](
        prompt=prompt,
        seed=5,
        aspect=aspect,
        names=names,
        settings=m.settings,
        hw=HW,
        prefix="x",
        images=images or {},
        params=params,
    )
    for nid, node in g.items():
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                assert value[0] in g, f"{nid}.{key} reads from missing node {value[0]}"
    return g, save


def classes(g: dict) -> list[str]:
    return [n["class_type"] for n in g.values()]


# --- registry ---------------------------------------------------------------------------


def test_both_models_share_one_set_of_files_under_the_research_license():
    reg = registry.default()
    lic = reg.licenses["qwen-research"]
    # Pictures are the person's to use commercially (Qwen's statement); the files are not.
    assert lic.commercial and lic.accept and "research" in lic.condition
    files = {
        mid: list(model(mid).variants[0].files) for mid in ("qwen-image-21", "qwen-image-21-edit")
    }
    assert files["qwen-image-21"] == files["qwen-image-21-edit"]
    assert files["qwen-image-21"] == ["qwen21-turbo-int8", "qwen3vl-8b-w4a8", "qwen21-vae"]
    for mid, kind in (("qwen-image-21", "image"), ("qwen-image-21-edit", "edit")):
        m = model(mid)
        assert m.kind == kind and m.license == "qwen-research"
        assert m.settings["prompt_style"] in STYLES
        # int8 "convrot" files run on ComfyUI's own kernels, which need CUDA 13.
        assert [v.when for v in m.variants] == [{"backend": "cuda", "cuda_tag": "cu130"}]
        for f in reg.expand(m.variants[0].files):
            assert f.pinned and f.size > 0


def test_the_older_qwen_models_keep_their_files():
    assert list(model("qwen-image").variants[0].files)[1:] == ["qwen25vl-fp8", "qwen-image-vae"]
    assert list(model("qwen-edit").variants[0].files)[1:] == ["qwen25vl-fp8", "qwen-image-vae"]


def test_controls_and_their_limits():
    m = model("qwen-image-21")
    assert m.control("megapixels").max == 4.0
    assert m.control("loras").family == "qwen-image-21"
    assert m.control("scheduler").default == "shipped"
    with pytest.raises(controls.ControlError):
        controls.check(m, {"steps": 13})
    out, warnings = controls.check(model("qwen-image-21-edit"), {"cache": "off", "feather": 4})
    assert warnings == [] and out["cache"] == "off"


# --- text to image ----------------------------------------------------------------------


def test_text_to_image_runs_the_shipped_schedule_at_cfg_1():
    g, save = build("qwen-image-21")
    assert g["sigmas"] == {"class_type": "ManualSigmas", "inputs": {"sigmas": SHIPPED}}
    assert g["guider"]["inputs"]["cfg"] == 1.0
    assert g["sampler"]["inputs"]["sampler_name"] == "euler"
    assert g["clip"]["inputs"]["type"] == "qwen_image"
    assert g["text"]["inputs"]["prompt"] == "a fox"
    assert "vae" not in g["text"]["inputs"]
    assert not any(k.startswith("images.") for k in g["text"]["inputs"])
    # 2 MP on a 32-pixel grid, the alpha dropped from a plain picture.
    w, h = g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]
    assert (w % 32, h % 32) == (0, 0) and abs(w * h / 1024**2 - 2.0) < 0.05
    unset = build("qwen-image-21", aspect="16:9")[0]["latent"]["inputs"]
    asked = build("qwen-image-21", aspect="16:9", megapixels=2.0)[0]["latent"]["inputs"]
    assert unset == asked  # the default is the control's default
    assert g[save]["inputs"]["images"] == ["alpha", 0]
    assert "QwenImage21Cache" not in classes(g)


def test_trained_sizes_are_the_model_cards_4_mp_table():
    g, _ = build("qwen-image-21", aspect="16:9", trained_sizes=True)
    assert (g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]) == (2752, 1536)


def test_transparent_pictures_use_the_model_cards_wording_and_keep_alpha():
    g, save = build("qwen-image-21", prompt="a red fox sticker.", transparent=True)
    text = g["text"]["inputs"]["prompt"]
    assert text.startswith("This is an RGBA image with transparency. a red fox sticker.")
    assert text.endswith("the background is transparent.")
    assert g[save]["inputs"]["images"] == ["decode", 0]


def test_a_start_picture_is_image_to_image_on_the_tail_of_the_schedule():
    g, _ = build("qwen-image-21", images={"image1": "a.png"}, denoise=0.4)
    assert g["latent"]["class_type"] == "VAEEncode"
    assert g["init_scale"]["inputs"]["megapixels"] == 2.0
    sigmas = [float(s) for s in g["sigmas"]["inputs"]["sigmas"].split(", ")]
    # The shipped curve, scaled to begin at the denoise level: all eight steps run.
    assert len(sigmas) == 9 and sigmas[0] == 0.4 and sigmas[-1] == 0
    assert sigmas[-2] == round(0.414568 * 0.4, 6)
    assert "vae" not in g["text"]["inputs"]  # a start picture is not a reference


def test_the_lowest_denoise_still_runs_every_step():
    sigmas = graphs.qwen21_sigmas(8, 0.05).split(", ")
    assert len(sigmas) == 9 and float(sigmas[0]) == 0.05


@pytest.mark.parametrize("steps", [4, 6, 12])
def test_other_step_counts_stretch_the_shipped_curve(steps):
    sigmas = [float(s) for s in graphs.qwen21_sigmas(steps).split(", ")]
    assert len(sigmas) == steps + 1
    assert sigmas[0] == 1.0 and sigmas[-2] == 0.414568 and sigmas[-1] == 0
    assert sigmas == sorted(sigmas, reverse=True)


def test_another_scheduler_uses_comfyuis_own():
    g, _ = build("qwen-image-21", scheduler="beta", steps=10)
    assert g["sigmas"]["class_type"] == "BasicScheduler"
    assert g["sigmas"]["inputs"]["scheduler"] == "beta"
    assert g["sigmas"]["inputs"]["steps"] == 10


def test_loras_load_on_the_plain_loader():
    g, _ = build("qwen-image-21", loras=[{"file": "a.safetensors", "strength": 0.8}])
    assert g["lora1"]["class_type"] == "LoraLoaderModelOnly"
    assert g["guider"]["inputs"]["model"] == ["lora1", 0]


# --- editing ----------------------------------------------------------------------------


def test_edit_hands_scaled_pictures_to_the_encoder_and_samples_on_its_latent():
    g, save = build(
        "qwen-image-21-edit",
        images={"image1": "a.png", "image2": "b.png"},
        prompt="Put the cat from Picture 2 on the sofa in Picture 1",
    )
    enc = g["text"]["inputs"]
    assert enc["prompt"] == "Put the cat from <image2> on the sofa in <image1>"
    assert enc["resolution"] == 0 and enc["vae"] == ["vae", 0]
    assert enc["images.image_1"] == ["scale1", 0] and enc["images.image_2"] == ["scale2", 0]
    assert g["scale1"]["inputs"]["resolution_steps"] == 32
    assert g["scale1"]["inputs"]["megapixels"] == 2.0
    assert g["sample"]["inputs"]["latent_image"] == ["text", 2]
    assert g[save]["inputs"]["images"] == ["reshape", 0]
    assert g["reshape"]["inputs"]["image"] == ["alpha", 0]
    # Back to Picture 1's own shape: the size of the original at the working size.
    assert g["shape_ref"]["inputs"] == {
        "image": ["load1", 0],
        "upscale_method": "lanczos",
        "megapixels": 2.0,
        "resolution_steps": 1,
    }
    assert g["reshape"]["inputs"]["width"] == ["shape", 0]


def test_a_skipped_picture_2_renumbers_the_tags():
    g, _ = build(
        "qwen-image-21-edit",
        images={"image1": "a.png", "image3": "c.png"},
        prompt="Give the man in Picture 1 the hat from Picture 3",
    )
    enc = g["text"]["inputs"]
    assert enc["prompt"] == "Give the man in <image1> the hat from <image2>"
    assert enc["images.image_2"] == ["scale2", 0]
    assert g["load2"]["inputs"]["image"] == "c.png"


def test_one_picture_keeps_its_wording():
    g, _ = build("qwen-image-21-edit", images={"image1": "a.png"}, prompt="Make Picture 1 red")
    assert g["text"]["inputs"]["prompt"] == "Make Picture 1 red"


def test_edits_avoid_the_noisy_1024_grid():
    g, _ = build("qwen-image-21-edit", images={"image1": "a.png"}, megapixels=1.0)
    assert g["scale1"]["inputs"]["megapixels"] == 1.05


def test_an_area_edit_is_pasted_back_over_picture_1():
    g, save = build("qwen-image-21-edit", images={"image1": "a.png", "mask": "m.png"}, feather=6)
    assert g[save]["inputs"]["images"] == ["reshape", 0]
    assert g["reshape"]["inputs"]["image"] == ["paste", 0]
    # The clean picture, made opaque, is what the area is pasted over.
    assert g["paste"]["inputs"]["destination"] == ["base", 0]
    assert g["base"]["inputs"]["image"] == ["scale1", 0]
    assert g["paste"]["inputs"]["source"] == ["rgb", 0]
    assert g["mask_size"]["inputs"]["image"] == ["scale1", 0]
    # A mask with no boxes: the model sees one red circle drawn in the graph.
    assert g["mark"]["class_type"] == "IrisEchoMarkArea"
    assert g["mark"]["inputs"] == {"image": ["scale1", 0], "mask": ["mask_load", 0]}
    assert g["text"]["inputs"]["images.image_1"] == ["mark", 0]


def test_boxes_reach_the_model_as_the_engines_circled_copy():
    g, _ = build("qwen-image-21-edit", images={"image1": "a.png", "mask": "m.png", "mark": "k.png"})
    assert g["loadmark"]["inputs"]["image"] == "k.png"
    assert g["text"]["inputs"]["images.image_1"] == ["scalemark", 0]
    assert g["scalemark"]["inputs"]["megapixels"] == g["scale1"]["inputs"]["megapixels"]
    assert "mark" not in g  # no circle drawn in the graph as well
    assert g["paste"]["inputs"]["destination"] == ["base", 0]


def test_a_whole_picture_edit_is_not_marked():
    g, _ = build("qwen-image-21-edit", images={"image1": "a.png"})
    assert "mark" not in g and g["text"]["inputs"]["images.image_1"] == ["scale1", 0]


def test_pictures_keep_their_transparency_on_the_way_in():
    g, _ = build("qwen-image-21-edit", images={"image1": "a.png"})
    assert g["rgba1"] == {
        "class_type": "JoinImageWithAlpha",
        "inputs": {"image": ["load1", 0], "alpha": ["load1", 1]},
    }
    assert g["scale1"]["inputs"]["image"] == ["rgba1", 0]


def test_ten_pictures_with_their_own_tags():
    images = {f"image{i}": f"{i}.png" for i in range(1, 11)}
    g, _ = build(
        "qwen-image-21-edit",
        images=images,
        prompt="The people from Picture 2 to Picture 9 stand around Picture 1 holding Picture 10",
    )
    enc = g["text"]["inputs"]
    assert enc["prompt"] == (
        "The people from <image2> to <image9> stand around <image1> holding <image10>"
    )
    for i in range(1, 11):
        assert enc[f"images.image_{i}"] == [f"scale{i}", 0]
        assert g[f"load{i}"]["inputs"]["image"] == f"{i}.png"


def test_other_pictures_can_be_read_smaller():
    images = {"image1": "a.png", "image2": "b.png", "image3": "c.png"}
    g, _ = build("qwen-image-21-edit", images=images, ref_megapixels=0.5)
    assert g["scale1"]["inputs"]["megapixels"] == 2.0
    assert g["scale2"]["inputs"]["megapixels"] == 0.5
    assert g["scale3"]["inputs"]["megapixels"] == 0.5
    g, _ = build("qwen-image-21-edit", images=images, megapixels=1.5)
    assert g["scale2"]["inputs"]["megapixels"] == 1.5  # unset: they follow the working size


@pytest.mark.parametrize(
    ("pictures", "each"), [(2, 2.0), (3, 1.65), (4, 1.1), (5, 0.82), (10, 0.37)]
)
def test_many_pictures_share_a_size_budget(pictures, each):
    images = {f"image{i}": f"{i}.png" for i in range(1, pictures + 1)}
    g, _ = build("qwen-image-21-edit", images=images)
    assert g["scale1"]["inputs"]["megapixels"] == 2.0  # Picture 1 keeps the working size
    assert g[f"scale{pictures}"]["inputs"]["megapixels"] == each


def test_edit_limits_come_from_the_registry():
    m = model("qwen-image-21-edit")
    assert m.settings["edit"] == {"pictures": 10, "areas": 3}
    out, warnings = controls.check(m, {f"image{i}": f"{i}.png" for i in range(1, 11)})
    assert warnings == []
    three = [[0.1, 0.1, 0.2, 0.2], [0.3, 0.3, 0.4, 0.4], [0.5, 0.5, 0.6, 0.6]]
    controls.check(m, {"image1": "a.png", "box": three})
    with pytest.raises(controls.ControlError, match="up to 3 marked areas"):
        controls.check(m, {"image1": "a.png", "box": [*three, [0.7, 0.7, 0.8, 0.8]]})
    with pytest.raises(controls.ControlError, match="four numbers"):
        controls.check(m, {"image1": "a.png", "box": [0.5, 0.5, 0.2, 0.2]})
    with pytest.raises(controls.ControlError, match="up to 3 pictures"):
        controls.check(model("qwen-edit"), {"image1": "a.png", "image4": "d.png"})
    with pytest.raises(controls.ControlError, match="up to 1 marked area"):
        controls.check(model("qwen-edit"), {"image1": "a.png", "box": three[:2]})
    with pytest.raises(controls.ControlError, match="whole picture"):
        controls.check(model("flux-kontext"), {"image1": "a.png", "box": three[0]})


def test_the_engine_circles_each_area_in_its_own_colour(tmp_path):
    from PIL import Image

    from irisecho_core.engines.comfy import box_mask, mark_areas

    src = tmp_path / "p.png"
    Image.new("RGBA", (400, 200), (128, 128, 128, 255)).save(src)
    boxes = [[0.1, 0.2, 0.3, 0.6], [0.6, 0.2, 0.9, 0.8]]
    box_mask(src, boxes, tmp_path / "m.png")
    mask = Image.open(tmp_path / "m.png")
    assert mask.getpixel((80, 80)) == 255 and mask.getpixel((300, 100)) == 255
    assert mask.getpixel((200, 100)) == 0
    mark_areas(src, boxes, tmp_path / "k.png")
    marked = Image.open(tmp_path / "k.png")
    assert marked.mode == "RGBA" and marked.size == (400, 200)
    pixels = {marked.getpixel((x, y)) for x in range(0, 400, 2) for y in range(0, 200, 2)}
    assert (255, 0, 0, 255) in pixels and (0, 90, 255, 255) in pixels
    assert (0, 200, 60, 255) not in pixels  # two areas, two colours


def test_edit_cache_setting_adds_the_cache_node():
    g, _ = build("qwen-image-21-edit", images={"image1": "a.png"}, cache="cpu")
    assert g["cache"]["inputs"] == {"model": ["unet", 0], "device": "cpu", "dtype": "default"}
    assert g["guider"]["inputs"]["model"] == ["cache", 0]


def test_edit_needs_a_picture():
    with pytest.raises(ValueError):
        build("qwen-image-21-edit")


# --- prompt writer ----------------------------------------------------------------------


def test_writer_styles_follow_qwens_own_rewriter():
    t2i = system_prompt(STYLES["qwen-image-21"], "improve", 0, "a fox in snow")
    assert "The image is a [style]" in t2i and "which side falls into shadow" in t2i
    assert STYLES["qwen-image-21"].max_tokens > 300
    poster = system_prompt(STYLES["qwen-image-21"], "improve", 0, 'a poster that reads "SALE"')
    assert "reading order" in poster
    one = system_prompt(STYLES["qwen-image-21-edit"], "improve_image", 1, "make the sky red")
    assert "Keep everything else in the picture unchanged" in one
    many = system_prompt(STYLES["qwen-image-21-edit"], "improve_image", 2, "the dog from picture 2")
    assert "Picture 1 is the canvas" in many and "Picture 1 to Picture 2" in many


def test_writer_handles_up_to_ten_pictures():
    from irisecho_core.engines.promptwriter.styles import MULTI

    assert MULTI.search("the hat from picture 10") and MULTI.search("put image 7 on the left")
    assert not MULTI.search("make picture 1 brighter")
    many = system_prompt(STYLES["qwen-image-21-edit"], "improve_image", 6, "a group photo of them")
    assert "Picture 1 to Picture 6" in many and "up to ten pictures" in many
    assert "the draft names no canvas" in many  # a group photo needs no canvas picture
