# Brand

## The idea

In Greek myth Iris is the rainbow messenger and Echo the voice that answers.
In anatomy, the iris is the ring of radial fibers around the pupil. The mark
draws that ring as a circular audio spectrum: each bar is an iris fiber and a
frequency band at once, so one picture covers both halves of the app, the
images and the sound. Broken rings outside it are the echo.

The mark stands in for the final "o" of the wordmark: **IrisEch**◉.

## Palette

Named for the parts of an eye. The warm amber sits where a hazel eye's
collarette does, nearest the pupil.

| Name | Hex | Use |
|---|---|---|
| Pupil | `#0E0B16` | Backgrounds, text on light |
| Limbus | `#2A2140` | Raised surfaces, rings |
| Iris violet | `#6B5BD6` | Primary accent, image features |
| Stroma teal | `#2FB8A6` | Secondary accent, sound features |
| Collarette amber | `#F2A541` | Highlights, sparingly |
| Sclera | `#F3F0FA` | Text on dark, light backgrounds |

## Type

[Bricolage Grotesque](https://github.com/ateliertriay/bricolage), SIL Open
Font License 1.1. The wordmark is set at weight 700, width 88, optical size 96,
tracked slightly tight, and converted to outlines, so no font file ships.

## Files

| File | For |
|---|---|
| `docs/assets/mark.svg` | The mark alone |
| `docs/assets/icon.svg`, `icon-1024.png` | App and installer icons |
| `docs/assets/wordmark-on-dark.svg`, `wordmark-on-light.svg` | Wordmark for dark and light backgrounds |
| `docs/assets/banner.png` | README header |
| `docs/assets/social-preview.png` | GitHub social preview (1280 × 640) |
| `docs/assets/art/*.jpg` | Generated art used in the banner |
| `docs/assets/screens/*.jpg` | README screenshots of the app |

## Rebuilding

```sh
uv run --no-project --with fonttools --with uharfbuzz python docs/brand/build_brand.py
python docs/brand/render_banner.py   # needs Edge or Chrome
```

Both are deterministic. Generated PNGs are stripped of metadata by the render
script; run `python scripts/scrub_media.py` on any other PNG before
committing it.

## Where the art came from

All generated with Apache-2.0 models, locally, and committed without editing
other than resizing and cropping. Distilled models like these ignore
negatives, so prompts describe only what should be there.

| File | Model | Seed | Prompt |
|---|---|---|---|
| `art/hero.jpg` | Z-Image-Turbo (int4) | 23 | Extreme macro photograph of a human iris, fine radial fibers glowing warm amber around the pupil and fading to teal and deep violet at the outer edge, the fibers arranged like a circular audio spectrum, deep indigo darkness around the eye, shallow depth of field, soft cinematic light |

## What the screenshots show

The screenshots are of the app itself with real results in it, captured from a
headless browser at twice the size and scaled to 2000 px wide. The graphics
card name in the corner is a generic one. Everything in them was made with
Apache-2.0 models.

| Shown in | Model | Seed | Prompt |
|---|---|---|---|
| Image | Z-Image Turbo (int4) | 506152904558426 | A vibrant cyberpunk street market at night, rain-slicked cobblestone streets reflecting colorful neon signage in electric pink, turquoise, and violet. Atmospheric mist, detailed food stalls with glowing lanterns, stylized anime art aesthetic, bold outlines, cinematic composition |
| Image | Z-Image Turbo (int4) | 352663020456202 | A macro close-up photograph of an antique mechanical wristwatch, intricate gearwork and brass balance wheel exposed through a skeleton dial. Soft, directional studio lighting with subtle golden highlights, shallow depth of field, sharp focus on the rubies and gear teeth, photorealistic texture, 8k resolution |
| Image, lightbox | Z-Image Turbo (int4) | 170508256300735 | A portrait of an elderly sea captain with a weathered face, deep wrinkles, and a full salt-and-pepper beard. Wearing a heavy dark blue wool nautical jacket with brass buttons. Catchlights in his blue eyes, outdoor overcast lighting, dramatic portrait photography. |
| Image, Edit (the picture being changed) | Z-Image Turbo (int4) | 817046239582612 | A minimal ceramic bowl containing exactly three glossy green apples, sitting on a natural light wood table next to a single white porcelain mug. Clean indoor daylight coming from a side window, soft realistic shadows, minimalist Scandinavian aesthetic. |
| Edit | Qwen Edit Fast (int4) | 848756700721720 | Change only the Apples color to bright pink |
| Edit | Qwen Edit Fast (int4) | 11 | Change only the apples to deep glossy red |
| Edit | Qwen Edit Fast (int4) | 13 | Make it early evening with warm golden light from the window and long soft shadows, keep everything else the same |
| Edit | Qwen Edit Fast (int4) | 12 | Replace the white mug with a small potted succulent, keep everything else the same |

The Voice screenshot holds four lines read by Kokoro's George voice. `phone.jpg` is three
captures at a phone's size of what a paired phone is served, set side by side.
