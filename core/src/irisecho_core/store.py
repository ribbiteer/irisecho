# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where each weight file is, and whether it is complete.

Downloaded files live at <models>/<category>/<filename>. People who already
have weights (a ComfyUI "models" folder, say) can link that folder: IrisEcho
finds registry files there by size, confirms them by sha256, and uses them in
place without copying. Linked folders are only ever read.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from irisecho_core import paths
from irisecho_core.registry import FileSpec, Registry

# Folder names other tools use for the same kind of file.
CATEGORY_ALIASES = {
    "diffusion_models": ("diffusion_models", "unet"),
    "text_encoders": ("text_encoders", "clip"),
    "vae": ("vae",),
    "loras": ("loras",),
    "upscale_models": ("upscale_models",),
    "frame_interpolation": ("frame_interpolation",),
}

# How deep into a linked folder to look. Deep enough that pointing at a parent
# (a whole ComfyUI folder, say) still finds models/LLM/<family>/<model>/file.
SCAN_DEPTH = 6
# Folders that never hold model weights and can hold tens of thousands of files.
SKIP_DIRS = {
    ".git",
    ".cache",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "site-packages",
    "python_embeded",
    "custom_nodes",
    "$recycle.bin",
    "system volume information",
}


@dataclass
class FileStatus:
    id: str
    state: str  # "ready" | "linked" | "partial" | "missing"
    size: int
    have: int
    path: str = ""

    def public(self) -> dict:
        return self.__dict__.copy()


class Hasher:
    """sha256, or git's blob sha1 for files pinned that way."""

    def __init__(self, spec: FileSpec):
        self.git = not spec.sha256
        self.h = hashlib.sha1() if self.git else hashlib.sha256()
        if self.git:
            self.h.update(f"blob {spec.size}\0".encode())
        self.expected = spec.git_sha1 if self.git else spec.sha256

    def update(self, chunk: bytes) -> None:
        self.h.update(chunk)

    def ok(self) -> bool:
        return self.h.hexdigest() == self.expected


def file_matches(path: Path, spec: FileSpec, progress: Callable[[int], None] | None = None) -> bool:
    hasher = Hasher(spec)
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            hasher.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return hasher.ok()


def sha256_file(path: Path, progress: Callable[[int], None] | None = None) -> str:
    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return digest.hexdigest()


