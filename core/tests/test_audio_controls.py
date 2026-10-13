# SPDX-License-Identifier: AGPL-3.0-or-later
"""Advanced controls of the four audio studios.

A job that sets none of them must send its worker exactly the request it sent before
controls existed (the `BEFORE` shapes below are copied from the engines as they were);
a job that sets one adds only that one thing.
"""

import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from irisecho_core import controls, paths, registry
from irisecho_core.controls import ControlError
from irisecho_core.engines import acestep, audioformat, chatterbox, kokoro, stableaudio
from irisecho_core.engines.base import RunContext

PROTOCOL = Path(__file__).parents[1] / "src/irisecho_core/engines/protocol"
sys.path.insert(0, str(PROTOCOL))
import irisecho_audio as au  # noqa: E402

HW = SimpleNamespace(backend="cuda", cuda_tag="cu128", gpus=[], vram_mb=12000)
REF = hashlib.md5(b"ref").hexdigest() + ".wav"


class Files(dict):
    """Every file id the engine asks for exists."""

    def __missing__(self, key):
        return Path("models") / key


def run(engine, model_id: str, params: dict, out_dir: Path, result: dict):
    """Run the engine's job and return the (op, request) pairs it sent its worker."""
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
    (paths.sub("uploads") / REF).write_bytes(b"x")
    monkeypatch.setattr(chatterbox, "group_root", lambda ctx, group: Path("root") / group)
    monkeypatch.setattr(acestep, "group_root", lambda ctx, group: Path("root") / group)
    monkeypatch.setattr(stableaudio, "group_root", lambda ctx, group: Path("root") / group)
    return tmp_path


# --- parity: nothing set, nothing new in the request ------------------------------------------


def test_kokoro_request_is_unchanged_without_controls(home):
    sent = run(kokoro.KokoroEngine(HW), "kokoro", {"text": "Hello"}, home, {"duration": 1})
    assert sent[1] == (
        "speak",
        {
            "text": "Hello",
            "voice": str(Path("models") / "kokoro-voices/af_heart.pt"),
            "lang": "a",
            "speed": 1.0,
            "clean": True,
            "out": str(home / "s.wav"),
        },
    )


def test_chatterbox_requests_are_unchanged_without_controls(home):
    take = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}
    for mid, variant in (("chatterbox", "classic"), ("chatterbox-turbo", "turbo")):
        params = {"text": "Hi", "ref": REF, "consent": True, "takes": 2, "clean": True}
        sent = run(chatterbox.ChatterboxEngine(HW), mid, params, home, take)
        assert sent[1] == (
            "speak",
            {
                "text": "Hi",
                "ref": str(paths.sub("uploads") / REF),
                "variant": variant,
                "exaggeration": 0.5,
                "cfg": 0.5,
                "seed": None,
                "clean": True,
                "outs": [str(home / "s.wav"), str(home / "s-2.wav")],
            },
        )


def test_acestep_request_is_unchanged_without_controls(home):
    take = {"takes": [{"path": "a", "duration": 1}]}
    params = {"prompt": "synthwave", "takes": 2, "bpm": 100, "loop": True}
    sent = run(acestep.AceStepEngine(HW), "ace-step", params, home, take)
    assert sent[1] == (
        "compose",
        {
            "caption": "synthwave",
            "lyrics": "",
            "duration": 30.0,
            "bpm": 100,
            "seed": None,
            "thinking": True,
            "loop": True,
            "outs": [str(home / "s.wav"), str(home / "s-2.wav")],
        },
    )


def test_stable_audio_request_is_unchanged_without_controls(home):
    take = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}
    sent = run(stableaudio.StableAudioEngine(HW), "sa3-sfx", {"prompt": "door"}, home, take)
    assert sent[1] == (
        "generate",
        {
            "prompt": "TrackType: SFX, door",
            "seconds": 3.0,
            "seed": None,
            "outs": [str(home / "s.wav"), str(home / "s-2.wav"), str(home / "s-3.wav")],
            "trim": True,
        },
    )


def test_the_default_value_of_every_control_changes_nothing():
    """Sending a control's recommended value explicitly is the same as leaving it out."""
    for mid in ("kokoro", "chatterbox", "chatterbox-turbo", "ace-step", "sa3-sfx"):
        m = registry.default().models[mid]
        assert m.controls, mid
        params = {c.name: c.default for c in m.controls}
        out, _ = controls.check(m, params)
        assert set(out) == set(params)


# --- the controls themselves ---------------------------------------------------------------------


def test_kokoro_blend_replaces_the_voice(home):
    params = {"text": "Hi", "voice": "am_adam", "blend": "Bella:0.6, sky:0.4"}
    sent = run(kokoro.KokoroEngine(HW), "kokoro", params, home, {"duration": 1})
    request = sent[1][1]
    assert "voice" not in request
    assert request["voices"] == [
        [str(Path("models") / "kokoro-voices/af_bella.pt"), 0.6],
        [str(Path("models") / "kokoro-voices/af_sky.pt"), 0.4],
    ]
    assert request["lang"] == "a"


