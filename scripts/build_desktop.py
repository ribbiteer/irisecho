#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the desktop installer for this platform.

    uv run python scripts/build_desktop.py

1. builds the web UI into the core package
2. builds the core wheel and checks the UI is inside it
3. stages the wheel and a `uv` binary as app resources
4. runs `tauri build` (NSIS installer on Windows, .app and .dmg on macOS)

Needs Node.js and a Rust toolchain. Output lands in
desktop/src-tauri/target/release/bundle/.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "desktop" / "src-tauri" / "resources"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"
NPX = "npx.cmd" if sys.platform == "win32" else "npx"


def run(args: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(args)}", flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def main() -> int:
    run([NPM, "ci", "--no-audit", "--no-fund"], ROOT / "ui")
    run([NPM, "run", "build"], ROOT / "ui")

    if RES.exists():
        shutil.rmtree(RES)
    RES.mkdir(parents=True)
    run(["uv", "build", "--wheel", "--out-dir", str(RES), str(ROOT / "core")], ROOT)
    (RES / ".gitignore").unlink(missing_ok=True)  # uv build writes one; keep it out of the app
    (wheel,) = RES.glob("irisecho_core-*.whl")
    with zipfile.ZipFile(wheel) as z:
        if "irisecho_core/web/index.html" not in z.namelist():
            sys.exit("The wheel is missing the web UI.")

    from uv import find_uv_bin

    uv = Path(find_uv_bin())
    shutil.copy2(uv, RES / uv.name)
    print(f"staged {wheel.name} and {uv.name}")

    bundles = {"win32": "nsis", "darwin": "app,dmg"}.get(sys.platform, "appimage,deb")
    run([NPM, "ci", "--no-audit", "--no-fund"], ROOT / "desktop")
    run([NPX, "tauri", "build", "--bundles", bundles], ROOT / "desktop")
    out = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle"
    for f in sorted(out.rglob("*")):
        if f.suffix in (".exe", ".dmg", ".AppImage", ".deb") and f.is_file():
            print(f"built {f.relative_to(ROOT)} ({f.stat().st_size / 2**20:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
