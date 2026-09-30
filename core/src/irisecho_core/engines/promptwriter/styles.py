# SPDX-License-Identifier: AGPL-3.0-or-later
"""How each generator likes to be prompted, and the instructions that ask for it.

One style per family of models. Each states what the model's text encoder and
training make it good at, so the rewrite plays to that. Every style forbids
negative phrasing: the distilled models run without classifier-free guidance,
so "no X" and "without Y" do nothing, or worse, plant X in the picture.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class Style:
    id: str
    label: str
    edit: bool  # the prompt is an instruction applied to a given picture
    video: bool
    guide: str
    guide_with_image: str = ""  # used instead of `guide` when a picture is supplied


STYLES: dict[str, Style] = {
    s.id: s
    for s in [
        Style(
            "zimage",
            "Z-Image",
            edit=False,
            video=False,
            guide=(
                "Target: Z-Image-Turbo, a fast model with a language-model text encoder. "
                "Write natural sentences, about 50 to 90 words. Cover, in this order: the "
                "main subject and its attributes, what it is doing, where it is and how "
                "things are arranged, then the medium, light and lens or art style. Give "
                "concrete visual detail a knowledgeable person would volunteer instead of "
                "bare nouns. Put the subject first: with so few sampling steps early words "
                "count most. Keep the draft's language."
            ),
        ),
        Style(
            "flux",
            "FLUX",
            edit=False,
            video=False,
            guide=(
                "Target: FLUX.1, which reads a short pooled summary plus a longer sequence. "
                "Write flowing prose, not tags. State the subject and its action in the "
                "first sentence; anything past about 70 words counts for much less. Aim for "
                "40 to 65 words in two to four sentences. After the subject add the setting, "
                "then named light (golden-hour rim light, overcast softbox), then materials "
                "and color, and fold camera or medium into a clause about the subject. Pick "
                "one style register, such as 35mm film or gouache illustration, and stay in it."
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
                "20 to 35 words and one or two sentences. Choose one light quality and at "
                "most one mood word. Plain, concrete description beats ornate phrasing."
            ),
        ),
        Style(
            "qwen-image",
            "Qwen-Image",
            edit=False,
            video=False,
            guide=(
                "Target: Qwen-Image, which follows long structured descriptions and draws "
                "lettering well. Write full sentences like a brief to an art director, 70 to "
                "130 words: subject and its characteristics, then foreground, middle and "
                "background, then light, style and composition. For every piece of visible "
                "text give the exact string in double quotes, where it sits, and how it is "
                "made (typeface mood, color, material such as neon tube or etched stone). If "
                "the draft only says 'a name and a date', invent one concrete example."
            ),
        ),
        Style(
            "qwen-edit",
            "Qwen Edit",
            edit=True,
            video=False,
            guide=(
                "Target: Qwen-Image-Edit, which takes one instruction about a supplied "
                "picture. Write a direct instruction in 20 to 60 words that starts with a "
                "verb and states exactly what changes: object, attribute, position and "
                "degree. If the request is vague, choose specifics. Name what must stay "
                "the same when the edit is local: identity, pose, background, framing. "
                "When several pictures are supplied, call them 'picture 1', 'picture 2' "
                "and say which part comes from which."
            ),
            guide_with_image=(
                "Target: Qwen-Image-Edit, which takes one instruction about the supplied "
                "picture. Look at the picture, then write a direct instruction in 20 to 60 "
                "words that starts with a verb and states exactly what changes: object, "
                "attribute, position and degree. Refer to things by how they look in this "
                "picture. Name what must stay the same when the edit is local."
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
                "continuous shot. Write 60 to 100 words. Start with the subject and its "
                "motion using specific verbs and pace, then the scene, then named light and "
                "color, then shot size and camera movement (static, slow push-in, pan left, "
                "tracking). Describe what moves and how, not a still frame. One action per "
                "clip: the model cannot fit a story into five seconds."
            ),
        ),
        Style(
            "wan-i2v",
            "Wan video from a picture",
            edit=False,
            video=True,
            guide=(
                "Target: Wan 2.2 image to video. The first frame is a supplied picture, so "
                "the model already has the whole scene. Describe only motion, in 30 to 80 "
                "words: who or what moves, how, in what order, and how the camera moves. "
                "Do not describe things that are already visible and static. One action "
                "per clip."
            ),
        ),
    ]
}

MODES = ("improve", "describe", "improve_image")


def system_prompt(style: Style, mode: str, has_image: bool) -> str:
    guide = style.guide_with_image if has_image and style.guide_with_image else style.guide
    return f"{guide}\n\n{COMMON}\n\n{REPLY}"


def user_prompt(style: Style, mode: str, draft: str) -> str:
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
        return (
            f"The attached picture is the reference. Rewrite this draft as a better {kind}, "
            f"keeping its intent.\n\nDraft: {draft}"
        )
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
