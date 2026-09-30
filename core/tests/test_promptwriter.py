# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from irisecho_core import registry
from irisecho_core.engines.promptwriter import STYLES, style_for
from irisecho_core.engines.promptwriter.styles import clean, system_prompt, user_prompt


def test_every_image_edit_and_video_model_has_a_style():
    reg = registry.default()
    for m in reg.models.values():
        if m.kind in ("image", "edit", "video"):
            assert m.settings.get("prompt_style") in STYLES, m.id
            assert style_for(m.id, m.settings)


def test_models_without_a_style_are_refused():
    with pytest.raises(ValueError):
        style_for("kokoro", {})


def test_styles_forbid_negative_phrasing():
    for style in STYLES.values():
        assert "never use 'no'" in system_prompt(style, "improve", False)


def test_edit_style_swaps_guide_when_a_picture_is_given():
    style = STYLES["qwen-edit"]
    assert system_prompt(style, "improve_image", True) != system_prompt(style, "improve", False)


def test_user_prompt_carries_the_draft():
    assert "a red fox" in user_prompt(STYLES["flux"], "improve", "a red fox")
    assert "attached picture" in user_prompt(STYLES["flux"], "describe", "")
    assert "video" in user_prompt(STYLES["wan-i2v"], "describe", "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"A fox at dawn."', "A fox at dawn."),
        ("Prompt: A fox\n at dawn.", "A fox at dawn."),
        ('A sign that reads "OPEN" in neon.', 'A sign that reads "OPEN" in neon.'),
        ("  spaced   out  ", "spaced out"),
    ],
)
def test_clean(raw, expected):
    assert clean(raw) == expected


def test_prompt_jobs_are_hidden_from_listings(tmp_path):
    from irisecho_core.db import Database

    db = Database(tmp_path / "s.db")
    for i, kind in enumerate(["prompt", "image", "prompt"]):
        db.insert(
            {
                "id": f"j{i}",
                "model": "m",
                "kind": kind,
                "params": {},
                "status": "done",
                "created": float(i),
            }
        )
    assert [j["id"] for j in db.list()] == ["j1"]
    assert len(db.list(kind="prompt")) == 2
    assert db.prune("prompt", keep=1) == ["j0"]
