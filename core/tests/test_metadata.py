# SPDX-License-Identifier: AGPL-3.0-or-later
from PIL import Image

from irisecho_core import metadata, registry


def picture(path):
    Image.new("RGB", (32, 16), (200, 30, 30)).save(path)
    return path


def test_settings_go_in_as_a1111_text_and_come_back_exactly(tmp_path):
    p = picture(tmp_path / "a.png")
    model = registry.default().models["flux-dev"]
    params = {
        "prompt": "a lighthouse at dusk",
        "aspect": "16:9",
        "steps": 28,
        "image1": "secret-upload.png",
        "loras": [{"file": "watercolor.safetensors", "strength": 0.8}],
    }
    metadata.embed(p, model, params, 1234, (32, 16))
    with Image.open(p) as im:
        im.load()
        text = im.text["parameters"]
        assert im.size == (32, 16)
    assert text.splitlines()[0] == "a lighthouse at dusk"
    line = text.splitlines()[1]
    for part in (
        "Steps: 28",
        "Sampler: euler",
        "Seed: 1234",
        "Size: 32x16",
        "Model: FLUX.1 dev",
        "Distilled CFG Scale: 3.5",
    ):
        assert part in line
    assert 'Lora weights: "watercolor: 0.8"' in line
    found = metadata.read(p)
    assert found["source"] == "irisecho" and found["model"] == "flux-dev"
    assert found["params"]["seed"] == 1234 and found["params"]["steps"] == 28
    assert "image1" not in found["params"]  # files on this computer stay out


def test_unicode_prompts_use_itxt(tmp_path):
    p = picture(tmp_path / "u.png")
    metadata.embed(
        p, registry.default().models["z-image-turbo"], {"prompt": "café ☕ 東京"}, 5, None
    )
    assert metadata.read(p)["params"]["prompt"] == "café ☕ 東京"


def test_a1111_text_from_elsewhere_gives_prompt_seed_and_steps(tmp_path):
    p = picture(tmp_path / "b.png")
    text = (
        "a red fox\nNegative prompt: blurry\n"
        "Steps: 30, Sampler: Euler a, CFG scale: 7, Seed: 99, Size: 512x512"
    )
    metadata.add_text(p, {"parameters": text})
    found = metadata.read(p)
    assert found == {
        "model": None,
        "params": {"prompt": "a red fox", "seed": 99, "steps": 30},
        "source": "parameters",
    }


def test_pictures_without_settings_give_none(tmp_path):
    assert metadata.read(picture(tmp_path / "c.png")) is None
    (tmp_path / "d.png").write_bytes(b"not a picture")
    assert metadata.read(tmp_path / "d.png") is None