def test_kokoro_blend_without_weights_is_equal_and_a_british_first_voice_sets_the_language(home):
    sent = run(
        kokoro.KokoroEngine(HW),
        "kokoro",
        {"text": "Hi", "blend": "bf_emma,bf_alice"},
        home,
        {"duration": 1},
    )
    assert [w for _, w in sent[1][1]["voices"]] == [1.0, 1.0]
    assert sent[1][1]["lang"] == "b"


@pytest.mark.parametrize(
    "text, message",
    [
        ("af_bella:0.5,nobody", "unknown voice"),
        ("af_bella:0", "above zero"),
        ("af_bella:-1", "above zero"),
        ("af_bella:lots", "not a weight"),
        ("af_bella:nan", "above zero"),
        (" , ", "at least one voice"),
        ("af_bella,af_sky,af_nova,af_heart,af_kore,af_alloy", "at most"),
    ],
)
def test_kokoro_blend_is_checked(text, message):
    with pytest.raises(ValueError, match=message):
        kokoro.parse_blend(text)


def test_kokoro_blend_takes_ids_or_listed_names():
    assert kokoro.parse_blend("af_bella, Sky") == kokoro.parse_blend("Bella,af_sky")


def test_kokoro_blend_adds_a_repeated_voice():
    assert kokoro.parse_blend("af_bella:1, af_sky:1, af_bella:1") == [
        ("af_bella", 2.0),
        ("af_sky", 1.0),
    ]


def test_chatterbox_sampling_goes_to_the_worker_per_model(home):
    take = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}
    base = {"text": "Hi", "ref": REF, "consent": True}
    loud = {"temperature": 1.1, "top_p": 0.9, "min_p": 0.1, "top_k": 50, "repetition_penalty": 1.4}
    e = chatterbox.ChatterboxEngine(HW)
    classic = run(e, "chatterbox", {**base, **loud}, home, take)[1][1]
    assert classic["sampling"] == {
        "temperature": 1.1,
        "top_p": 0.9,
        "min_p": 0.1,
        "repetition_penalty": 1.4,
    }
    turbo = run(e, "chatterbox-turbo", {**base, **loud}, home, take)[1][1]
    assert turbo["sampling"] == {
        "temperature": 1.1,
        "top_p": 0.9,
        "top_k": 50,
        "repetition_penalty": 1.4,
    }
    assert turbo["exaggeration"] == 0.5  # unchanged: turbo ignores it, the worker passes none


def test_chatterbox_sampling_is_held_to_the_registry_range(home):
    take = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}
    params = {"text": "Hi", "ref": REF, "consent": True, "temperature": 99, "top_k": -4}
    sent = run(chatterbox.ChatterboxEngine(HW), "chatterbox-turbo", params, home, take)
    assert sent[1][1]["sampling"] == {"temperature": 2.0, "top_k": 0}


def test_chatterbox_exaggeration_up_to_two_is_passed_on(home):
    take = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}
    params = {"text": "Hi", "ref": REF, "consent": True, "exaggeration": 2.0}
    assert (
        run(chatterbox.ChatterboxEngine(HW), "chatterbox", params, home, take)[1][1]["exaggeration"]
        == 2.0
    )


def test_acestep_controls_reach_the_worker(home):
    take = {"takes": [{"path": "a", "duration": 1}]}
    params = {
        "prompt": "jazz",
        "key": "A minor",
        "time_signature": "3",
        "vocal_language": "ja",
        "steps": 12,
        "shift": 2.5,
        "lm_temperature": 0.5,
        "lm_top_p": 0.8,
        "raw": True,
        "format": "flac",
        "loop": True,
        "bpm": 90,
    }
    request = run(acestep.AceStepEngine(HW), "ace-step", params, home, take)[1][1]
    assert request["key"] == "A minor"
    assert request["time_signature"] == "3"
    assert request["vocal_language"] == "ja"
    assert request["steps"] == 12
    assert request["shift"] == 2.5
    assert request["lm_temperature"] == 0.5
    assert request["lm_top_p"] == 0.8
    assert request["raw"] is True
    assert request["format"] == "flac"
    assert request["outs"] == [str(home / "s.flac")]
    assert request["loop"] is True  # raw does not touch loops


def test_acestep_values_are_held_to_the_registry_range(home):
    take = {"takes": [{"path": "a", "duration": 1}]}
    params = {"prompt": "jazz", "steps": 500, "shift": 0, "lm_top_p": 3}
    request = run(acestep.AceStepEngine(HW), "ace-step", params, home, take)[1][1]
    assert (request["steps"], request["shift"], request["lm_top_p"]) == (20, 1.0, 1.0)


