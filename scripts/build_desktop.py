#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the desktop installer for this platform.

    uv run python scripts/build_desktop.py

1. builds the web UI into the core package
2. builds the core wheel and checks the UI is inside it
3. stages the wheel, a `uv` binary and the core's dependencies as uv.lock pins
   them (constraints.txt) as app resources
4. runs `tauri build` (NSIS installer on Windows, .app and .dmg on macOS)

Needs Node.js and a Rust toolchain. Output lands in
desktop/src-tauri/target/release/bundle/.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "desktop" / "src-tauri" / "resources"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"
NPX = "npx.cmd" if sys.platform == "win32" else "npx"
CONSTRAINTS = "constraints.txt"  # the shell looks for this name (main.rs)
PINNED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
REQUIRES = re.compile(r"(?m)^Requires-Dist: ([A-Za-z0-9][A-Za-z0-9._-]*)")


def run(args: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(args)}", flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def export_constraints(dest: Path) -> dict[str, list[str]]:
    """Write the core's runtime dependencies, exactly as uv.lock records them.

    The shell installs the core wheel with this file as its constraints, so an
    installer keeps installing the versions it was released with whatever is
    published later. Versions for every platform are in it, each behind its
    marker, with the hashes of the files uv may download. Returns the versions
    recorded for each package.
    """
    from uv import find_uv_bin

    args = [find_uv_bin(), "export", "--locked", "--no-dev", "--package", "irisecho-core"]
    args += ["--no-emit-workspace", "--no-header", "--format", "requirements.txt"]
    run([*args, "--output-file", str(dest), "--quiet"], ROOT)
    pins: dict[str, list[str]] = {}
    for line in dest.read_text(encoding="utf-8").splitlines():
        m = PINNED.match(line)
        if m:
            pins.setdefault(canonical(m.group(1)), []).append(m.group(2))
        elif line and not line.startswith((" ", "#")):
            sys.exit(f"{dest.name} has a requirement without an exact version: {line}")
    if not pins:
        sys.exit(f"{dest.name} is empty")
    return pins


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
    pins = export_constraints(RES / CONSTRAINTS)
    with zipfile.ZipFile(wheel) as z:
        (metadata,) = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
        needs = REQUIRES.findall(z.read(metadata).decode("utf-8"))
    unpinned = sorted({canonical(n) for n in needs} - set(pins))
    if unpinned:
        sys.exit(f"uv.lock records no version for: {', '.join(unpinned)}")
    print(f"staged {wheel.name}, {uv.name} and {CONSTRAINTS} ({len(pins)} pinned packages)")

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
