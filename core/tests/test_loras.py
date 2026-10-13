# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import struct

import pytest

from irisecho_core import loras


def write(path, tensors: dict, meta: dict | None = None):
    """A header-only safetensors file: enough for the family check, no weights read."""
    header = {
        k: {"dtype": "F16", "shape": shape, "data_offsets": [0, 0]} for k, shape in tensors.items()
    }
    if meta is not None:
        header["__metadata__"] = meta
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)
    return path


FLUX = {"transformer.single_transformer_blocks.0.attn.to_q.lora_A.weight": [16, 3072]}
FLUX_KOHYA = {"lora_unet_double_blocks_0_img_attn_proj.lora_down.weight": [16, 3072]}
QWEN = {"transformer_blocks.0.img_mlp.net.0.proj.lora_A.weight": [16, 3072]}
ZIMAGE = {"diffusion_model.layers.0.attention.to_q.lora_A.weight": [32, 3840]}
LUMINA = {"diffusion_model.layers.0.attention.to_q.lora_A.weight": [32, 2304]}
WAN14 = {"diffusion_model.blocks.0.self_attn.q.lora_A.weight": [64, 5120]}
WAN5 = {"diffusion_model.blocks.0.self_attn.q.lora_A.weight": [64, 3072]}
SDXL = {"lora_unet_down_blocks_1_attentions_0_proj_in.lora_down.weight": [8, 640]}


@pytest.mark.parametrize(
    "tensors, family",
    [
        (FLUX, "flux"),
        (FLUX_KOHYA, "flux"),
        (QWEN, "qwen-image"),
        (ZIMAGE, "zimage"),
        (LUMINA, "unknown"),
        (WAN14, "wan14b"),
        (WAN5, "unknown"),
        (SDXL, "sd"),
        ({"something.else": [4, 4]}, "unknown"),
    ],
)
def test_family_from_tensor_names(tmp_path, tensors, family):
    assert loras.family_of(loras.read_header(write(tmp_path / "a.safetensors", tensors))) == family


def test_trigger_words_come_from_training_tags(tmp_path):
    freq = json.dumps({"10_mystyle": {"mystyle": 40, "a cat": 3, "watercolor": 12}})
    path = write(tmp_path / "s.safetensors", FLUX, {"ss_tag_frequency": freq})
    assert loras.triggers_of(loras.read_header(path)) == ["mystyle", "watercolor", "a cat"]


def test_scan_lists_safetensors_only_with_pair_hints(tmp_path):
    write(tmp_path / "style.safetensors", FLUX)
    (tmp_path / "wan").mkdir()
    write(tmp_path / "wan" / "Motion_HIGH_noise.safetensors", WAN14)
    write(tmp_path / "wan" / "motion_low.safetensors", WAN14)
    (tmp_path / "evil.ckpt").write_bytes(b"pickle")
    (tmp_path / "broken.safetensors").write_bytes(b"nope")
    found = {e["file"]: e for e in loras.scan([tmp_path])}
    assert set(found) == {
        "style.safetensors",
        "wan\\Motion_HIGH_noise.safetensors",
        "wan\\motion_low.safetensors",
    } or set(found) == {
        "style.safetensors",
        "wan/Motion_HIGH_noise.safetensors",
        "wan/motion_low.safetensors",
    }
    hints = {e["pair_hint"] for e in found.values()}
    assert hints == {"", "high", "low"}


def test_resolve_refuses_missing_wrong_family_and_escapes(tmp_path):
    write(tmp_path / "style.safetensors", FLUX)
    write(tmp_path / "wan.safetensors", WAN14)
    assert loras.resolve([tmp_path], "style.safetensors", "flux") == "style.safetensors"
    with pytest.raises(loras.LoraError, match="another kind of model"):
        loras.resolve([tmp_path], "wan.safetensors", "flux")
    with pytest.raises(loras.LoraError, match="not in the loras folder"):
        loras.resolve([tmp_path], "gone.safetensors", "flux")
    with pytest.raises(loras.LoraError, match="cannot be used"):
        loras.resolve([tmp_path], "../style.safetensors", "flux")
