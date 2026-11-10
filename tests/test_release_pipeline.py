# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release scripts and workflows: versions, pinned installs, pinned actions."""

import importlib.util
import re
import shutil
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump = load("bump_version")
build = load("build_desktop")


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


# --- bump_version.py (P2-44) ----------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    """A copy of every file that records the version; the real ones are never written."""
    for rel in {rel for rel, _ in [*bump.PLACES, bump.UV_LOCK]}:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    return tmp_path


def snapshot(root):
    return {rel: (root / rel).read_bytes() for rel, _ in bump.PLACES}


def test_a_bump_writes_every_place(tree):
    bump.write("9.8.7", tree)
    versions = bump.found(tree)
    lock = [k for k in versions if k.startswith("uv.lock")]
    assert {v for k, v in versions.items() if k not in lock} == {"9.8.7"}
    # Only the version text changed, and both versions in a package-lock.json were set.
    assert (tree / "ui/package-lock.json").read_text(encoding="utf-8").count('"9.8.7"') >= 2
    assert bump.check(root=tree) == 1  # uv.lock still has the old one until `uv lock` runs


def test_a_missing_pattern_changes_nothing(tree):
    # The last place in the list loses its version: every earlier file was once
    # already rewritten by the time this was noticed.
    last = tree / bump.PLACES[-1][0]
    last.write_text("{}\n", encoding="utf-8")
    before = snapshot(tree)
    with pytest.raises(SystemExit) as stop:
        bump.write("9.8.7", tree)
    assert bump.PLACES[-1][0] in str(stop.value) and "nothing was changed" in str(stop.value)
    assert snapshot(tree) == before


def test_a_missing_file_changes_nothing(tree):
    (tree / bump.PLACES[5][0]).unlink()
    kept = [p for p in bump.PLACES if p[0] != bump.PLACES[5][0]]
    before = {rel: (tree / rel).read_bytes() for rel, _ in kept}
    with pytest.raises(SystemExit):
        bump.write("9.8.7", tree)
    assert {rel: (tree / rel).read_bytes() for rel, _ in kept} == before


def test_check_covers_uv_lock_and_the_tag(tree, capsys):
    current = bump.found(tree)["core/pyproject.toml (0)"]
    assert bump.check(root=tree) == 0
    assert bump.check(tag=f"v{current}", root=tree) == 0
    assert bump.check(tag="v0.0.0", root=tree) == 1
    assert bump.check(tag=current, root=tree) == 1  # a tag is vX.Y.Z
    lock = tree / "uv.lock"
    text = lock.read_text(encoding="utf-8")
    stale = text.replace(
        f'name = "irisecho-core"\nversion = "{current}"',
        'name = "irisecho-core"\nversion = "0.0.1"',
    )
    assert stale != text
    lock.write_text(stale, encoding="utf-8", newline="\n")
    assert bump.check(root=tree) == 1
    lock.unlink()
    assert bump.check(root=tree) == 1
    assert "uv.lock" in capsys.readouterr().out


def test_the_real_tree_agrees():
    assert bump.check() == 0


# --- the core's pinned dependencies in the installer (P1-07) ---------------------


def test_the_exported_constraints_pin_every_runtime_dependency(tmp_path):
    dest = tmp_path / build.CONSTRAINTS
    pins = build.export_constraints(dest)
    text = dest.read_text(encoding="utf-8")

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked: dict[str, set[str]] = {}
    for package in lock["package"]:
        locked.setdefault(canonical(package["name"]), set()).add(package.get("version"))
    core = next(p for p in lock["package"] if p["name"] == "irisecho-core")

    # Everything the core depends on, directly or through another package, on any platform.
    # An extra asked for on the way (httpx[socks]) brings that extra's packages too.
    by_name: dict[str, list[dict]] = {}
    for package in lock["package"]:
        by_name.setdefault(canonical(package["name"]), []).append(package)
    todo = list(core["dependencies"])
    needed, done = set(), set()
    while todo:
        dep = todo.pop()
        key = (canonical(dep["name"]), tuple(dep.get("extra", ())))
        if key in done:
            continue
        done.add(key)
        needed.add(key[0])
        for package in by_name[key[0]]:
            todo += package.get("dependencies", [])
            for extra in key[1]:
                todo += package.get("optional-dependencies", {}).get(extra, [])
    assert len(needed) > 20
    assert needed == set(pins), needed ^ set(pins)
    for name, versions in pins.items():
        assert set(versions) == locked[name], name

    # What pyproject.toml asks for by name is all there.
    project = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8"))
    for spec in project["project"]["dependencies"]:
        assert canonical(re.match(r"[A-Za-z0-9._-]+", spec).group(0)) in pins, spec

    # Exact versions only, each with the hashes uv checks a download against;
    # no dev tools, not the core itself, and nothing that says where it was built.
    requirements = [x for x in text.splitlines() if x and not x.startswith((" ", "#"))]
    assert all(build.PINNED.match(x) for x in requirements)
    assert text.count("--hash=sha256:") >= len(requirements)
    assert not {"pytest", "ruff", "irisecho-core"} & set(pins)
    assert str(tmp_path) not in text and "-e " not in text


