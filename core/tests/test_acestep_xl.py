# SPDX-License-Identifier: AGPL-3.0-or-later
"""ACE-Step 1.5 XL and XL SFT: the opt-in bigger music models.

They run on the ACE-Step engine with other files and memory settings; ACE-Step 1.5
itself must load and compose exactly as before.
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from irisecho_core import controls, registry
from irisecho_core.engines import acestep
from irisecho_core.engines.base import RunContext

ENGINES = Path(__file__).parents[1] / "src/irisecho_core/engines"
sys.path.insert(0, str(ENGINES / "protocol"))

HW = SimpleNamespace(backend="cuda", cuda_tag="cu128", gpus=[], vram_mb=12000)
TAKE = {"takes": [{"path": "a", "duration": 1, "seed": 1}]}


class Files(dict):
    def __missing__(self, key):
        return Path("models") / key


def run(model_id: str, params: dict, out_dir: Path):
    """The (op, request) pairs the engine sends its worker for one job."""
    sent: list[tuple[str, dict]] = []
    engine = acestep.AceStepEngine(HW)

    async def fake(ctx, op, request):
        sent.append((op, request))
        return TAKE

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
    return dict(sent)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    # Every ACE-Step file group sits in the one checkpoint folder.
    monkeypatch.setattr(acestep, "group_root", lambda ctx, group: Path("root"))
    return tmp_path


def load_worker():
    spec = importlib.util.spec_from_file_location("acestep_worker", ENGINES / "acestep/worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- registry -------------------------------------------------------------------------------------


def test_xl_models_share_ace_steps_decoder_and_text_encoder_by_file_id():
    reg = registry.default()
    core = {f.id for f in reg.expand(reg.models["ace-step"].variants[0].files)}
    for mid, dit in (("ace-step-xl", "ace-step-xl-turbo"), ("ace-step-xl-sft", "ace-step-xl-sft")):
        model = reg.models[mid]
        assert [v.when for v in model.variants] == [{"backend": "cuda"}]
        ids = {f.id for f in reg.expand(model.variants[0].files)}
        shared = ids & core
        # Same ids, so removing one model never deletes what the other still uses.
        assert "ace-step-core/vae/diffusion_pytorch_model.safetensors" in shared
        assert "ace-step-core/Qwen3-Embedding-0.6B/model.safetensors" in shared
        assert "ace-step-core/acestep-v15-turbo/model.safetensors" not in ids
        assert not ids & {f.id for f in reg.expand(["ace-step-lm"])}
        assert set(reg.groups[dit]) <= ids and set(reg.groups["ace-step-lm-4b"]) <= ids
        for f in reg.expand(model.variants[0].files):
            assert f.no_link and f.pinned and f.category.startswith("ace-step")


def test_xl_controls_are_ace_steps_own():
    reg = registry.default()
    base = {c.name: c for c in reg.models["ace-step"].controls}
    xl = {c.name: c for c in reg.models["ace-step-xl"].controls}
    assert xl == base
    sft = {c.name: c for c in reg.models["ace-step-xl-sft"].controls}
    assert set(sft) == set(base) | {"cfg"}
    assert {k: v for k, v in sft.items() if k not in ("steps", "cfg")} == {
        k: v for k, v in base.items() if k != "steps"
    }
    assert (sft["steps"].default, sft["steps"].min, sft["steps"].max) == (50, 10, 100)
    assert (sft["cfg"].default, sft["cfg"].min, sft["cfg"].max) == (7.0, 1, 15)


def test_the_default_value_of_every_xl_control_changes_nothing():
    for mid in ("ace-step-xl", "ace-step-xl-sft"):
        m = registry.default().models[mid]
        params = {c.name: c.default for c in m.controls}
        out, _ = controls.check(m, params)
        assert set(out) == set(params)


# --- engine requests ------------------------------------------------------------------------------


def test_ace_step_loads_as_before(home):
    load = run("ace-step", {"prompt": "jazz"}, home)["load"]
    assert load["dit"] == "acestep-v15-turbo" and load["lm"] == "acestep-5Hz-lm-0.6B"
    assert load["quantization"] is None and load["offload"] is False


def test_xl_loads_the_4b_renderer_and_planner_with_offload(home):
    for mid, dit in (
        ("ace-step-xl", "acestep-v15-xl-turbo"),
        ("ace-step-xl-sft", "acestep-v15-xl-sft"),
    ):
        load = run(mid, {"prompt": "jazz"}, home)["load"]
        assert load["dit"] == dit and load["lm"] == "acestep-5Hz-lm-4B"
        assert load["quantization"] == "int8_weight_only" and load["offload"] is True


def test_xl_turbo_compose_adds_only_the_pass_limit(home):
    request = run("ace-step-xl", {"prompt": "jazz", "takes": 2}, home)["compose"]
    assert request["pass_seconds"] == 480
    assert not {"steps", "cfg", "sft"} & set(request)


def test_xl_sft_gets_its_steps_and_guidance(home):
    request = run("ace-step-xl-sft", {"prompt": "jazz"}, home)["compose"]
    assert (request["steps"], request["cfg"], request["sft"]) == (50, 7.0, True)
    request = run("ace-step-xl-sft", {"prompt": "jazz", "steps": 500, "cfg": 0}, home)["compose"]
    assert (request["steps"], request["cfg"]) == (100, 1.0)


def test_cfg_is_ignored_on_models_without_it(home):
    assert "cfg" not in run("ace-step", {"prompt": "jazz", "cfg": 9}, home)["compose"]


def test_xl_refuses_a_renderer_in_another_folder(home, monkeypatch):
    monkeypatch.setattr(acestep, "group_root", lambda ctx, group: Path("root") / group)
    with pytest.raises(RuntimeError, match="split between folders"):
        run("ace-step-xl", {"prompt": "jazz"}, home)


# --- worker ---------------------------------------------------------------------------------------


def test_pass_limit_caps_takes_per_pass():
    worker = load_worker()
    allow_all = SimpleNamespace(_vram_guard_reduce_batch=lambda n, **k: n)
    assert worker.pass_size(allow_all, 3, 240, 480) == 2
    assert worker.pass_size(allow_all, 3, 600, 480) == 1
    assert worker.pass_size(allow_all, 4, 60, 480) == 4
    assert worker.pass_size(allow_all, 3, 240) == 3  # no limit: as before


def fake_inference(monkeypatch, gens: list, batches: list):
    def generate_music(dit, llm, gen, config, save_dir=None, progress=None):
        gens.append(gen)
        batches.append(config.batch_size)
        audios = [{"tensor": None} for _ in range(config.batch_size)]
        return SimpleNamespace(success=True, audios=audios, error=None, extra_outputs={})

    inference = types.SimpleNamespace(
        GenerationParams=lambda **kw: SimpleNamespace(**kw),
        GenerationConfig=lambda **kw: SimpleNamespace(**kw),
        generate_music=generate_music,
    )
    monkeypatch.setitem(sys.modules, "acestep", types.ModuleType("acestep"))
    monkeypatch.setitem(sys.modules, "acestep.inference", inference)


def test_worker_sft_turns_dcw_off_and_sets_guidance(monkeypatch):
    worker = load_worker()
    gens: list = []
    fake_inference(monkeypatch, gens, [])
    monkeypatch.setitem(worker.state, "dit", SimpleNamespace())
    reporter = SimpleNamespace(progress=lambda *a: None)
    with pytest.raises(RuntimeError):  # the fake makes no audio; the request is what counts
        worker.compose(
            {"caption": "c", "duration": 30, "outs": ["a"], "sft": True, "steps": 50, "cfg": 7.0},
            reporter,
        )
    gen = gens[0]
    assert (gen.dcw_enabled, gen.guidance_scale, gen.inference_steps) == (False, 7.0, 50)
    gens.clear()
    with pytest.raises(RuntimeError):
        worker.compose({"caption": "c", "duration": 30, "outs": ["a"]}, reporter)
    assert not {"dcw_enabled", "guidance_scale"} & set(vars(gens[0]))


def test_worker_swaps_models_instead_of_stacking_them(monkeypatch):
    worker = load_worker()
    inits, unloads = [], []

    class Handler:
        def initialize_service(self, **kw):
            inits.append(kw)
            return "ok", True

    class LLM:
        def initialize(self, **kw):
            inits.append(kw)
            return "ok", True

        def unload(self):
            unloads.append(True)

    monkeypatch.setitem(sys.modules, "acestep", types.ModuleType("acestep"))
    monkeypatch.setitem(sys.modules, "acestep.handler", SimpleNamespace(AceStepHandler=Handler))
    monkeypatch.setitem(sys.modules, "acestep.llm_inference", SimpleNamespace(LLMHandler=LLM))
    reporter = SimpleNamespace(progress=lambda *a: None)
    base = {"checkpoints": "c", "project": "p", "device": "cuda", "lm_root": "c", "thinking": True}
    worker.load({**base, "lm": "acestep-5Hz-lm-0.6B"}, reporter)
    assert inits[0]["config_path"] == "acestep-v15-turbo"
    assert (inits[0]["offload_to_cpu"], inits[0]["quantization"]) == (False, None)
    assert inits[1]["offload_to_cpu"] is False
    worker.load({**base, "lm": "acestep-5Hz-lm-0.6B"}, reporter)
    assert len(inits) == 2  # same model: nothing reloaded
    xl = {**base, "dit": "acestep-v15-xl-turbo", "lm": "acestep-5Hz-lm-4B", "offload": True}
    worker.load({**xl, "quantization": "int8_weight_only"}, reporter)
    assert unloads == [True]  # the 0.6B planner left before the XL models came in
    assert inits[2]["config_path"] == "acestep-v15-xl-turbo"
    assert inits[2]["offload_dit_to_cpu"] is True and inits[3]["offload_to_cpu"] is True
    assert inits[3]["lm_model_path"] == "acestep-5Hz-lm-4B"
