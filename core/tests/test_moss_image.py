# SPDX-License-Identifier: AGPL-3.0-or-later
"""MOSS-Image, for pictures and edits: ComfyUI's MOSS support backported onto the pinned
release, the graphs as ComfyUI's own templates run them, and nothing added to the models
beside it (those are held by test_graph_parity)."""

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from irisecho_core import controls, loras, registry
from irisecho_core.engines import comfy
from irisecho_core.engines.comfy import graphs, sourcepatch
from irisecho_core.engines.promptwriter.styles import STYLES, system_prompt

HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")


def model(mid: str):
    return registry.default().models[mid]


def build(mid: str, images=None, aspect="1:1", prompt="a fox", **params):
    m = model(mid)
    names = {s.id: s.filename for s in registry.default().expand(m.variants[0].files)}
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


def test_one_set_of_files_and_the_edit_add_on():
    reg = registry.default()
    image, edit = model("moss-image"), model("moss-image-edit")
    assert image.kind == "image" and edit.kind == "edit"
    assert list(image.variants[0].files) == ["moss-sft-int8", "qwen35-9b-w4a8", "flux2-vae-small"]
    assert list(edit.variants[0].files) == [*image.variants[0].files, "moss-edit-lora"]
    for m in (image, edit):
        assert m.settings["prompt_style"] in STYLES
        # int8 "convrot" and w4a8 files run on ComfyUI's own kernels, which need CUDA 13.
        assert [v.when for v in m.variants] == [{"backend": "cuda", "cuda_tag": "cu130"}]
        for f in reg.expand(m.variants[0].files):
            assert f.pinned and f.size > 0 and f.repo == "Comfy-Org/MOSS-Image"
    assert image.license == "apache-2.0"
    lic = reg.licenses[edit.license]
    assert lic.commercial and lic.accept and "GPL" in lic.name


def test_list_order_keeps_the_owners_models_first():
    ids = list(registry.default().models)
    images = [i for i in ids if model(i).kind == "image"]
    edits = [i for i in ids if model(i).kind == "edit"]
    assert images.index("moss-image") == images.index("qwen-image-fast") + 1
    assert images.index("moss-image-fast") == images.index("moss-image") + 1
    assert edits.index("moss-image-edit") == edits.index("qwen-edit-fast") + 1
    assert images[0] == "qwen-image-21" and edits[0] == "qwen-image-21-edit"


def test_controls():
    m = model("moss-image")
    assert m.control("loras").family == "moss-image"
    assert m.control("megapixels").max == 4.0
    assert m.control("layer_skip").default is False
    e = model("moss-image-edit")
    # One picture is offered: the add-on did not use a second one in tests.
    assert e.settings["edit"] == {"pictures": 1, "areas": 1}
    with pytest.raises(controls.ControlError, match="up to 1 picture"):
        controls.check(e, {"image2": "b.png"})
    with pytest.raises(controls.ControlError, match="1 marked area"):
        controls.check(e, {"box": [[0, 0, 0.5, 0.5], [0.5, 0.5, 1, 1]]})


# --- pictures ---------------------------------------------------------------------------


def test_picture_graph_follows_comfyuis_template():
    g, save = build("moss-image", aspect="16:9")
    assert g["clip"]["inputs"]["type"] == "moss_image"
    s = g["sample"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"], s["scheduler"]) == (
        25,
        2.5,
        "er_sde",
        "simple",
    )
    assert s["model"] == ["unet", 0] and s["denoise"] == 1.0
    assert g["negative"]["inputs"]["text"] == graphs.MOSS_NEGATIVE
    latent = g["latent"]["inputs"]
    assert g["latent"]["class_type"] == "EmptyFlux2LatentImage"
    assert latent["width"] % 16 == 0 and latent["height"] % 16 == 0
    assert latent["width"] > latent["height"]
    assert "SkipLayerGuidanceDiTSimple" not in classes(g)
    assert "ModelSamplingAuraFlow" not in classes(g)
    assert g[save]["class_type"] == "SaveImage"


