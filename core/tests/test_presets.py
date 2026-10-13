# SPDX-License-Identifier: AGPL-3.0-or-later
import json

import pytest

from irisecho_core import presets, registry
from irisecho_core.controls import ControlError


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_presets_are_saved_checked_and_deleted(home):
    flux = registry.default().models["flux-dev"]
    data = presets.put(flux, " Soft light ", {"guidance": "2.5", "steps": 30})
    assert data == {"flux-dev": {"Soft light": {"guidance": 2.5, "steps": 30}}}
    assert json.loads((home / "presets.json").read_text(encoding="utf-8")) == data
    assert presets.delete("flux-dev", "Soft light") == {}
    assert presets.load() == {}


def test_presets_refuse_what_the_model_cannot_take():
    flux = registry.default().models["flux-dev"]
    with pytest.raises(ControlError, match="between 1 and 6"):
        presets.put(flux, "loud", {"guidance": 40})
    with pytest.raises(ControlError, match="no control named prompt"):
        presets.put(flux, "p", {"prompt": "a fox"})
    with pytest.raises(ControlError, match="name"):
        presets.put(flux, "  ", {"steps": 20})


def test_a_broken_presets_file_reads_as_empty(home):
    (home / "presets.json").write_text("{nope", encoding="utf-8")
    assert presets.load() == {}
