# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installed into IrisEcho's private ComfyUI as custom_nodes/irisecho_3d.

Helpers for the 3D studio, run inside ComfyUI where torch is at hand:

* IrisEchoFrameViews frames a front and a back picture the way Pixal3D's
  multi-view rig expects them (eye level, 20 degree field of view, the object
  spanning 1/1.1 of the frame at the same scale in every view);
* IrisEchoViewCheck tells whether a back view really is the front's opposite;
* IrisEchoSimilarity tells whether an edit changed a picture at all.

Masks follow ComfyUI's convention: 1 where the object is.
"""

import torch
import torch.nn.functional as F

SIZE = 1024
SPAN = 1 / 1.1  # the rig's unit cube covers this much of the frame


def _bbox(mask: torch.Tensor) -> tuple[int, int, int, int]:
    ys, xs = torch.nonzero(mask > 0.5, as_tuple=True)
    if ys.numel() == 0:
        raise ValueError("No object was found in one of the pictures.")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _resize(t: torch.Tensor, w: int, h: int) -> torch.Tensor:
    """[H, W, C] -> [h, w, C], area-averaged when shrinking."""
    x = t.movedim(-1, 0).unsqueeze(0)
    mode = "area" if h < t.shape[0] else "bilinear"
    kw = {} if mode == "area" else {"align_corners": False}
    return F.interpolate(x, size=(h, w), mode=mode, **kw).squeeze(0).movedim(0, -1)


class IrisEchoFrameViews:
    """Height is the same from every side at eye level, so each view is scaled to
    the median object height; one shared scale then fits the widest extent."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"front": ("IMAGE",), "front_mask": ("MASK",)},
            "optional": {"back": ("IMAGE",), "back_mask": ("MASK",)},
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("front", "back")
    FUNCTION = "frame"
    CATEGORY = "irisecho"

    def frame(self, front, front_mask, back=None, back_mask=None):
        views = [(front[0, ..., :3], front_mask[0])]
        if back is not None and back_mask is not None:
            views.append((back[0, ..., :3], back_mask[0]))
        boxes = []
        for img, mask in views:
            if mask.shape != img.shape[:2]:
                mask = F.interpolate(mask[None, None], size=img.shape[:2], mode="bilinear")[0, 0]
            boxes.append((img, mask, _bbox(mask)))
        heights = sorted(b[3] - b[1] for _, _, b in boxes)
        common = heights[len(heights) // 2]
        extent = max(max(b[3] - b[1], b[2] - b[0]) * common / (b[3] - b[1]) for _, _, b in boxes)
        shared = SIZE * SPAN / extent
        out = []
        for img, mask, (x0, y0, x1, y1) in boxes:
            k = common / (y1 - y0) * shared
            pad = 4
            x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
            x1, y1 = min(img.shape[1], x1 + pad), min(img.shape[0], y1 + pad)
            rgba = torch.cat([img[y0:y1, x0:x1], mask[y0:y1, x0:x1, None]], dim=-1)
            w, h = max(1, round((x1 - x0) * k)), max(1, round((y1 - y0) * k))
            rgba = _resize(rgba, min(w, SIZE), min(h, SIZE)).clamp(0, 1)
            canvas = torch.zeros(SIZE, SIZE, 4)
            top, left = (SIZE - rgba.shape[0]) // 2, (SIZE - rgba.shape[1]) // 2
            canvas[top : top + rgba.shape[0], left : left + rgba.shape[1]] = rgba
            out.append(canvas[None])
        return (out[0], out[1] if len(out) > 1 else out[0])


def _norm_mask(mask: torch.Tensor, size: int = 512) -> torch.Tensor:
    """The object's outline scaled to a fixed height and centred, for comparing views."""
    x0, y0, x1, y1 = _bbox(mask)
    crop = (mask[y0:y1, x0:x1] > 0.5).float()
    k = size * 0.8 / (y1 - y0)
    w, h = max(1, round((x1 - x0) * k)), round((y1 - y0) * k)
    crop = _resize(crop[..., None], min(w, 2 * size), h)[..., 0] > 0.5
    canvas = torch.zeros(size, 2 * size, dtype=torch.bool)
    top, left = (size - crop.shape[0]) // 2, (2 * size - crop.shape[1]) // 2
    canvas[top : top + crop.shape[0], left : left + crop.shape[1]] = crop
    return canvas


class IrisEchoViewCheck:
    """Seen from exactly opposite at eye level, an object's outline is the mirror
    image of its front outline (the rig's camera is nearly orthographic)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"front_mask": ("MASK",), "back_mask": ("MASK",)}}

    RETURN_TYPES = ()
    FUNCTION = "check"
    OUTPUT_NODE = True
    CATEGORY = "irisecho"

    def check(self, front_mask, back_mask):
        a = _norm_mask(front_mask[0])
        b = _norm_mask(back_mask[0]).flip(1)
        iou = float((a & b).sum()) / max(1.0, float((a | b).sum()))
        return {"ui": {"irisecho": [{"mirror_iou": round(iou, 4)}]}}


class IrisEchoSimilarity:
    """Correlation of two pictures at 64 px in grey: near 1 when an edit left it as it was."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"a": ("IMAGE",), "b": ("IMAGE",)}}

    RETURN_TYPES = ()
    FUNCTION = "compare"
    OUTPUT_NODE = True
    CATEGORY = "irisecho"

    def compare(self, a, b):
        def grey(img):
            g = img[0, ..., :3].mean(-1)
            g = F.interpolate(g[None, None], size=(64, 64), mode="area")[0, 0]
            return (g - g.mean()) / (g.std() + 1e-6)

        corr = float((grey(a) * grey(b)).mean())
        return {"ui": {"irisecho": [{"similarity": round(corr, 4)}]}}


class IrisEchoChooseMask:
    """The picture's own transparency when it really has some (upstream's rule: an
    alpha channel that is opaque everywhere counts as none), else the cut-out."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"alpha": ("MASK",), "cutout": ("MASK",)}}

    RETURN_TYPES = ("MASK",)
    FUNCTION = "choose"
    CATEGORY = "irisecho"

    def choose(self, alpha, cutout):
        # LoadImage gives a 64x64 placeholder for pictures without alpha.
        if alpha.shape[-2:] == cutout.shape[-2:] and float((alpha < 0.5).float().mean()) > 0.01:
            return (alpha,)
        return (cutout,)


class IrisEchoShade:
    """Light an unlit texture render with a shaded clay render of the same view."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"color": ("IMAGE",), "clay": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "shade"
    CATEGORY = "irisecho"

    def shade(self, color, clay):
        light = clay[..., :3].mean(-1, keepdim=True)
        light = light / light.amax().clamp_min(1e-6)
        return ((color[..., :3] * (0.35 + 0.75 * light)).clamp(0, 1),)


class IrisEchoRGBA:
    """A picture with its mask as transparency (1 = opaque), for saving cut-outs and thumbnails."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",), "mask": ("MASK",)}}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "join"
    CATEGORY = "irisecho"

    def join(self, image, mask):
        if mask.shape[-2:] != image.shape[1:3]:
            mask = F.interpolate(mask[:, None], size=image.shape[1:3], mode="bilinear")[:, 0]
        return (torch.cat([image[..., :3], mask[..., None].clamp(0, 1)], dim=-1),)


NODE_CLASS_MAPPINGS = {
    "IrisEchoChooseMask": IrisEchoChooseMask,
    "IrisEchoShade": IrisEchoShade,
    "IrisEchoRGBA": IrisEchoRGBA,
    "IrisEchoFrameViews": IrisEchoFrameViews,
    "IrisEchoViewCheck": IrisEchoViewCheck,
    "IrisEchoSimilarity": IrisEchoSimilarity,
}
