# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render banner.html to PNGs with a headless Chromium browser (Edge or Chrome).

    python docs/brand/render_banner.py

Writes docs/assets/banner.png (README), docs/assets/social-preview.png
(GitHub Settings > Social preview, 1280 x 640) and docs/assets/icon-1024.png
(source for app and installer icons), then strips their metadata.
Run build_brand.py first so the wordmark and cached font exist.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ASSETS = HERE.parent / "assets"
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
from scrub_media import scrub_png  # noqa: E402

SIZES = {"banner.png": (1280, 440), "social-preview.png": (1280, 640)}

CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    shutil.which("chromium") or "",
    shutil.which("google-chrome") or "",
]


def browser() -> str:
    for c in CANDIDATES:
        if c and Path(c).exists():
            return c
    sys.exit("No Chromium browser found; set CHROME to its executable.")


def shoot(exe: str, url: str, out: Path, w: int, h: int, transparent: bool = False) -> None:
    with tempfile.TemporaryDirectory() as profile:
        args = [
            exe,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            "--force-device-scale-factor=1",
            f"--user-data-dir={profile}",
            f"--window-size={w},{h}",
            f"--screenshot={out}",
        ]
        if transparent:
            args.append("--default-background-color=00000000")
        subprocess.run([*args, url], check=True, capture_output=True)
    clean, _ = scrub_png(out.read_bytes())
    out.write_bytes(clean)
    print(f"wrote {out} ({w}x{h})")


def main() -> None:
    exe = browser()
    page = (HERE / "banner.html").resolve().as_uri()
    for name, (w, h) in SIZES.items():
        shoot(exe, f"{page}?w={w}&h={h}", ASSETS / name, w, h)

    with tempfile.TemporaryDirectory() as tmp:
        wrapper = Path(tmp) / "icon.html"
        icon = (ASSETS / "icon.svg").resolve().as_uri()
        wrapper.write_text(
            f'<html><body style="margin:0;background:transparent">'
            f'<img src="{icon}" width="1024" height="1024"></body></html>',
            encoding="utf-8",
        )
        shoot(exe, wrapper.as_uri(), ASSETS / "icon-1024.png", 1024, 1024, transparent=True)


if __name__ == "__main__":
    main()
