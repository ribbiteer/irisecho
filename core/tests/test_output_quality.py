# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in output quality: video encode quality, LTX sound level, trimming a song's silent
ending, and the song planner's own plan returned with the music. None of them changes a
job that does not ask (test_graph_parity holds the graphs)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from irisecho_core import controls, registry
from irisecho_core.engines import acestep
from irisecho_core.engines.comfy import graphs

PROTOCOL = Path(acestep.__file__).parent.parent / "protocol"
HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")
VIDEO = ["ltx25", "wan22-i2v", "wan22-i2v-1022", "wan22-t2v", "wan22-camera", "hunyuan15-i2v"]


def worker():
    sys.path.insert(0, str(PROTOCOL))
    spec = importlib.util.spec_from_file_location(
        "acestep_worker", Path(acestep.__file__).with_name("worker.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(mid, **params):
    m = registry.default().models[mid]
    names = {s.id: s.filename for s in registry.default().expand(m.variants[0].files)}
    images = {"start": "s.png", "video": "v.mp4"} if mid != "wan22-t2v" else {}
    g, save = graphs.BUILDERS[m.variants[0].workflow](
        prompt="waves on a dock",
        seed=1,
        aspect="16:9",
        names=names,
        settings=m.settings,
        hw=HW,
        prefix="x",
        images=images,
        params=params,
    )
    return g, save


# --- video quality ----------------------------------------------------------------------


@pytest.mark.parametrize("mid", [*VIDEO, "seedvr2-video"])
def test_every_video_model_offers_quality_with_todays_default(mid):
    c = registry.default().models[mid].control("video_quality")
    assert c.default == "standard" and [o for o in c.values()] == ["standard", "high", "max"]


def test_standard_quality_saves_as_before():
    assert graphs.video_save_inputs("p", {}) == {
        "video": ["video", 0],
        "filename_prefix": "p",
        "format": "auto",
        "codec": "auto",
    }


@pytest.mark.parametrize(("quality", "crf"), [("high", 16.0), ("max", 10.0)])
def test_higher_quality_reencodes_h264_at_a_lower_crf(quality, crf):
    g, save = build("ltx25", video_quality=quality, seconds=4)
    inputs = g[save]["inputs"]
    assert inputs["format"] == "mp4" and inputs["format.codec"] == "h264"
    assert inputs["format.codec.encoding"] == "re-encode"
    assert inputs["format.codec.encoding.crf"] == crf


# --- LTX sound level --------------------------------------------------------------------


def test_ltx_sound_is_levelled_only_when_asked():
    g, _ = build("ltx25", seconds=4)
    assert "level" not in g
    g, _ = build("ltx25", seconds=4, even_sound=True)
    assert g["level"]["inputs"] == {"audio": ["sound", 0], "lufs": -19.0}
    assert g["video"]["inputs"]["audio"] == ["level", 0]
    g, _ = build("ltx25", seconds=4, even_sound=True, sound=False)
    assert "level" not in g and "audio" not in g["video"]["inputs"]


def test_ltx_offers_even_sound():
    c = registry.default().models["ltx25"].control("even_sound")
    assert c.type == "bool" and c.default is False


# --- music ------------------------------------------------------------------------------


def test_trim_end_cuts_a_quiet_tail_and_leaves_full_songs_alone():
    trim_end = worker().trim_end
    sr = 48000
    song = np.zeros((sr * 20, 2), np.float32)
    song[: sr * 12] = 0.3 * np.sin(np.arange(sr * 12) / 10)[:, None]
    song[sr * 12 :] += (1e-4 * np.random.default_rng(1).standard_normal((sr * 8, 2))).astype(
        np.float32
    )
    out = trim_end(song, sr)
    assert abs(len(out) / sr - 12.5) < 0.06
    assert abs(out[-1]).max() < 1e-3  # faded out
    assert len(trim_end(song[: sr * 12], sr)) == sr * 12


def test_trim_end_is_a_music_control_and_reaches_the_worker():
    m = registry.default().models["ace-step"]
    assert m.control("trim_end").default is False
    assert acestep.compose_options(m, {"trim_end": True})["trim_end"] is True
    assert "trim_end" not in acestep.compose_options(m, {})
    controls.check(m, {"prompt": "jazz", "trim_end": True})


def test_the_planners_plan_comes_back_with_the_music():
    planned = worker().planned
    result = SimpleNamespace(
        extra_outputs={
            # What ACE-Step's planner returned for a 30 s instrumental (2026-10-15).
            "lm_metadata": {
                "bpm": 106,
                "caption": "A brief, atmospheric instrumental piece featuring a warm piano.",
                "duration": 30,
                "keyscale": "C major",
                "language": "unknown",
                "timesignature": "2",
                "genres": ["ambient"],
            }
        }
    )
    assert planned(result) == {
        "caption": "A brief, atmospheric instrumental piece featuring a warm piano.",
        "bpm": 106,
        "keyscale": "C major",
        "timesignature": "2",
        "duration": 30,
        "language": "unknown",
    }
    assert planned(SimpleNamespace(extra_outputs={})) == {}


def test_no_plan_when_the_planner_wrote_nothing():
    # bpm, key, time signature and duration all set: ACE-Step skips planning and only
    # echoes them back, which is not a plan.
    echo = {"bpm": 70, "keyscale": "D minor", "timesignature": "4", "duration": 30}
    assert worker().planned(SimpleNamespace(extra_outputs={"lm_metadata": echo})) == {}
