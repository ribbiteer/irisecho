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
        assert "never use 'no'" in system_prompt(style, "improve", 0)


def test_edit_style_swaps_guide_when_a_picture_is_given():
    style = STYLES["qwen-edit"]
    assert system_prompt(style, "improve_image", 1) != system_prompt(style, "improve", 0)


def test_edit_style_numbers_several_pictures():
    style = STYLES["qwen-edit"]
    assert "Picture 1 to Picture 3" in system_prompt(style, "improve_image", 3)
    assert "Picture 1 to" not in system_prompt(style, "improve_image", 1)
    assert "pictures are the references" in user_prompt(style, "improve_image", "x", 3)


@pytest.mark.parametrize(
    ("pictures", "draft", "multi"),
    [
        (0, "make his jacket red", False),
        (1, "make his jacket red", False),
        (0, "the dog from picture 2 sits on the sofa in picture 1", True),
        (0, "put the cup from Image 3 on the table", True),
        (2, "combine them", True),
    ],
)
def test_multi_picture_edits_get_only_the_result_form(pictures, draft, multi):
    prompt = system_prompt(STYLES["qwen-edit"], "improve", pictures, draft)
    assert ("describes the finished picture" in prompt) is multi
    assert ("keep [named things] unchanged" in prompt) is not multi


def test_still_styles_ask_for_light_as_facts_and_an_eyeline():
    for sid in ("zimage", "flux", "qwen-image", "krea2"):
        prompt = system_prompt(STYLES[sid], "improve", 0)
        assert "which side falls into shadow" in prompt, sid
    for sid in ("zimage", "flux"):
        assert "where that person looks" in system_prompt(STYLES[sid], "improve", 0), sid


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


def test_text_rules_reach_the_writer_only_when_the_draft_is_about_words():
    plain = system_prompt(STYLES["zimage"], "improve", 0, "a leopard in a garden")
    assert "double quotes" not in plain
    assert "lettering" not in plain
    sign = system_prompt(STYLES["zimage"], "improve", 0, "a neon sign that says OPEN")
    assert "double quotes" in sign
    assert "double quotes" in system_prompt(STYLES["zimage"], "describe", 1)


def test_qwen_image_lettering_guide_is_kept_for_drafts_with_text():
    assert "neon tube" not in system_prompt(STYLES["qwen-image"], "improve", 0, "a red fox")
    poster = system_prompt(STYLES["qwen-image"], "improve", 0, 'a poster that reads "SALE"')
    assert "neon tube" in poster


def test_style_guides_do_not_hand_the_writer_a_negation_to_repeat():
    for sid in ("zimage", "flux", "flux-schnell", "qwen-image", "krea2"):
        guide = STYLES[sid].guide
        assert "Use no " not in guide, sid
        assert "Add no " not in guide, sid


def test_clock_rule_reaches_the_writer_only_for_drafts_with_a_clock():
    plain = system_prompt(STYLES["flux"], "improve", 0, "a fisherman on a dock")
    assert "baton markers" not in plain
    watch = system_prompt(STYLES["flux"], "improve", 0, "a man holding a pocket watch")
    assert "baton markers" in watch


def test_multi_picture_edit_keeps_named_objects_and_names_what_picture_1_shows():
    multi = system_prompt(
        STYLES["qwen-edit"], "improve_image", 2, "the family from picture 2 on a bench"
    )
    assert "Keep every object the draft names" in multi
    assert "most distinctive things you can see" in multi
    single = system_prompt(STYLES["qwen-edit"], "improve", 0, "make the sky red")
    assert "most distinctive things" not in single