class Store:
    def __init__(self, registry: Registry, models_dir: Path, linked_dirs: Iterable[str] = ()):
        self.registry = registry
        self.root = models_dir
        self.linked_dirs = [Path(d) for d in linked_dirs]
        self.index_path = paths.data_dir() / "linked.json"
        self.scanned_path = paths.data_dir() / "linked-scan.txt"
        self.links: dict[str, dict] = self._load_links()
        self._sizes: dict[int, list[Path]] | None = None  # built once per scan
        self._digests: dict[Path, str] = {}  # sha256 of files read during this scan
        self._on_link: Callable[[str], None] | None = None

    # --- paths -------------------------------------------------------------

    def own_path(self, spec: FileSpec) -> Path:
        return self.root / spec.category / spec.subdir / spec.filename

    def part_path(self, spec: FileSpec) -> Path:
        return self.own_path(spec).with_name(spec.filename + ".part")

    def locate(self, spec: FileSpec) -> Path | None:
        own = self.own_path(spec)
        if own.is_file() and own.stat().st_size == spec.size:
            return own
        link = self.links.get(spec.id)
        if link:
            p = Path(link["path"])
            try:
                st = p.stat()
            except OSError:
                return None
            if st.st_size == spec.size and st.st_mtime_ns == link["mtime"]:
                return p
        return None

    def status(self, spec: FileSpec) -> FileStatus:
        own = self.own_path(spec)
        if own.is_file() and own.stat().st_size == spec.size:
            return FileStatus(spec.id, "ready", spec.size, spec.size, str(own))
        found = self.locate(spec)
        if found:
            return FileStatus(spec.id, "linked", spec.size, spec.size, str(found))
        part = self.part_path(spec)
        if part.is_file():
            return FileStatus(spec.id, "partial", spec.size, part.stat().st_size)
        return FileStatus(spec.id, "missing", spec.size, 0)

    def all_ready(self, specs: Iterable[FileSpec]) -> bool:
        return all(self.locate(s) is not None for s in specs)

    def category_dirs(self, category: str) -> list[Path]:
        """Every folder files of this category may be read from: ours first, then linked."""
        dirs = [self.root / category]
        for link in list(self.links.values()):  # a scan may be adding links on its own thread
            if link["category"] == category:
                parent = Path(link["path"]).parent
                if parent not in dirs:
                    dirs.append(parent)
        return dirs

    # --- linked folders ----------------------------------------------------

    def _load_links(self) -> dict[str, dict]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_links(self) -> None:
        # Written whole and swapped in: a crash mid-write must not lose the index.
        tmp = self.index_path.with_name(self.index_path.name + ".tmp")
        tmp.write_text(json.dumps(self.links, indent=1), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def fingerprint(self, dirs: Iterable[Path] | None = None) -> str:
        """Changes whenever the registry or the linked folders change."""
        dirs = self.linked_dirs if dirs is None else dirs
        h = hashlib.sha256()
        for item in [*sorted(self.registry.files), *sorted(str(x) for x in dirs)]:
            h.update(item.encode() + b"|")
        return h.hexdigest()

    def needs_scan(self) -> bool:
        """True when the folders linked now are not the ones the last finished scan looked at."""
        try:
            return self.scanned_path.read_text(encoding="utf-8").strip() != self.fingerprint()
        except OSError:
            return bool(self.linked_dirs)

    def _index_sizes(self, dirs: Iterable[Path]) -> dict[int, list[Path]]:
        """Every file in the linked folders, by size. One walk serves the whole scan."""
        index: dict[int, list[Path]] = {}
        seen: set[str] = set()
        for base in dirs:
            for path, size in _walk(base, SCAN_DEPTH):
                key = str(path).lower()
                if key not in seen:
                    seen.add(key)
                    index.setdefault(size, []).append(path)
        return index

    def candidates(self, spec: FileSpec) -> list[Path]:
        """Files in linked folders with this file's size: same name first, then likeliest folder."""
        if self._sizes is None:
            self._sizes = self._index_sizes(self.linked_dirs)
        names = {n.lower() for n in CATEGORY_ALIASES.get(spec.category, (spec.category,))}
        found = self._sizes.get(spec.size, [])
        return sorted(
            found,
            key=lambda p: (
                p.name.lower() != spec.filename.lower(),
                p.parent.name.lower() not in names,
            ),
        )

    def _matches(self, path: Path, spec: FileSpec, report: Callable[[int], None]) -> bool:
        """Is this file the one the registry pins? Each file is read once per scan at most."""
        try:
            if not spec.sha256:
                return file_matches(path, spec, report)
            digest = self._digests.get(path)
            if digest is None:
                digest = self._digests[path] = sha256_file(path, report)
            else:
                report(spec.size)
            return digest == spec.sha256
        except OSError:
            return False  # unreadable or in use right now: not a reason to stop the scan

    def scan_links(
        self,
        progress: Callable[[str, int, int, int, int], None] | None = None,
        on_link: Callable[[str], None] | None = None,
    ) -> dict[str, str]:
        """Find registry files in linked folders. Returns {file_id: path} of new links.

        `progress(file_id, done, total, overall_done, overall_total)` counts bytes
        of the files being compared, so a long scan can show how far along it is.
        `on_link(file_id)` is called as each file is confirmed; the link is usable
        and saved from that moment, not only when the whole scan ends.
        """
        # The folders as they are now; a change made meanwhile is for the next scan.
        dirs = list(self.linked_dirs)
        self._on_link = on_link
        self._digests = {}
        try:
            self._sizes = self._index_sizes(dirs)
            found = self._scan_links(dirs, progress)
            self.scanned_path.write_text(self.fingerprint(dirs), encoding="utf-8")
            return found
        finally:
            self._sizes = None
            self._digests = {}
            self._on_link = None

    def _scan_links(self, dirs: list[Path], on_progress) -> dict[str, str]:
        # Forget links into folders that are no longer linked.
        self.links = {
            k: v
            for k, v in self.links.items()
            if any(Path(v["path"]).is_relative_to(d) for d in dirs)
        }
        # A keep_dirs group stays linked only if all of it is, each file in its place.
        by_group: dict[str, list[FileSpec]] = {}
        for spec in self.registry.files.values():
            if spec.keep_dirs:
                by_group.setdefault(spec.group, []).append(spec)
        for specs in by_group.values():
            links = [self.links.get(s.id) for s in specs]
            intact = all(
                link and Path(link["path"]).as_posix().lower().endswith(s.layout.lower())
                for s, link in zip(specs, links, strict=True)
            )
            if not intact:
                for s in specs:
                    self.links.pop(s.id, None)

        for spec in self.registry.files.values():
            if spec.no_link:
                self.links.pop(spec.id, None)
        found: dict[str, str] = {}
        missing = [s for s in self.registry.files.values() if not s.no_link and not self.locate(s)]
        overall_total = sum(s.size for s in missing if self.candidates(s))
        tick_state = {"file": "", "last": 0, "base": 0}
        inner = on_progress

        def progress(file_id: str, done: int, total: int) -> None:
            if not inner:
                return
            if file_id != tick_state["file"]:
                tick_state["base"] += tick_state["last"]
                tick_state.update(file=file_id, last=total)
            inner(
                file_id, done, total, min(overall_total, tick_state["base"] + done), overall_total
            )

        # Files that must keep their folder layout are linked a whole group at a time.
        groups: dict[str, list[FileSpec]] = {}
        for spec in missing:
            if spec.keep_dirs:
                groups.setdefault(spec.group, []).append(spec)
        for specs in groups.values():
            found.update(self._link_group(specs, progress))

        for spec in missing:
            if spec.keep_dirs:
                continue
            for cand in self.candidates(spec):

                def report(done: int, spec: FileSpec = spec) -> None:
                    if progress:
                        progress(spec.id, done, spec.size)

                if self._matches(cand, spec, report):
                    self._link(spec, cand)
                    self._linked([spec])
                    found[spec.id] = str(cand)
                    break
        self._save_links()
        return found

    def _link(self, spec: FileSpec, path: Path) -> None:
        self.links[spec.id] = {
            "path": str(path),
            "category": spec.category,
            "mtime": path.stat().st_mtime_ns,
        }

    def _linked(self, specs: list[FileSpec]) -> None:
        """Keep what has been confirmed so far, and say so."""
        self._save_links()
        if self._on_link:
            for spec in specs:
                self._on_link(spec.id)

    def _link_group(self, specs: list[FileSpec], progress) -> dict[str, str]:
        """Link a keep_dirs group only from a folder that holds all of it, laid out as expected."""
        anchor = max(specs, key=lambda s: s.size)
        tried: set[Path] = set()
        for cand in self.candidates(anchor):
            parts = Path(anchor.layout).parts
            if tuple(p.lower() for p in cand.parts[-len(parts) :]) != tuple(
                p.lower() for p in parts
            ):
                continue
            root = cand.parents[len(parts) - 1]
            if root in tried:
                continue
            tried.add(root)
            paths = {s.id: root / s.layout for s in specs}
            if not all(
                p.is_file() and p.stat().st_size == s.size
                for s, p in zip(specs, paths.values(), strict=True)
            ):
                continue
            ok = True
            for spec in specs:

                def report(done: int, spec: FileSpec = spec) -> None:
                    if progress:
                        progress(spec.id, done, spec.size)

                if not self._matches(paths[spec.id], spec, report):
                    ok = False
                    break
            if ok:
                for spec in specs:
                    self._link(spec, paths[spec.id])
                self._linked(specs)
                return {k: str(v) for k, v in paths.items()}
        return {}

    # --- housekeeping -----------------------------------------------------

    def remove(self, spec: FileSpec) -> bool:
        """Delete our own copy (never a linked file)."""
        removed = False
        for p in (self.own_path(spec), self.part_path(spec)):
            if p.is_file():
                p.unlink()
                removed = True
        return removed


def _walk(root: Path, depth: int):
    """Yield (path, size) for files under root, `depth` folders down, skipping junk folders."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_file(follow_symlinks=True):
                yield Path(e.path), e.stat().st_size
            elif e.is_dir(follow_symlinks=False) and depth > 0 and e.name.lower() not in SKIP_DIRS:
                yield from _walk(Path(e.path), depth - 1)
        except OSError:
            continue