def test_picture_controls():
    g, _ = build(
        "moss-image",
        steps=50,
        guidance=3.0,
        layer_skip=True,
        shift=4.0,
        negative="blurry",
        sampler="euler",
        loras=[{"file": "style.safetensors", "strength": 0.8}],
    )
    s = g["sample"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"]) == (50, 3.0, "euler")
    assert g["slg"]["inputs"]["single_layers"] == "22" and g["slg"]["inputs"]["end_percent"] == 1.0
    assert g["shift"]["inputs"]["shift"] == 4.0
    assert g["lora1"]["inputs"]["lora_name"] == "style.safetensors"
    assert s["model"] == ["slg", 0]
    assert g["negative"]["inputs"]["text"] == "blurry"


def test_picture_from_a_picture():
    g, _ = build("moss-image", images={"image1": "in.png"}, denoise=0.4, megapixels=2.0)
    assert g["sample"]["inputs"]["latent_image"] == ["latent", 0]
    assert g["latent"]["class_type"] == "VAEEncode"
    assert g["sample"]["inputs"]["denoise"] == 0.4
    assert g["init_scale"]["inputs"]["megapixels"] == pytest.approx(2.0, abs=0.01)


# --- edits ------------------------------------------------------------------------------


def test_edit_graph_follows_the_add_ons_recipe():
    g, save = build("moss-image-edit", images={"image1": "a.png"})
    assert g["edit_lora"]["inputs"]["lora_name"].startswith("moss_image_edit_lora")
    assert g["slg"]["inputs"]["model"] == ["edit_lora", 0]
    assert g["slg"]["inputs"]["end_percent"] == 1.0
    s = g["sample"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"], s["scheduler"]) == (30, 4.0, "euler", "normal")
    for side in ("text", "negative"):
        enc = g[side]["inputs"]
        assert g[side]["class_type"] == "TextEncodeMOSSImageEdit"
        assert enc["edit_format"] == "Moss-Image-Edit" and enc["vae"] == ["vae", 0]
        assert enc["images.image_0"] == ["scale1", 0]
    assert g["negative"]["inputs"]["prompt"] == graphs.MOSS_NEGATIVE
    assert g["scale1"]["inputs"]["megapixels"] == 1.0
    assert g["scale1"]["inputs"]["resolution_steps"] == 16
    assert g["latent"]["inputs"]["width"] == ["size", 0]
    assert g[save]["inputs"]["images"] == ["decode", 0]


def test_both_run_with_dynamic_vram():
    # The edit add-on's reference cache does not fit beside the fully loaded model otherwise.
    assert {"moss-image", "moss-edit"} <= graphs.DYNAMIC_VRAM


def test_builder_still_takes_several_pictures_in_order():
    g, _ = build(
        "moss-image-edit", images={"image1": "a.png", "image3": "c.png", "image4": "d.png"}
    )
    enc = g["text"]["inputs"]
    assert [enc[f"images.image_{i}"] for i in range(3)] == [
        ["scale1", 0],
        ["scale2", 0],
        ["scale3", 0],
    ]
    assert g["load2"]["inputs"]["image"] == "c.png" and g["load3"]["inputs"]["image"] == "d.png"
    # The others share MOSS_REF_BUDGET_MP; Picture 1 keeps the working size.
    assert g["scale1"]["inputs"]["megapixels"] == 1.0
    assert g["scale2"]["inputs"]["megapixels"] == 0.5
    with pytest.raises(ValueError, match="picture to edit"):
        build("moss-image-edit", images={"image2": "b.png"})


def test_edit_of_an_area_pastes_onto_picture_1():
    g, save = build("moss-image-edit", images={"image1": "a.png", "mask": "m.png"}, feather=0)
    assert g[save]["inputs"]["images"] == ["paste", 0]
    assert g["paste"]["inputs"]["destination"] == ["scale1", 0]
    assert "mask_blur" not in g


# --- add-ons and writer -----------------------------------------------------------------


def _header(path: Path, keys: dict) -> Path:
    raw = json.dumps(keys).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)
    return path


@pytest.mark.parametrize(
    "keys",
    [
        {"layers.0.proj.q.lora_A": {"shape": [64, 3072]}},  # the edit add-on's own form
        {"diffusion_model.layers.3.proj.ffn_up_gate.lora_A.weight": {"shape": [16, 3072]}},
        {"lora_unet_layers_7_proj_qkv.lora_down.weight": {"shape": [16, 3072]}},
    ],
)
def test_moss_add_ons_are_recognised(tmp_path, keys):
    assert loras.describe(_header(tmp_path / "a.safetensors", keys))["family"] == "moss-image"


def test_other_families_are_not_taken_for_moss(tmp_path):
    zimage = {"layers.0.attention.to_q.lora_A.weight": {"shape": [16, 3840]}}
    assert loras.describe(_header(tmp_path / "z.safetensors", zimage))["family"] == "zimage"


def test_writer_styles():
    image, edit = STYLES["moss-image"], STYLES["moss-image-edit"]
    assert not image.edit and edit.edit and edit.guide_with_image and not edit.guide_multi
    assert "Change only the" in system_prompt(edit, "improve_image", 1, "make the coat red")
    poster = system_prompt(image, "improve", 0, 'a poster that reads "SALE"')
    assert "English and Chinese" in poster


# --- the ComfyUI backport ---------------------------------------------------------------


def test_backport_is_listed_and_adds_its_files(tmp_path):
    assert "comfyui-moss-image.diff" in comfy.COMFY_PATCHES
    diff = (comfy.PATCH_DIR / "comfyui-moss-image.diff").read_text(encoding="utf-8")
    added = [p.path for p in sourcepatch.parse(diff) if p.new_file]
    assert added == [
        "comfy/ldm/moss_image/model.py",
        "comfy/text_encoders/moss_image.py",
        "comfy_extras/nodes_moss_image.py",
    ]


def test_new_files_apply_strictly(tmp_path):
    body = "print('hi')\n"
    new = sourcepatch.blob_sha1(body.encode())
    diff = (
        "diff --git a/pkg/new.py b/pkg/new.py\nnew file mode 100644\n"
        f"index 0000000..{new[:11]}\n--- /dev/null\n+++ b/pkg/new.py\n"
        "@@ -0,0 +1 @@\n+print('hi')\n"
    )
    assert sourcepatch.state(tmp_path, diff) == "pristine"
    assert sourcepatch.apply(tmp_path, diff) is True
    assert (tmp_path / "pkg" / "new.py").read_text() == body
    assert sourcepatch.apply(tmp_path, diff) is False  # already there
    (tmp_path / "pkg" / "new.py").write_text("something else\n")
    with pytest.raises(sourcepatch.PatchError):
        sourcepatch.apply(tmp_path, diff)


# --- MOSS-Image Fast --------------------------------------------------------------------


def test_fast_adds_the_8_step_lora_at_cfg_1():
    m = model("moss-image-fast")
    files = registry.default().expand(m.variants[0].files)
    turbo = [f for f in files if f.id == "moss-turbo-lora"][0]
    assert turbo.repo == "lodestones/MOSS-Image-SFT-8-steps-dmd" and turbo.pinned
    # Stored under its own name: the repository calls it lora.safetensors.
    assert turbo.filename == "moss_image_sft_8step_dmd_lora.safetensors"
    assert m.license == "apache-2.0" and m.control("loras").family == "moss-image"
    assert m.control("guidance") is None and m.control("negative") is None
    g, _ = build("moss-image-fast", loras=[{"file": "style.safetensors", "strength": 0.5}])
    assert g["turbo"]["inputs"]["lora_name"] == turbo.filename
    assert g["turbo"]["inputs"]["strength_model"] == 1.0
    assert g["lora1"]["inputs"]["model"] == ["turbo", 0]
    s = g["sample"]["inputs"]
    assert (s["steps"], s["cfg"], s["sampler_name"], s["scheduler"]) == (8, 1.0, "euler", "simple")
    assert s["model"] == ["lora1", 0]


def test_the_full_model_has_no_speed_up():
    g, _ = build("moss-image")
    assert "turbo" not in g and g["sample"]["inputs"]["sampler_name"] == "er_sde"
