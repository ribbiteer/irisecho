# SPDX-License-Identifier: AGPL-3.0-or-later
"""An engine whose install stopped halfway must install cleanly on the next try."""

import asyncio

from irisecho_core.engines import uvenv


def test_a_leftover_environment_is_cleared_before_a_new_one(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    (venv / "Lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = somewhere\n", encoding="utf-8")
    seen = []

    async def fake_run(args, log, cwd=None, env=None):
        # uv is asked to create the folder only once nothing is left there.
        seen.append((args[1], venv.exists()))

    monkeypatch.setattr(uvenv, "run", fake_run)
    monkeypatch.setattr(uvenv, "uv_bin", lambda: "uv")
    said = []
    asyncio.run(uvenv.create_venv(venv, "3.12", said.append))
    assert seen == [("venv", False)]
    assert any("earlier, unfinished install" in line for line in said)


def test_a_first_install_says_nothing_about_leftovers(tmp_path, monkeypatch):
    async def fake_run(args, log, cwd=None, env=None):
        pass

    monkeypatch.setattr(uvenv, "run", fake_run)
    monkeypatch.setattr(uvenv, "uv_bin", lambda: "uv")
    said = []
    asyncio.run(uvenv.create_venv(tmp_path / ".venv", "3.12", said.append))
    assert said == []


def test_every_engine_builds_its_environment_through_the_shared_helper():
    """No engine calls `uv venv` itself, so none can miss the leftover clean-up."""
    from pathlib import Path

    engines = Path(uvenv.__file__).parent
    for source in engines.glob("*/__init__.py"):
        text = source.read_text(encoding="utf-8")
        assert '"venv"' not in text, f"{source.parent.name} runs uv venv directly"


def test_an_install_log_is_kept_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    from irisecho_core.app import App

    app = App()
    engine = app.engines["kokoro"]
    engine.installed = lambda: False

    async def failing_install(log):
        log("Downloading something")
        raise RuntimeError("the network went away")

    engine.install = failing_install

    async def go():
        app.bus.bind(asyncio.get_running_loop())
        app.install_engine("kokoro")
        await app.install_tasks["kokoro"]

    asyncio.run(go())
    text = (tmp_path / "logs" / "install-kokoro.log").read_text(encoding="utf-8")
    assert "installing Kokoro" in text and "Downloading something" in text
    assert "the network went away" in text
    assert engine.state == "error"


PIP_ENGINES = ["kokoro", "chatterbox", "promptwriter", "comfy"]


def test_engines_installed_with_pip_have_recorded_versions():
    """Every engine that resolves packages itself installs against recorded versions."""
    import re
    from pathlib import Path

    engines = Path(uvenv.__file__).parent
    pin = re.compile(r"^[a-z0-9][a-z0-9._-]*==[0-9][^\s+;]*$")
    for name in PIP_ENGINES:
        lines = (engines / name / "constraints.txt").read_text(encoding="utf-8").splitlines()
        pins = [line for line in lines if line and not line.startswith("#")]
        assert len(pins) > 10, name
        assert all(pin.match(line) for line in pins), [x for x in pins if not pin.match(x)]
        source = (engines / name / "__init__.py").read_text(encoding="utf-8")
        assert "constraints=self.constraints" in source, f"{name} does not use its pins"


def _install_calls():
    """Every place under engines/ that can make uv install packages.

    Yields (file, kind, node): `pip_install(...)` calls, list literals that hold
    uv sub-command words, and bare `uv_bin()` calls. Found by reading the source,
    so a new engine or a new call is covered without being added to a list here.
    """
    import ast
    from pathlib import Path

    engines = Path(uvenv.__file__).parent
    for source in sorted(engines.rglob("*.py")):
        if source == Path(uvenv.__file__):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name in ("pip_install", "uv_bin"):
                    yield source, name, node
            elif isinstance(node, ast.List):
                yield source, "list", node


def _words(node) -> list[str]:
    import ast

    return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def test_no_engine_install_can_resolve_a_package_freely():
    """Each pip call names its recorded versions, and each sync follows a lock.

    The one allowed exception installs exact versions with --no-deps, which
    resolves nothing.
    """
    import ast
    import re

    exact = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[a-z,]+\])?==[0-9][^\s,;<>=*]*$")
    pip_calls = uv_calls = uv_lists = 0
    for source, kind, node in _install_calls():
        where = f"{source.parent.name}/{source.name}:{node.lineno}"
        if kind == "pip_install":
            pip_calls += 1
            keywords = {k.arg: ast.unparse(k.value) for k in node.keywords}
            if keywords.get("constraints") == "self.constraints":
                recorded = source.parent / "constraints.txt"
                assert recorded.is_file(), f"{where} has no constraints.txt to install against"
                continue
            assert "constraints" not in keywords, f"{where} passes something else as constraints"
            flags = [a.value for a in node.args[3:] if isinstance(a, ast.Constant)]
            packages = ast.literal_eval(node.args[1])
            assert "--no-deps" in flags, f"{where} installs without recorded versions"
            assert all(exact.match(p) for p in packages), f"{where} installs an open version"
        elif kind == "uv_bin":
            uv_calls += 1
        else:
            words = _words(node)
            starts_uv = bool(node.elts) and ast.unparse(node.elts[0]).endswith("uv_bin()")
            if starts_uv:
                uv_lists += 1
                assert words[:1] == ["sync"], f"{where} runs uv {words[:1]} outside the helpers"
            assert "pip" not in words, f"{where} runs pip itself; use uvenv.pip_install"
            if starts_uv or "sync" in words:
                locked = "--frozen" in words or "--locked" in words
                assert locked, f"{where} syncs without holding to the project's lock"
    # uv is only ever started from a command list that was checked above.
    assert uv_calls == uv_lists
    assert pip_calls >= 7 and uv_lists >= 2  # the scan really found the call sites


