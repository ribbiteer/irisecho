# SPDX-License-Identifier: AGPL-3.0-or-later
"""How each generator likes to be prompted, and the instructions that ask for it.

One style per family of models. Each states what the model's text encoder and
training make it good at, so the rewrite plays to that, plus what side-by-side
tests of these models showed: light written as facts is what moves a picture
off the evenly lit default, FLUX people look into the lens unless told where to
look, numerals and clock times come out garbled, multi-picture edits act on a
short description of the result, and image-to-video moves best on a short
motion-only prompt. Every style forbids negative phrasing: the distilled models
run without classifier-free guidance, so "no X" and "without Y" do nothing, or
worse, plant X in the picture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMMON = (
    "Keep everything the person asked for: the subject, counts, colors and names. If "
    "they asked for words to appear in the picture, put those words in double quotes, "
    "copied exactly; never add captions, signs or lettering they did not ask for. Write "
    "only positive statements: never use 'no', 'not', 'without' or 'avoid'; describe "
    "what is there instead. Do not pad with quality words such as 'masterpiece', '8k' "
    "or 'highly detailed'."
)

REPLY = (
    "Reply with the finished prompt only, as plain text in a single paragraph: no title, "
    "no label, no quotation marks around it, no explanation, no lists."
)

LIGHT = (
    "Write the light as facts: the one main source, where it is, which side of the "
    "subject it lights, which side falls into shadow, and how the background falls off "
    "into darkness or haze."
)
# The writer is a small model that copies example sentences word for word, so the
# patterns below use [placeholders] instead of examples it could paste.
EYES = (
    "Right after the sentence that introduces each person, add a short sentence saying "
    "where that person looks, naming the thing they look at in this scene: '[person] looks "
    "[down at, out of, toward] [thing].'"
)
DIALS = (
    "If the scene has a clock or watch and the draft gives it no numerals or time, describe "
    "its face with plain baton markers."
)
EDIT_ONE = (
    "Write a direct instruction in 15 to 50 words that starts with a verb (Replace, Change, "
    "Add, Remove, Make) and states exactly what changes: object, attribute, position and "
    "degree. Then name what must stay unchanged, using only things the draft or the picture "
    "shows: '[verb] [what changes] [how]; keep [named things] unchanged.'"
)
# Several pictures: the developers' own form, a short description of the result.
# In tests, any keep-list in a multi-picture edit stopped the action.
EDIT_MANY = (
    "Write one sentence that describes the finished picture: '[who or what] from Picture 2 "
    "[does what] [where] in Picture 1.' Only if the draft uses Picture 3, add ', [holding "
    "or wearing] [what] from Picture 3'. Write 'Picture 1', 'Picture 2' and 'Picture 3' "
    "exactly. End the sentence there: write no 'keep' or 'keeping' clause and no list of "
    "what stays the same, because such lists stop the action."
)
# A draft that names a second or third picture is a multi-picture edit.
MULTI = re.compile(r"\b(picture|image|pic|photo)\s*[23]\b", re.IGNORECASE)


@dataclass(frozen=True)
class Style:
    id: str
    label: str
    edit: bool  # the prompt is an instruction applied to a given picture
    video: bool
    guide: str
    guide_with_image: str = ""  # used instead of `guide` when a picture is supplied
    guide_multi: str = ""  # used instead of both when the edit combines several pictures


STYLES: dict[str, Style] = {
    s.id: s
    for s in [
        Style(
            "zimage",
            "Z-Image",
            edit=False,
            video=False,
            guide=(
                "Target: Z-Image-Turbo, which was tuned on the output of its developers' "
                "prompt enhancer, so write the way that enhancer does: plain, objective, "
                "concrete sentences, 70 to 150 words. Cover, in this order: the main subject "
                "and its attributes, what it is doing, where it is and how things are "
                "arranged from front to back, then the light, then the lens or art style. "
                "Where the draft leaves a design open, decide it and describe exactly what "
                "you decided: shape, material and color. Put the subject first: with so few "
                f"sampling steps early words count most. {LIGHT} {EYES} Use no metaphors "
                f"and no words for moods or feelings. {DIALS} Keep the draft's language."
            ),
        ),
        Style(
            "flux",
            "FLUX",
            edit=False,
            video=False,
            guide=(
                "Target: FLUX.1 dev or Krea. Two text encoders read the prompt: one sees "
                "only the first 60 or so words, the other reads all of it. Write flowing "
                "prose, not tags, 60 to 150 words. Put the subject and its action in the "
                f"first sentence. {EYES} Without that sentence people stare into the lens. "
                "Then the setting from front to back, then the light. "
                f"{LIGHT} Then materials, color and surface wear, and the camera as facts "
                "(shot size, camera height, focal length and aperture) or one named art medium; "
                f"stay in that one style. {DIALS}"
            ),
        ),
        Style(
            "flux-schnell",
            "FLUX schnell",
            edit=False,
            video=False,
            guide=(
                "Target: FLUX.1 schnell, which makes the picture in four steps and blurs "
                "several subjects together. Use one subject, one action, one setting, in "
                "20 to 35 words and one or two sentences. Say where a person looks. Name "
                "the one light source and the side it comes from, and use at most one mood "
                "word. Plain, concrete description beats ornate phrasing."
            ),
        ),
        Style(
            "krea2",
            "Krea 2",
            edit=False,
            video=False,
            guide=(
                "Target: Krea 2. Its text encoder is asked, as Qwen-Image's is, for color, "
                "shape, size, texture, quantity, text and spatial relationships, so write "
                "one cohesive paragraph of full sentences, 60 to 150 words. Give each "
                "subject its own attributes and action in one place, then the setting from "
                f"front to back, then the light and the camera. {LIGHT} Keep any medium the "
                "draft names (photograph, illustration, painting, 3D render) and stay in it. "
                "Put every piece of visible text in double quotes, exactly as asked. Add no "
                "objects, props or animals the draft does not imply; if the draft is already "
                "detailed, polish it and keep its wording. Assume clothing covers intimate "
                "anatomy."
            ),
        ),
        Style(
            "qwen-image",
            "Qwen-Image",
            edit=False,
            video=False,
            guide=(
                "Target: Qwen-Image, which draws lettering well. Its text encoder is asked "
                "for color, shape, size, texture, quantity, text, spatial relationships and "
                "background, so cover each of them. Start with the style as a short "
                "sentence of its own, the way its developers do ('Realistic photography "
                "style.', 'Flat vector illustration style.'). Then write full sentences, 80 "
                "to 180 words: the subject and its characteristics, then foreground, middle "
                f"and background with their positions, then the light and composition. {LIGHT} "
                "For every piece of visible text give the exact string in double quotes, "
                "where it sits, its size and how it is made (typeface mood, color, material "
                "such as neon tube or etched stone). Keep text to a few large words. If the "
                "draft only says 'a name and a date', invent one concrete example. "
                f"{DIALS}"
            ),
        ),
        Style(
            "qwen-edit",
            "Qwen Edit",
            edit=True,
            video=False,
            guide=(
                "Target: Qwen-Image-Edit, which changes a supplied picture by one "
                f"instruction. {EDIT_ONE} If the request is vague, choose specifics. "
                "Describe things in the picture only as the draft does: an invented color "
                "or pattern overrides what the picture shows."
            ),
            guide_with_image=(
                "Target: Qwen-Image-Edit, which changes the attached picture by one "
                "instruction. Look at the picture first and refer to things by how they "
                f"look in it. {EDIT_ONE} Name only colors, patterns and details the picture "
                "really shows: the model follows the words over the picture."
            ),
            guide_multi=(
                "Target: Qwen-Image-Edit, which combines up to three pictures into one; the "
                f"result has Picture 1's size. {EDIT_MANY} Describe things in the pictures "
                "only as the draft does or as they look: an invented color or pattern "
                "overrides what the picture shows."
            ),
        ),
        Style(
            "kontext",
            "Kontext",
            edit=True,
            video=False,
            guide=(
                "Target: FLUX.1 Kontext, which edits by changing only what is named. Write "
                "one or two short instructions, under 45 words, as direct actions with a "
                "target state ('recolor the sedan to matte olive green'). Name only the "
                "change. Refer to people and objects by a visual description, not by 'he', "
                "'she' or 'it'. For a style change name a real medium or movement. For text "
                "give the exact new string in double quotes. Add what to preserve when it "
                "would otherwise drift, such as the face or the composition."
            ),
        ),
        Style(
            "wan-t2v",
            "Wan video",
            edit=False,
            video=True,
            guide=(
                "Target: Wan 2.2 text to video, a clip of about five seconds in one "
                "continuous shot, 60 to 150 words. Start the way its developers' rewriter "
                "does: up to four comma-separated settings from the vocabulary it was "
                "trained on, such as 'Night time', 'Daylight', 'Practical lighting', "
                "'Moonlight', 'Soft lighting', 'Hard lighting', 'Side lighting', 'Edge "
                "lighting', 'Warm colors', 'Cool colors', 'Close-up shot', 'Medium shot', "
                "'Wide shot'. If the draft names a style such as anime or watercolor, start "
                "with that instead. Then the subject's look, how its one action unfolds with "
                "specific verbs and pace, gentle background motion (steam rising, leaves "
                "stirring), and the camera move. Describe what moves, not a still frame, "
                "and leave out feelings and atmosphere. One action per clip: the model "
                "cannot fit a story into five seconds."
            ),
        ),
        Style(
            "wan-i2v",
            "Wan video from a picture",
            edit=False,
            video=True,
            guide=(
                "Target: Wan 2.2 image to video. The first frame is a supplied picture, so "
                "the model already has the whole scene. Describe only motion, in one to "
                "three short sentences under 60 words: who or what moves and how, then one "
                "sentence for the camera ('The camera pushes in toward her face.', 'The "
                "camera tilts down to the letter.' or 'The camera is still.'). When an "
                "object should stay put, say so and name the one thing that moves ('The "
                "watch lies still on the felt. Only the seconds hand moves.'); otherwise "
                "Wan adds hands that pick it up. Leave out scenery, mood and style words: "
                "they make the camera push harder and the clip darken. One action per clip."
            ),
        ),
    ]
}

MODES = ("improve", "describe", "improve_image")


def system_prompt(style: Style, mode: str, pictures: int, draft: str = "") -> str:
    if style.guide_multi and (pictures > 1 or MULTI.search(draft)):
        guide = style.guide_multi
    else:
        guide = style.guide_with_image if pictures and style.guide_with_image else style.guide
    if style.edit and pictures > 1:
        guide += f" The {pictures} pictures are attached in order: Picture 1 to Picture {pictures}."
    return f"{guide}\n\n{COMMON}\n\n{REPLY}"


def user_prompt(style: Style, mode: str, draft: str, pictures: int = 1) -> str:
    kind = "edit instruction" if style.edit else "video prompt" if style.video else "image prompt"
    if mode == "describe":
        if style.edit:
            return (
                "Suggest one good edit for the attached picture and write it as an "
                f"{kind} in the style described above."
            )
        if style.video:
            return "Imagine how the attached picture could move and write it as a video prompt."
        return (
            "Describe the attached picture as an image prompt that would recreate it, in "
            "the style described above."
        )
    if mode == "improve_image":
        ref = (
            "The attached pictures are the references."
            if pictures > 1
            else ("The attached picture is the reference.")
        )
        return f"{ref} Rewrite this draft as a better {kind}, keeping its intent.\n\nDraft: {draft}"
    return f"Rewrite this draft as a better {kind}, keeping its intent.\n\nDraft: {draft}"


def clean(text: str) -> str:
    """Strip the wrapping a chat model sometimes adds despite instructions."""
    text = text.strip()
    for prefix in ("prompt:", "improved prompt:", "here is the prompt:", "rewritten prompt:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
    pairs = {'"': '"', "'": "'", "“": "”"}
    if len(text) > 1 and pairs.get(text[0]) == text[-1] and text.count(text[0]) == 2:
        text = text[1:-1].strip()
    return " ".join(text.split())
