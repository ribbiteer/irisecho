# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the IrisEcho mark, wordmarks and icon as SVG.

The mark is an eye whose iris is a circular audio waveform: each radial bar is
both an iris fiber and a spectrum band. Output is deterministic.

    uv run --no-project --with fonttools --with uharfbuzz python docs/brand/build_brand.py

The wordmark font (Bricolage Grotesque, SIL OFL 1.1) is downloaded on first
run into docs/brand/.cache/ and converted to outlines; it is not committed.
"""

from __future__ import annotations

import io
import math
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE.parent / "assets"
CACHE = HERE / ".cache"
FONT_URL = (
    "https://github.com/google/fonts/raw/main/ofl/bricolagegrotesque/"
    "BricolageGrotesque%5Bopsz,wdth,wght%5D.ttf"
)

# Palette, named for the parts of an eye.
PUPIL = "#0E0B16"  # ink
LIMBUS = "#2A2140"  # dark outer ring of the iris
VIOLET = "#6B5BD6"  # iris
TEAL = "#2FB8A6"  # stroma
AMBER = "#F2A541"  # collarette, the warm ring nearest the pupil
SCLERA = "#F3F0FA"  # light

WORD = "IrisEch"  # the mark is the final "o"


def waveform(n: int) -> list[float]:
    """Deterministic, organic-looking spectrum in 0..1 (sum of sines)."""
    vals = []
    for i in range(n):
        t = 2 * math.pi * i / n
        v = (
            0.58
            + 0.07 * math.sin(3 * t + 0.6)
            + 0.13 * math.sin(8 * t + 1.9)
            + 0.12 * math.sin(13 * t + 0.3)
            + 0.10 * math.sin(31 * t + 2.2)
        )
        vals.append(min(1.0, max(0.0, v)))
    return vals


def mark_group(
    *,
    bars: int = 96,
    bar_width: float = 5.0,
    pupil_r: float = 72,
    inner_gap: float = 12,
    min_len: float = 34,
    max_len: float = 118,
    echoes: bool = True,
    id_prefix: str = "m",
) -> str:
    """The mark centered at (256, 256) in a 512 x 512 box."""
    c = 256
    r0 = pupil_r + inner_gap
    r_max = r0 + max_len
    grad = f"{id_prefix}-fiber"
    parts = [
        "<defs>",
        f'<radialGradient id="{grad}" cx="{c}" cy="{c}" r="{r_max}" '
        'gradientUnits="userSpaceOnUse">',
        f'<stop offset="{r0 / r_max:.3f}" stop-color="{AMBER}"/>',
        f'<stop offset="{(r0 + 0.45 * max_len) / r_max:.3f}" stop-color="{TEAL}"/>',
        f'<stop offset="1" stop-color="{VIOLET}"/>',
        "</radialGradient>",
        "</defs>",
    ]
    if echoes:
        for k, (dr, op) in enumerate(((18, 0.55), (38, 0.28))):
            r = r_max + dr
            # Echo rings are broken arcs: an echo is never the whole sound.
            circ = 2 * math.pi * r
            dash = f"{circ * 0.62:.1f} {circ * 0.38:.1f}"
            parts.append(
                f'<circle cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="{VIOLET}" '
                f'stroke-width="{bar_width * 0.9:.1f}" stroke-linecap="round" '
                f'stroke-dasharray="{dash}" stroke-opacity="{op}" '
                f'transform="rotate({-120 + 40 * k} {c} {c})"/>'
            )
    lines = []
    for i, v in enumerate(waveform(bars)):
        a = 2 * math.pi * i / bars - math.pi / 2
        r1 = r0 + min_len + v * (max_len - min_len)
        x0, y0 = c + r0 * math.cos(a), c + r0 * math.sin(a)
        x1, y1 = c + r1 * math.cos(a), c + r1 * math.sin(a)
        lines.append(f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}"/>')
    parts.append(
        f'<g stroke="url(#{grad})" stroke-width="{bar_width}" stroke-linecap="round">'
        + "".join(lines)
        + "</g>"
    )
    parts.append(f'<circle cx="{c}" cy="{c}" r="{pupil_r}" fill="{PUPIL}"/>')
    parts.append(
        f'<circle cx="{c}" cy="{c}" r="{pupil_r}" fill="none" stroke="{LIMBUS}" '
        f'stroke-width="{bar_width * 0.8:.1f}"/>'
    )
    # Catchlight: the detail that makes it read as an eye.
    parts.append(
        f'<circle cx="{c - pupil_r * 0.36:.1f}" cy="{c - pupil_r * 0.38:.1f}" '
        f'r="{pupil_r * 0.2:.1f}" fill="{SCLERA}"/>'
    )
    return "".join(parts)


def svg(width: float, height: float, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{body}</svg>\n"
    )


def load_font() -> bytes:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / "BricolageGrotesque.ttf"
    if not path.exists():
        with urllib.request.urlopen(FONT_URL) as r:
            path.write_bytes(r.read())
    return path.read_bytes()


def word_paths(text: str, axes: dict[str, float], tracking: float) -> tuple[str, float, dict]:
    """Shape `text` and return (svg path data, advance width, metrics) in font units."""
    import uharfbuzz as hb
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    data = load_font()
    font = instantiateVariableFont(TTFont(io.BytesIO(data)), axes)
    buf_io = io.BytesIO()
    font.save(buf_io)
    static = buf_io.getvalue()

    hb_font = hb.Font(hb.Face(static))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hb_font, buf, {"kern": True, "liga": True})

    glyph_set = TTFont(io.BytesIO(static)).getGlyphSet()
    order = TTFont(io.BytesIO(static)).getGlyphOrder()
    pen = SVGPathPen(glyph_set)
    x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        name = order[info.codepoint]
        # Flip y: font units are y-up, SVG is y-down.
        glyph_set[name].draw(TransformPen(pen, (1, 0, 0, -1, x + pos.x_offset, -pos.y_offset)))
        x += pos.x_advance + tracking
    os2 = font["OS/2"]
    metrics = {"upm": font["head"].unitsPerEm, "xh": os2.sxHeight, "cap": os2.sCapHeight}
    return pen.getCommands(), x - tracking, metrics


def wordmark(text_color: str, id_prefix: str) -> str:
    axes = {"wght": 700, "wdth": 88, "opsz": 96}
    d, advance, m = word_paths(WORD, axes, tracking=-18)
    upm, xh = m["upm"], m["xh"]
    # The mark replaces a lowercase "o": its iris spans a little over the x-height.
    mark_d = xh * 1.12
    gap = upm * 0.035
    scale_mark = mark_d / 380  # the mark's drawn diameter is ~380 of its 512 box
    pad = upm * 0.08
    width = advance + gap + mark_d + 2 * pad
    height = m["cap"] + upm * 0.28 + 2 * pad
    baseline = pad + m["cap"] + upm * 0.06
    mark_cx = pad + advance + gap + mark_d / 2
    mark_cy = baseline - xh / 2
    mark = mark_group(
        bars=48,
        bar_width=10,
        pupil_r=74,
        inner_gap=14,
        min_len=72,
        max_len=100,
        echoes=False,
        id_prefix=id_prefix,
    )
    body = (
        f'<path d="{d}" fill="{text_color}" transform="translate({pad:.1f} {baseline:.1f})"/>'
        f'<g transform="translate({mark_cx - 256 * scale_mark:.1f} '
        f"{mark_cy - 256 * scale_mark:.1f}) "
        f'scale({scale_mark:.4f})">{mark}</g>'
    )
    return svg(width, height, body, "IrisEcho")


def icon() -> str:
    body = (
        "<defs>"
        f'<radialGradient id="i-glow" cx="256" cy="256" r="300" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{LIMBUS}"/><stop offset="1" stop-color="{PUPIL}"/>'
        "</radialGradient></defs>"
        '<rect x="16" y="16" width="480" height="480" rx="108" fill="url(#i-glow)"/>'
        '<g transform="translate(256 256) scale(0.86) translate(-256 -256)">'
        + mark_group(id_prefix="i")
        + "</g>"
    )
    return svg(512, 512, body, "IrisEcho")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "mark.svg": svg(512, 512, mark_group(), "IrisEcho"),
        "icon.svg": icon(),
        "wordmark-on-dark.svg": wordmark(SCLERA, "wd"),
        "wordmark-on-light.svg": wordmark(PUPIL, "wl"),
    }
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
