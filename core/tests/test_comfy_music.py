# SPDX-License-Identifier: AGPL-3.0-or-later
"""Music in ComfyUI: YuE2, MiniMax Music 3 and Stable Audio 3 Medium, wired as ComfyUI's own
templates, with every take as its own seed and the file written the way the job asks."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from irisecho_core import controls, registry
from irisecho_core.engines.base import RunContext
from irisecho_core.engines.comfy import ComfyEngine, graphs

HW = SimpleNamespace(gpus=[SimpleNamespace(compute_cap=8.9)], vram_mb=12282, backend="cuda")
MUSIC = {"yue2": "yue2", "minimax-music3": "minimax-music3", "sa3-medium": "sa3-music"}


def model(mid: str):
    return registry.default().models[mid]


def build(mid: str, prompt="lo-fi hip hop, mellow Rhodes", **params):
    m = model(mid)
    names = {s.id: s.filename for s in registry.default().expand(m.variants[0].files)}
    g, save = graphs.BUILDERS[m.variants[0].workflow](
        prompt=prompt,
        seed=5,
        aspect="1:1",
        names=names,
        settings=m.settings,
        hw=HW,
        prefix="x",
        images={},
        params={"duration": 120, **params},
    )
    for nid, node in g.items():
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                assert value[0] in g, f"{nid}.{key} reads from missing node {value[0]}"
    return g, save


# --- registry -------------------------------------------------------------------------------------


def test_three_music_models_run_in_comfyui():
    reg = registry.default()
    for mid, workflow in MUSIC.items():
        m = reg.models[mid]
        assert (m.kind, m.engine) == ("music", "comfy")
        assert [v.when for v in m.variants] == [{"backend": "cuda"}]
        assert m.variants[0].workflow == workflow and workflow in graphs.BUILDERS
        assert workflow in graphs.MUSIC and workflow in graphs.DYNAMIC_VRAM
        assert {c.name for c in m.controls} == {"format", "trim_end", "steps"}
        for f in reg.expand(m.variants[0].files):
            assert f.pinned and f.repo.startswith("Comfy-Org/")


def test_licenses():
    reg = registry.default()
    lic = {mid: reg.licenses[reg.models[mid].license] for mid in MUSIC}
    assert lic["yue2"].commercial is False and lic["yue2"].accept
    assert lic["minimax-music3"].condition == "Free under $20M annual revenue"
    # ComfyUI's copy of Stable Audio 3 is not gated, unlike Stability's own repo.
    assert lic["sa3-medium"].accept and not lic["sa3-medium"].gated


def test_what_the_studio_offers_each_model():
    assert model("yue2").settings["music"] == {
        "lyrics": True,
        "planner": True,
        "source": False,
        "loop": True,
        "ends_by": 120,
    }
    assert model("minimax-music3").settings["music"]["ends_by"] == 150
    assert "ends_by" not in model("sa3-medium").settings["music"]  # makes the length asked
    assert model("minimax-music3").settings["music"]["planner"] is False
    assert model("sa3-medium").settings["music"]["lyrics"] is False
    assert "music" not in model("ace-step").settings  # ACE-Step keeps every option


def test_the_default_value_of_every_control_changes_nothing():
    for mid in MUSIC:
        m = model(mid)
        params = {c.name: c.default for c in m.controls}
        assert set(controls.check(m, params)[0]) == set(params)
        assert build(mid, **params) == build(mid)


# --- graphs ---------------------------------------------------------------------------------------


def test_yue2_plans_a_score_first_unless_planning_is_off():
    g, save = build("yue2")
    assert g["music"]["inputs"]["abc"] == ["abc", 0]
    assert (
        g["sample"]["inputs"].items()
        >= {"steps": 32, "cfg": 1.0, "sampler_name": "dpm_2", "scheduler": "sgm_uniform"}.items()
    )
    assert g["latent"]["inputs"]["seconds"] == ["music", 1]
    g, _ = build("yue2", thinking=False)
    assert "abc" not in g and g["music"]["inputs"]["abc"] == ""
    assert g[save]["class_type"] == "IrisEchoSaveMusic"


def test_instrumentals_get_section_tags_sized_to_the_length():
    lyrics = build("yue2")[0]["music"]["inputs"]["lyrics"]  # 120 s at 20 s a section
    assert lyrics.split("\n\n") == [
        "[Intro]",
        "[Instrumental]",
        "[Solo]",
        "[Instrumental]",
        "[Bridge]",
        "[Outro]",
    ]
    lyrics = build("minimax-music3", duration=30)[0]["music"]["inputs"]["lyrics"]
    assert lyrics.split("\n\n") == ["[Intro]", "[Instrumental]", "[Outro]"]
    sung = build("yue2", lyrics="[verse]\nla la")[0]["music"]["inputs"]["lyrics"]
    assert sung == "[verse]\nla la"


def test_tempo_goes_into_the_description():
    assert build("yue2", bpm=82)[0]["music"]["inputs"]["style"].endswith(", 82 BPM")
    assert build("sa3-medium", bpm=90)[0]["pos"]["inputs"]["text"].endswith(", 90 BPM")


def test_minimax_wraps_tags_in_its_caption_sections_and_keeps_a_written_caption():
    g, _ = build("minimax-music3")
    caption = g["music"]["inputs"]["caption"]
    assert (
        caption.startswith("Global Metadata: lo-fi hip hop")
        and "Instrumental, no vocals" in caption
    )
    assert g["decode"]["class_type"] == "VAEDecodeAudioTiled"
    assert g["sample"]["inputs"]["cfg"] == 1.7 and g["sample"]["inputs"]["steps"] == 30
    own = "Global Metadata: jazz.\n\nVocal Details: none.\n\nArrangement: trio."
    assert build("minimax-music3", prompt=own)[0]["music"]["inputs"]["caption"] == own


def test_stable_audio_makes_instrumentals_of_the_asked_length():
    g, _ = build("sa3-medium", duration=200)
    assert g["latent"]["inputs"]["seconds"] == 200.0
    assert (g["sample"]["inputs"]["steps"], g["sample"]["inputs"]["sampler_name"]) == (8, "lcm")
    with pytest.raises(ValueError, match="instrumentals only"):
        build("sa3-medium", lyrics="[verse]\nhello")


def test_stable_audio_is_set_up_as_its_comfyui_template():
    # ComfyUI's template for this checkpoint: KSampler lcm / simple, 8 steps, cfg 1.0, with
    # the text encoder on the default device. Moving it to the CPU was measured: no faster,
    # and the sound changes slightly. Dynamic VRAM stays on (no reload between prompts).
    g, _ = build("sa3-medium")
    assert (
        g["sample"]["inputs"].items()
        >= {"steps": 8, "cfg": 1.0, "sampler_name": "lcm", "scheduler": "simple"}.items()
    )
    assert g["clip"]["inputs"]["type"] == "stable_audio"
    assert g["clip"]["inputs"]["device"] == "default"
    assert "sa3-music" in graphs.DYNAMIC_VRAM


def test_stable_audio_goes_to_six_minutes_and_says_so():
    m = model("sa3-medium")
    limit = m.settings["limits"]["duration"]
    assert limit["max"] == 360
    assert controls.check(m, {"duration": 360})[0]["duration"] == 360
    with pytest.raises(controls.ControlError, match="10 to 360 for this model"):
        controls.check(m, {"duration": 361})
    assert f"up to {limit['max'] // 60} minutes" in m.summary
    # The other two ComfyUI music models keep their own, shorter limit.
    for mid in ("yue2", "minimax-music3"):
        assert model(mid).settings["limits"]["duration"]["max"] == 300
        with pytest.raises(controls.ControlError, match="10 to 300"):
            controls.check(model(mid), {"duration": 360})


def test_length_defaults_to_what_the_registry_declares_and_is_not_clamped_here():
    # Range checks happen at submit time (controls.check); the builder uses what it is given.
    assert build("sa3-medium", duration=0)[0]["latent"]["inputs"]["seconds"] == 30.0
    assert build("sa3-medium", duration=999)[0]["latent"]["inputs"]["seconds"] == 999.0


def test_none_of_them_starts_from_a_recording():
    for mid in MUSIC:
        with pytest.raises(ValueError, match="recording"):
            build(mid, source="abc.wav", task="cover")


def test_the_save_node_gets_format_loop_and_trim():
    g, save = build("yue2", format="flac", bpm=90, loop=True, trim_end=True)
    assert (
        g[save]["inputs"].items()
        >= {"format": "flac", "bpm": 90, "loop": True, "trim_end": True}.items()
    )
    g, save = build("yue2")
    assert g[save]["inputs"]["loop"] is False and g[save]["inputs"]["format"] == "wav16"


# --- engine ---------------------------------------------------------------------------------------


def test_every_take_is_its_own_graph_with_the_next_seed(tmp_path, monkeypatch):
    engine = ComfyEngine.__new__(ComfyEngine)
    engine.io, engine.state, engine.hw = tmp_path / "io", "idle", HW
    engine.process = None
    seeds, plans = [], []

    async def ensure_running(*a, **k):
        pass

    async def execute(ctx, graph, plan=None):
        seeds.append(graph["sample"]["inputs"]["seed"])
        plans.append(plan)
        name = f"song_{len(seeds)}.wav"
        (tmp_path / "io" / "output").mkdir(parents=True, exist_ok=True)
        (tmp_path / "io" / "output" / name).write_bytes(b"RIFF")
        return {
            "save": {
                "audio": [{"filename": name, "subfolder": "", "type": "output", "seconds": 61.5}]
            }
        }

    engine.ensure_running, engine._execute = ensure_running, execute
    monkeypatch.setattr(ComfyEngine, "alive", property(lambda self: False))
    m = model("sa3-medium")
    names = {s.id: s.filename for s in registry.default().expand(m.variants[0].files)}
    ctx = RunContext(
        job_id="j",
        params={"prompt": "jazz trio", "takes": 3, "seed": 40, "duration": 60},
        model=m,
        variant=m.variants[0],
        files={k: Path(v) for k, v in names.items()},
        specs={},
        out_dir=tmp_path / "out",
        stem="s",
        report=lambda *a: None,
        category_dirs=lambda c: [],
    )
    out = asyncio.run(engine.run(ctx))
    assert seeds == [40, 41, 42]
    assert [o["seed"] for o in out] == [40, 41, 42]
    assert [Path(o["path"]).name for o in out] == ["s.wav", "s-2.wav", "s-3.wav"]
    assert all(o["type"] == "audio" and o["duration"] == 61.5 for o in out)
    assert plans[1]["sample"][2] == "take 2 of 3 · Rendering"
