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
