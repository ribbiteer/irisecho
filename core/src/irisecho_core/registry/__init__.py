# SPDX-License-Identifier: AGPL-3.0-or-later
"""The model registry: what can be downloaded, under which license, for which machine."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from irisecho_core.hardware import Hardware

HERE = Path(__file__).parent
HF = "https://huggingface.co"


@dataclass(frozen=True)
class License:
    id: str
    name: str
    url: str
    commercial: bool
    accept: bool = False  # must be accepted in the app before download
    summary: str = ""
    condition: str = ""  # a limit on commercial use, shown next to the license
    gated: bool = False  # the host also asks for sign-in and acceptance


@dataclass(frozen=True)
class FileSpec:
    id: str
    repo: str
    revision: str
    path: str
    category: str
    size: int
    sha256: str
    save_as: str = ""
    git_sha1: str = ""  # used instead of sha256 for small files in gated repos
    subdir: str = ""  # kept from the repo path when the engine needs the folder layout
    group: str = ""  # the multi-file entry this file belongs to
    keep_dirs: bool = False
    no_link: bool = False  # always use IrisEcho's own copy (the engine writes next to it)

    @property
    def layout(self) -> str:
        """Where this file sits relative to its group's folder (keep_dirs groups only)."""
        rel = self.category.split("/", 1)[1] if "/" in self.category else ""
        return posixpath.join(rel, self.subdir, self.filename)

    @property
    def pinned(self) -> bool:
        """Whether the recorded hash is a real one that a download can be checked against."""
        if self.sha256:
            return len(self.sha256) == 64 and all(c in "0123456789abcdef" for c in self.sha256)
        return len(self.git_sha1) == 40 and all(c in "0123456789abcdef" for c in self.git_sha1)

    @property
    def filename(self) -> str:
        return self.save_as or posixpath.basename(self.path)

    @property
    def url(self) -> str:
        return f"{HF}/{self.repo}/resolve/{self.revision}/{self.path}"

    @property
    def page(self) -> str:
        return f"{HF}/{self.repo}"


@dataclass(frozen=True)
class Variant:
    when: dict
    files: tuple[str, ...]
    workflow: str = ""
    preview: bool = False

    def matches(self, hw: Hardware) -> bool:
        return all(getattr(hw, key, None) == value for key, value in self.when.items())


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    kind: str
    engine: str
    license: str
    summary: str
    variants: tuple[Variant, ...]
    settings: dict = field(default_factory=dict)

    def variant_for(self, hw: Hardware) -> Variant | None:
        return next((v for v in self.variants if v.matches(hw)), None)


@dataclass(frozen=True)
class Registry:
    licenses: dict[str, License]
    files: dict[str, FileSpec]
    groups: dict[str, tuple[str, ...]]
    models: dict[str, ModelSpec]

    def expand(self, ids: tuple[str, ...] | list[str]) -> list[FileSpec]:
        """Resolve file ids and group ids to concrete files, in order, without repeats."""
        seen: dict[str, FileSpec] = {}
        for i in ids:
            for fid in self.groups.get(i, (i,)):
                seen.setdefault(fid, self.files[fid])
        return list(seen.values())

    def model_files(self, model: ModelSpec, hw: Hardware) -> list[FileSpec]:
        variant = model.variant_for(hw)
        return self.expand(variant.files) if variant else []


def load(path: Path = HERE / "models.yaml", lock_path: Path = HERE / "lock.json") -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    revisions = raw["repos"]

    licenses = {k: License(id=k, **v) for k, v in raw["licenses"].items()}

    files: dict[str, FileSpec] = {}
    groups: dict[str, tuple[str, ...]] = {}
    for fid, entry in raw["files"].items():
        repo = entry["repo"]
        revision = revisions[repo]
        paths = entry.get("paths") or [entry["path"]]
        ids = []
        for p in paths:
            key = f"{repo}@{revision}:{p}"
            if key not in lock:
                raise ValueError(f"{key} is not in lock.json; run scripts/lock_registry.py")
            keep = entry.get("keep_dirs", False)
            if "path" in entry:
                sub_id = fid
            else:
                sub_id = f"{fid}/{p if keep else posixpath.basename(p)}"
            files[sub_id] = FileSpec(
                id=sub_id,
                repo=repo,
                revision=revision,
                path=p,
                category=entry["category"],
                size=lock[key]["size"],
                sha256=lock[key].get("sha256", ""),
                git_sha1=lock[key].get("git_sha1", ""),
                save_as=entry.get("save_as", ""),
                subdir=posixpath.dirname(p) if keep else "",
                group=fid if "paths" in entry else "",
                keep_dirs=keep,
                no_link=entry.get("no_link", False),
            )
            ids.append(sub_id)
        if "paths" in entry:
            groups[fid] = tuple(ids)

    models = {}
    for mid, m in raw["models"].items():
        if m["license"] not in licenses:
            raise ValueError(f"model {mid} uses unknown license {m['license']}")
        variants = tuple(
            Variant(
                when=v.get("when") or {},
                files=tuple(v["files"]),
                workflow=v.get("workflow", ""),
                preview=v.get("preview", False),
            )
            for v in m["variants"]
        )
        for v in variants:
            for f in v.files:
                if f not in files and f not in groups:
                    raise ValueError(f"model {mid} references unknown file {f}")
        models[mid] = ModelSpec(
            id=mid,
            name=m["name"],
            kind=m["kind"],
            engine=m["engine"],
            license=m["license"],
            summary=m.get("summary", ""),
            variants=variants,
            settings=m.get("settings") or {},
        )
    return Registry(licenses=licenses, files=files, groups=groups, models=models)


@lru_cache(maxsize=1)
def default() -> Registry:
    return load()