def test_the_shell_installs_with_the_bundled_constraints():
    """main.rs and build_desktop.py agree on the file, and the install names it."""
    shell = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert f'dir.join("{build.CONSTRAINTS}")' in shell
    assert '"--constraint"' in shell
    # The only `pip install` the shell runs is the one built by install_args.
    assert shell.count('"pip", "install"') == 2  # install_args and its test
    assert "install_args(&python, &wheel, &constraints)" in shell


# --- workflows (P1-17, P2-42, P2-43) ------------------------------------------------


def workflows():
    return {f.name: yaml.safe_load(f.read_text(encoding="utf-8")) for f in WORKFLOWS}


def steps(workflow):
    for job in workflow["jobs"].values():
        yield from job.get("steps", [])


def test_every_action_is_pinned_to_a_commit():
    pinned = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
    seen = 0
    for name, workflow in workflows().items():
        for job_id, job in workflow["jobs"].items():
            uses = [s["uses"] for s in job.get("steps", []) if "uses" in s]
            if "uses" in job:  # a reusable workflow of this repository
                assert job["uses"].startswith("./.github/workflows/"), (name, job_id)
            for ref in uses:
                seen += 1
                assert pinned.match(ref), f"{name}: {ref} is not pinned to a full commit"
    assert seen >= 10
    # The version each commit stands for is written beside it.
    for f in WORKFLOWS:
        for line in f.read_text(encoding="utf-8").splitlines():
            if re.search(r"uses: .+@[0-9a-f]{40}", line):
                assert re.search(r"@[0-9a-f]{40} # \S+", line), line


def test_only_the_publish_job_may_write():
    for name, workflow in workflows().items():
        assert workflow["permissions"] == {"contents": "read"}, name
        for job_id, job in workflow["jobs"].items():
            permissions = job.get("permissions", {"contents": "read"})
            if (name, job_id) == ("release.yml", "publish"):
                assert permissions == {"contents": "write"}
            else:
                assert permissions == {"contents": "read"}, (name, job_id)
    publish = workflows()["release.yml"]["jobs"]["publish"]
    # The job with the token builds nothing: no checkout, no npm, cargo or uv.
    text = yaml.safe_dump(publish)
    assert not re.search(r"checkout|npm|cargo|uv |setup-", text)


def test_the_release_checks_builds_then_publishes_a_draft():
    release = workflows()["release.yml"]
    jobs = release["jobs"]
    assert release[True] == {"push": {"tags": ["v*"]}}  # yaml reads the key `on` as True
    assert jobs["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["build"]["needs"] == "checks" and jobs["publish"]["needs"] == "build"
    runs = [s.get("run", "") for s in jobs["build"]["steps"]]
    tag_check = next(i for i, r in enumerate(runs) if "bump_version.py --check --tag" in r)
    assert tag_check < next(i for i, r in enumerate(runs) if "build_desktop.py" in r)
    publish = jobs["publish"]["steps"]
    assert any("sha256sum" in s.get("run", "") and "SHA256SUMS" in s["run"] for s in publish)
    release_step = publish[-1]
    assert "action-gh-release" in release_step["uses"]
    assert release_step["with"]["draft"] is True
    assert "SHA256SUMS" in release_step["with"]["files"] and ".exe" in release_step["with"]["files"]


def test_ci_runs_everywhere_and_covers_the_shell():
    ci = workflows()["ci.yml"]
    on = ci[True]
    assert on["push"]["branches"] == ["**"] and "pull_request" in on and "workflow_call" in on
    shell = ci["jobs"]["shell"]
    assert shell["runs-on"] == "windows-latest"
    runs = [s.get("run", "") for s in shell["steps"]]
    assert "cargo check --locked" in runs and "cargo test --locked" in runs
    scans = [s["run"] for s in steps(ci) if "identity_scan.py" in s.get("run", "")]
    assert len(scans) == 2 and all("$SCAN_FLAGS" in run for run in scans)
    assert any("--commit-msg" in run and "--full-message" in run for run in scans)
    load_step = next(s for s in steps(ci) if s.get("name") == "Load private denylist")
    assert "--require-denylist" in load_step["run"]