def test_every_recorded_versions_file_holds_exact_versions():
    import re
    from pathlib import Path

    engines = Path(uvenv.__file__).parent
    pin = re.compile(r"^[a-z0-9][a-z0-9._-]*==[0-9][^\s+;]*$")
    files = sorted(engines.glob("*/constraints.txt"))
    assert {f.parent.name for f in files} >= {*PIP_ENGINES, "acestep", "stableaudio"}
    for f in files:
        lines = f.read_text(encoding="utf-8").splitlines()
        pins = [line for line in lines if line and not line.startswith("#")]
        assert pins and all(pin.match(line) for line in pins), f.parent.name


def test_the_pip_call_beside_an_upstream_lock_is_fully_recorded():
    """ACE-Step and Stable Audio sync from upstream's uv.lock, then make one pip call.

    Their constraints hold what that call names and what it depends on.
    """
    from pathlib import Path

    from irisecho_core.engines import stableaudio

    engines = Path(uvenv.__file__).parent

    def pinned(name):
        lines = (engines / name / "constraints.txt").read_text(encoding="utf-8").splitlines()
        return dict(line.split("==") for line in lines if "==" in line and line[0] != "#")

    ace = pinned("acestep")
    assert {"soundfile", "cffi", "numpy"} <= set(ace)
    assert tuple(int(x) for x in ace["soundfile"].split(".")[:2]) >= (0, 13)
    sa = pinned("stableaudio")
    for spec in stableaudio.TORCH:  # the versions the engine asks for are the recorded ones
        name, version = spec.split("==")
        assert sa[name] == version
    assert {"filelock", "sympy", "networkx", "jinja2", "fsspec", "typing-extensions"} <= set(sa)


def test_the_two_locked_engines_pass_their_recorded_versions(tmp_path, monkeypatch):
    """Run both installs with the commands captured: the pip call carries --constraint."""
    import dataclasses

    from irisecho_core import hardware
    from irisecho_core.engines.acestep import AceStepEngine
    from irisecho_core.engines.stableaudio import StableAudioEngine

    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path))
    seen = []

    async def fake_run(args, log, cwd=None, env=None):
        seen.append(args)

    async def fake_fetch(repo, sha, dest, log, timeout=180):
        dest.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(uvenv, "run", fake_run)
    monkeypatch.setattr(uvenv, "fetch_source", fake_fetch)
    monkeypatch.setattr(uvenv, "uv_bin", lambda: "uv")
    hw = dataclasses.replace(hardware.detect(""), backend="cuda", cuda_tag="cu128")
    for cls in (AceStepEngine, StableAudioEngine):
        seen.clear()
        engine = cls(hw)
        asyncio.run(engine.install(lambda line: None))
        sync, pip = seen
        assert sync[1] == "sync" and "--frozen" in sync
        assert pip[1:3] == ["pip", "install"]
        assert pip[pip.index("--constraint") + 1] == str(engine.constraints)
        assert engine.constraints.is_file()


def test_pip_install_passes_the_recorded_versions(tmp_path, monkeypatch):
    seen = []

    async def fake_run(args, log, cwd=None, env=None):
        seen.append(args)

    monkeypatch.setattr(uvenv, "run", fake_run)
    monkeypatch.setattr(uvenv, "uv_bin", lambda: "uv")
    pins = tmp_path / "constraints.txt"
    asyncio.run(uvenv.pip_install(tmp_path, ["kokoro==0.9.4"], print, constraints=pins))
    asyncio.run(uvenv.pip_install(tmp_path, ["kokoro==0.9.4"], print))
    assert seen[0][seen[0].index("--constraint") + 1] == str(pins)
    assert "--constraint" not in seen[1]
