# SPDX-License-Identifier: AGPL-3.0-or-later
"""Music and sound jobs that start from a recording (cover, repaint, extend, variation, replace).

A job without a recording sends its worker exactly what it always sent (the parity tests in
test_audio_controls.py cover that too); a job with one adds only the keys checked here.
"""

import asyncio
import hashlib
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from irisecho_core import controls, paths, registry
from irisecho_core.controls import ControlError
from irisecho_core.engines import acestep, stableaudio
from irisecho_core.engines.base import RunContext

ENGINES = Path(__file__).parents[1] / "src/irisecho_core/engines"
sys.path.insert(0, str(ENGINES / "protocol"))

HW = SimpleNamespace(backend="cuda", cuda_tag="cu128", gpus=[], vram_mb=12000)
SRC = hashlib.md5(b"song").hexdigest() + ".wav"


class Files(dict):
    def __missing__(self, key):
        return Path("models") / key


def run(engine, model_id: str, params: dict, out_dir: Path, result: dict):
    sent: list[tuple[str, dict]] = []

    async def fake(ctx, op, request):
        sent.append((op, request))
        return result

    engine.call = fake
    ctx = RunContext(
        job_id="j",
        params=params,
        model=registry.default().models[model_id],
        variant=None,
        files=Files(),
        specs={},
        out_dir=out_dir,
        stem="s",
        report=lambda *a: None,
        category_dirs=lambda c: [],
    )
    asyncio.run(engine.run(ctx))
    return sent


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    (paths.sub("uploads") / SRC).write_bytes(b"x")
    monkeypatch.setattr(acestep, "group_root", lambda ctx, group: Path("root") / group)
    monkeypatch.setattr(stableaudio, "group_root", lambda ctx, group: Path("root") / group)
    return tmp_path


def load_worker(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_worker", ENGINES / name / "worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TAKE = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}


def ace(home, **params):
    base = {"prompt": "synthwave"}
    return run(acestep.AceStepEngine(HW), "ace-step", {**base, **params}, home, TAKE)


# --- ACE-Step -------------------------------------------------------------------------------------


def test_acestep_without_a_recording_sends_nothing_new(home):
    sent = ace(home, region=[1, 2], extend_by=5, cover_strength=None)
    assert sent[1][1].keys() == {
        "caption",
        "lyrics",
        "duration",
        "bpm",
        "seed",
        "thinking",
        "loop",
        "outs",
    }


def test_acestep_cover(home):
    sent = ace(home, source=SRC, task="cover", cover_strength=0.2, cover_noise=0.15)
    assert sent[0][1]["thinking"] is False  # the planner is not loaded for a cover
    request = sent[1][1]
    assert request["thinking"] is False
    assert request["task"] == "cover"
    assert request["source"] == str(paths.sub("uploads") / SRC)
    assert request["cover_strength"] == 0.2
    assert request["cover_noise"] == 0.15
    assert "region" not in request and "extend_by" not in request


def test_acestep_cover_sends_only_the_strengths_that_were_set(home):
    request = ace(home, source=SRC, task="cover")[1][1]
    assert "cover_strength" not in request and "cover_noise" not in request


def test_acestep_cover_strengths_are_held_to_the_registry_range(home):
    request = ace(home, source=SRC, task="cover", cover_strength=3, cover_noise=-1)[1][1]
    assert (request["cover_strength"], request["cover_noise"]) == (1.0, 0.0)


def test_acestep_repaint(home):
    request = ace(home, source=SRC, task="repaint", region=[12.5, 20], cover_strength=0.3)[1][1]
    assert request["task"] == "repaint"
    assert request["region"] == [12.5, 20.0]
    assert "cover_strength" not in request  # only a cover uses it


def test_acestep_extend(home):
    request = ace(home, source=SRC, task="extend", extend_by=30)[1][1]
    assert request["task"] == "extend"
    assert request["extend_by"] == 30.0
    assert "region" not in request


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"task": "cover"}, "Add the recording"),
        ({"source": SRC}, "Choose what to do"),
        ({"source": SRC, "task": "remix"}, "Unknown task"),
        ({"source": "../x.wav", "task": "cover"}, "Add a recording"),
        ({"source": "0" * 32 + ".wav", "task": "cover"}, "no longer available"),
        ({"source": "0" * 32 + ".png", "task": "cover"}, "WAV, MP3, FLAC or OGG"),
        ({"source": SRC, "task": "repaint"}, "part to repaint"),
        ({"source": SRC, "task": "repaint", "region": [5]}, "start and an end"),
        ({"source": SRC, "task": "repaint", "region": [-1, 4]}, "before the beginning"),
        ({"source": SRC, "task": "repaint", "region": [8, 8]}, "after its start"),
        ({"source": SRC, "task": "repaint", "region": [8, 4]}, "after its start"),
        ({"source": SRC, "task": "repaint", "region": [1, 900]}, "past 600"),
        ({"source": SRC, "task": "repaint", "region": ["a", 4]}, "number of seconds"),
        ({"source": SRC, "task": "extend"}, "how many seconds"),
        ({"source": SRC, "task": "extend", "extend_by": 0.5}, "between 1 and 240"),
        ({"source": SRC, "task": "extend", "extend_by": 500}, "between 1 and 240"),
    ],
)
def test_acestep_refuses_bad_source_jobs(home, params, message):
    with pytest.raises(ValueError, match=message):
        ace(home, **params)


