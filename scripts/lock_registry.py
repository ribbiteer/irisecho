#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pin the size and sha256 of every file in the model registry.

    uv run python scripts/lock_registry.py [--check]

Reads core/src/irisecho_core/registry/models.yaml, asks the Hugging Face API
for each file at the pinned revision, and writes registry/lock.json. Small
files that are not stored in LFS are downloaded and hashed. With --check,
exits non-zero if lock.json is missing entries, or holds one that is not a
real hash, instead of writing it.

Hugging Face hides the hashes of a gated model's files from anyone not signed
in (it answers with asterisks). To lock one, accept its terms on its page and
set HF_TOKEN to a read token of your own for this run; it is sent only to
huggingface.co.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

import httpx
import yaml

REGISTRY = Path(__file__).parents[1] / "core" / "src" / "irisecho_core" / "registry"
HF = "https://huggingface.co"
SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_SHA1 = re.compile(r"[0-9a-f]{40}")


def auth() -> dict[str, str]:
    token = os.environ.get("HF_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def well_formed(entry: dict) -> bool:
    """A real hash, not a placeholder such as the asterisks Hugging Face shows for gated files."""
    if "sha256" in entry:
        return bool(SHA256.fullmatch(entry["sha256"]))
    return bool(GIT_SHA1.fullmatch(entry.get("git_sha1", "")))


def file_paths(entry: dict) -> list[str]:
    return entry["paths"] if "paths" in entry else [entry["path"]]


def lock_key(repo: str, revision: str, path: str) -> str:
    return f"{repo}@{revision}:{path}"


def paths_info(repo: str, revision: str, paths: list[str]) -> dict[str, dict]:
    url = f"{HF}/api/models/{repo}/paths-info/{revision}"
    data = [("paths", p) for p in paths] + [("expand", "true")]
    r = httpx.post(
        url,
        content=urllib.parse.urlencode(data),
        timeout=60,
        follow_redirects=True,
        headers={"Content-Type": "application/x-www-form-urlencoded", **auth()},
    )
    r.raise_for_status()
    return {item["path"]: item for item in r.json()}


def tree_info(repo: str, revision: str) -> dict[str, dict]:
    """Public file listing with hashes; works for gated repos where paths-info does not."""
    url = f"{HF}/api/models/{repo}/tree/{revision}?recursive=true&expand=true"
    out: dict[str, dict] = {}
    while url:
        r = httpx.get(url, timeout=60, follow_redirects=True, headers=auth())
        r.raise_for_status()
        for item in r.json():
            if item.get("type") == "file":
                out[item["path"]] = item
        url = r.links.get("next", {}).get("url")
    return out


def hash_download(repo: str, revision: str, path: str) -> str:
    url = f"{HF}/{repo}/resolve/{revision}/{urllib.parse.quote(path)}"
    digest = hashlib.sha256()
    with httpx.stream("GET", url, timeout=120, follow_redirects=True, headers=auth()) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    registry = yaml.safe_load((REGISTRY / "models.yaml").read_text(encoding="utf-8"))
    lock_path = REGISTRY / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}

    wanted: dict[str, list[str]] = defaultdict(list)
    for entry in registry["files"].values():
        for path in file_paths(entry):
            wanted[entry["repo"]].append(path)

    missing = []
    fresh = {}
    for repo, paths in wanted.items():
        revision = registry["repos"][repo]
        # An entry without a real hash is locked again, as if it were missing.
        todo = [p for p in paths if not well_formed(lock.get(lock_key(repo, revision, p), {}))]
        for p in paths:
            key = lock_key(repo, revision, p)
            if p not in todo:
                fresh[key] = lock[key]
        if not todo:
            continue
        if args.check:
            missing += [lock_key(repo, revision, p) for p in todo]
            continue
        gated = False
        try:
            info = paths_info(repo, revision, todo)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (401, 403):
                raise
            gated = True
            info = tree_info(repo, revision)
        for p in todo:
            item = info.get(p)
            if item is None:
                sys.exit(f"{repo}@{revision} has no file {p}")
            lfs = item.get("lfs") or {}
            if lfs.get("oid") and not SHA256.fullmatch(lfs["oid"]):
                sys.exit(
                    f"{repo}: Hugging Face hid the hash of {p}. Accept the model's terms on "
                    f"{HF}/{repo} and run again with HF_TOKEN set to a read token."
                )
            if lfs.get("oid"):
                entry = {"size": item["size"], "sha256": lfs["oid"]}
            elif gated:
                # Small files in a gated repo: pin git's blob hash instead.
                entry = {"size": item["size"], "git_sha1": item["oid"]}
            else:
                entry = {"size": item["size"], "sha256": hash_download(repo, revision, p)}
            fresh[lock_key(repo, revision, p)] = entry
            print(f"locked {repo}:{p} ({item['size'] / 2**20:.0f} MB)")

    if args.check:
        for key in missing:
            print(f"not locked, or not a real hash: {key}", file=sys.stderr)
        return 1 if missing else 0

    with open(lock_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(dict(sorted(fresh.items())), indent=1) + "\n")
    print(f"wrote {lock_path} ({len(fresh)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