def test_stable_audio_controls_reach_the_worker(home):
    take = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}
    params = {
        "prompt": "door",
        "steps": 16,
        "cfg": 4,
        "negative": " music, speech ",
        "track_type": False,
        "format": "wav24",
    }
    request = run(stableaudio.StableAudioEngine(HW), "sa3-sfx", params, home, take)[1][1]
    assert request["prompt"] == "door"
    assert request["steps"] == 16
    assert request["cfg"] == 4.0
    assert request["negative"] == "music, speech"
    assert request["format"] == "wav24"
    assert request["outs"][0].endswith("s.wav")


def test_voice_output_format_sets_the_extension(home):
    sent = run(
        kokoro.KokoroEngine(HW), "kokoro", {"text": "Hi", "format": "flac"}, home, {"duration": 1}
    )
    assert sent[1][1]["out"] == str(home / "s.flac")
    assert sent[1][1]["format"] == "flac"


def test_unknown_audio_format_is_refused(home):
    with pytest.raises(ValueError, match="Unknown audio format"):
        run(kokoro.KokoroEngine(HW), "kokoro", {"text": "Hi", "format": "mp3"}, home, {})


# --- registry ------------------------------------------------------------------------------------


def spec(mid):
    return registry.default().models[mid]


def test_audio_controls_are_validated_by_the_registry():
    ace = spec("ace-step")
    out, warnings = controls.check(
        ace, {"prompt": "x", "key": "F# minor", "steps": "10", "time_signature": "6"}
    )
    assert out["steps"] == 10 and out["key"] == "F# minor" and warnings == []
    for params in (
        {"key": "H major"},
        {"steps": 21},
        {"shift": 5.5},
        {"time_signature": "5"},
        {"vocal_language": "xx"},
    ):
        with pytest.raises(ControlError):
            controls.check(ace, params)
    with pytest.raises(ControlError):
        controls.check(spec("chatterbox"), {"temperature": 6})
    with pytest.raises(ControlError):
        controls.check(spec("sa3-sfx"), {"cfg": 30})
    # A control of another model is reported, not applied.
    _, warnings = controls.check(spec("chatterbox-turbo"), {"min_p": 0.1})
    assert warnings


def test_acestep_keys_are_the_ones_the_planner_accepts():
    keys = [o for o in spec("ace-step").control("key").values() if o]
    assert len(keys) == 24
    valid = {f"{n}{a} {m}" for n in "ABCDEFG" for a in ("", "#", "b") for m in ("major", "minor")}
    assert set(keys) <= valid


def test_defaults_are_what_the_engines_run_today():
    ace, sfx = spec("ace-step"), spec("sa3-sfx")
    assert ace.control("steps").default == 8  # GenerationParams.inference_steps
    assert ace.control("shift").default == 3.0  # worker's shift=3.0
    assert ace.control("lm_temperature").default == 0.85
    assert ace.control("lm_top_p").default == 0.9
    assert ace.control("vocal_language").default == "unknown"
    assert sfx.control("steps").default == 8 and sfx.control("cfg").default == 1.0
    assert spec("chatterbox").control("top_p").default == 1.0
    assert spec("chatterbox").control("min_p").default == 0.05
    assert spec("chatterbox-turbo").control("top_p").default == 0.95
    assert spec("chatterbox-turbo").control("top_k").default == 1000
    for mid in ("chatterbox", "chatterbox-turbo"):
        assert spec(mid).control("temperature").default == 0.8
        assert spec(mid).control("repetition_penalty").default == 1.2


def test_every_audio_studio_offers_the_file_formats():
    for mid in ("kokoro", "chatterbox", "chatterbox-turbo", "ace-step", "sa3-sfx"):
        c = spec(mid).control("format")
        assert c.default == "wav16" and c.values() == ["wav16", "wav24", "flac"]


# --- file format ---------------------------------------------------------------------------------


def test_core_and_worker_agree_on_the_formats():
    assert {k: v[2] for k, v in au.AUDIO_FORMATS.items()} == audioformat.EXTENSIONS


def test_write_audio_picks_container_and_sample_format(monkeypatch):
    written = []
    fake = SimpleNamespace(write=lambda *a, **k: written.append((a, k)))
    monkeypatch.setitem(sys.modules, "soundfile", fake)
    audio = np.zeros(10, dtype=np.float32)
    au.write_audio("a.wav", audio, 24000)
    au.write_audio("b.wav", audio, 24000, "wav24")
    au.write_audio("c.flac", audio, 24000, "flac")
    assert [(k["format"], k["subtype"]) for _, k in written] == [
        ("WAV", "PCM_16"),
        ("WAV", "PCM_24"),
        ("FLAC", "PCM_24"),
    ]
    with pytest.raises(ValueError):
        au.write_audio("d.mp3", audio, 24000, "mp3")