def test_acestep_worker_builds_the_upstream_task_params(monkeypatch):
    worker = load_worker("acestep")
    assert worker.source_task({}) == {}
    assert worker.source_task({"task": None}) == {}
    cover = worker.source_task(
        {"task": "cover", "source": "s.wav", "cover_strength": 0.2, "cover_noise": 0.1}
    )
    assert cover == {
        "src_audio": "s.wav",
        "task_type": "cover",
        "audio_cover_strength": 0.2,
        "cover_noise_strength": 0.1,
    }
    assert worker.source_task({"task": "cover", "source": "s.wav"}) == {
        "src_audio": "s.wav",
        "task_type": "cover",
    }
    assert worker.source_task({"task": "repaint", "source": "s.wav", "region": [3, 9]}) == {
        "src_audio": "s.wav",
        "task_type": "repaint",
        "repainting_start": 3.0,
        "repainting_end": 9.0,
    }
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(info=lambda p: SimpleNamespace(duration=42.5)),
    )
    extend = worker.source_task({"task": "extend", "source": "s.wav", "extend_by": 20})
    assert extend == {
        "src_audio": "s.wav",
        "task_type": "repaint",  # a repaint that starts where the recording ends
        "repainting_start": 42.5,
        "repainting_end": 62.5,
    }
    with pytest.raises(ValueError, match="600"):
        worker.source_task({"task": "extend", "source": "s.wav", "extend_by": 600})


def test_acestep_cover_controls_need_a_recording_and_stay_in_range():
    model = registry.default().models["ace-step"]
    for name, default in (("cover_strength", 1.0), ("cover_noise", 0.0)):
        c = model.control(name)
        assert c.needs == "source" and c.default == default and (c.min, c.max) == (0, 1)
    assert controls.check(model, {"cover_strength": 0.5, "source": SRC, "task": "cover"})[1] == []
    with pytest.raises(ControlError):
        controls.check(model, {"cover_strength": 1.5})
    _, warnings = controls.check(
        model, {"source": SRC, "task": "extend", "region": [1, 2], "extend_by": 3}
    )
    assert warnings == []


# --- Stable Audio ---------------------------------------------------------------------------------


def sfx(home, **params):
    base = {"prompt": "door"}
    return run(stableaudio.StableAudioEngine(HW), "sa3-sfx", {**base, **params}, home, TAKE)


def test_stable_audio_variation(home):
    request = sfx(home, init_audio=SRC, init_strength=0.3)[1][1]
    assert request["init_audio"] == str(paths.sub("uploads") / SRC)
    assert request["init_strength"] == 0.3
    assert "region" not in request


def test_stable_audio_variation_defaults_to_half_and_is_held_to_the_range(home):
    assert sfx(home, init_audio=SRC)[1][1]["init_strength"] == 0.5
    assert sfx(home, init_audio=SRC, init_strength=7)[1][1]["init_strength"] == 1.0


def test_stable_audio_replace_a_part(home):
    request = sfx(home, init_audio=SRC, region=[0.5, 1.25], init_strength=0.2)[1][1]
    assert request["region"] == [0.5, 1.25]
    assert "init_strength" not in request  # a replaced part is made from the description


def test_stable_audio_without_a_sound_sends_nothing_new(home):
    request = sfx(home, init_strength=0.2)[1][1]
    assert "init_audio" not in request and "init_strength" not in request


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"region": [1, 2]}, "Add the sound"),
        ({"init_audio": "nope"}, "Add a sound"),
        ({"init_audio": "0" * 32 + ".wav"}, "no longer available"),
        ({"init_audio": SRC, "region": [2, 1]}, "after its start"),
        ({"init_audio": SRC, "region": [0, 31]}, "past 30"),
        ({"init_audio": SRC, "region": [1]}, "start and an end"),
    ],
)
def test_stable_audio_refuses_bad_source_jobs(home, params, message):
    with pytest.raises(ValueError, match=message):
        sfx(home, **params)


def test_stable_audio_worker_builds_the_upstream_arguments(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))  # not in this environment
    worker = load_worker("stableaudio")
    assert worker.starting_sound({"prompt": "x"}, 3.0) == ({}, 3.0)
    data = [[0.0]] * 22050  # half a second at 44.1 kHz
    monkeypatch.setitem(
        sys.modules, "soundfile", types.SimpleNamespace(read=lambda p, **kw: (data, 44100))
    )
    args, seconds = worker.starting_sound({"init_audio": "s.wav", "init_strength": 0.4}, 3.0)
    assert args == {"init_audio": (44100, data), "init_noise_level": 0.4}
    assert seconds == 3.0
    args, seconds = worker.starting_sound(
        {"init_audio": "s.wav", "init_strength": 0.4, "region": [1, 5]}, 3.0
    )
    assert args == {
        "inpaint_audio": (44100, data),
        "inpaint_mask_start_seconds": 1.0,
        "inpaint_mask_end_seconds": 5.0,
    }
    assert seconds == 5.0  # long enough to hold the new part
    long = [[0.0]] * (44100 * 40)
    monkeypatch.setitem(
        sys.modules, "soundfile", types.SimpleNamespace(read=lambda p, **kw: (long, 44100))
    )
    assert worker.starting_sound({"init_audio": "s.wav", "init_strength": 1.0}, 3.0)[1] == 30.0


def test_stable_audio_strength_control_needs_a_sound():
    c = registry.default().models["sa3-sfx"].control("init_strength")
    assert c.needs == "init_audio" and (c.min, c.max) == (0, 1)
