# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from irisecho_core import controls, registry
from irisecho_core.controls import ControlError
from irisecho_core.registry import Control, ModelSpec


def spec(*cs: Control, kind="image") -> ModelSpec:
    return ModelSpec(
        id="m",
        name="Test",
        kind=kind,
        engine="comfy",
        license="mit",
        summary="",
        variants=(),
        controls=cs,
    )


STEPS = Control(name="steps", type="int", label="Steps", default=20, min=8, max=40)
GUIDE = Control(name="guidance", type="float", label="Guidance", default=3.5, min=1, max=6)
SAMPLER = Control(
    name="sampler", type="choice", label="Sampler", default="euler", options=("euler", "heun")
)
FAST = Control(name="fast", type="bool", label="Fast", default=False)
LORAS = Control(name="loras", type="loras", label="Add-ons", default=[])
PAIRS = Control(name="loras", type="loras", label="Add-ons", default=[], pair=True)


def test_values_are_converted_and_checked():
    out, warnings = controls.check(
        spec(STEPS, GUIDE, SAMPLER, FAST),
        {
            "prompt": "a fox",
            "steps": "12",
            "guidance": 4,
            "sampler": "heun",
            "fast": "on",
        },
    )
    assert out == {"prompt": "a fox", "steps": 12, "guidance": 4.0, "sampler": "heun", "fast": True}
    assert warnings == []


@pytest.mark.parametrize(
    "params, message",
    [
        ({"steps": 7}, "between 8 and 40"),
        ({"steps": 12.5}, "whole number"),
        ({"steps": True}, "number"),
        ({"guidance": "lots"}, "number"),
        ({"guidance": float("nan")}, "number"),
        ({"sampler": "dpm"}, "one of: euler, heun"),
        ({"fast": "maybe"}, "true or false"),
    ],
)
def test_bad_values_are_refused_with_a_plain_message(params, message):
    with pytest.raises(ControlError, match=message):
        controls.check(spec(STEPS, GUIDE, SAMPLER, FAST), params)


def test_none_means_recommended_and_is_dropped():
    out, _ = controls.check(spec(STEPS), {"steps": None, "prompt": "x"})
    assert out == {"prompt": "x"}


def test_unknown_params_are_kept_but_reported():
    out, warnings = controls.check(spec(STEPS), {"prompt": "x", "cfg_scale": 7, "id": "cue-1"})
    assert out == {"prompt": "x", "cfg_scale": 7, "id": "cue-1"}
    assert warnings == ["Test does not use 'cfg_scale'; it was ignored."]


def test_loras_are_checked():
    out, _ = controls.check(
        spec(LORAS), {"loras": [{"file": "style.safetensors", "strength": "0.8"}]}
    )
    assert out["loras"] == [{"file": "style.safetensors", "strength": 0.8}]
    out, _ = controls.check(spec(LORAS), {"loras": []})
    assert "loras" not in out
    with pytest.raises(ControlError, match="at most 3"):
        controls.check(spec(LORAS), {"loras": [{"file": "a"}] * 4})
    with pytest.raises(ControlError, match="between -2 and 2"):
        controls.check(spec(LORAS), {"loras": [{"file": "a", "strength": 5}]})
    with pytest.raises(ControlError, match="needs a file"):
        controls.check(spec(LORAS), {"loras": [{"strength": 1}]})


def test_paired_loras_take_a_high_and_or_low_file():
    out, _ = controls.check(
        spec(PAIRS), {"loras": [{"high": "h.safetensors", "low": "l.safetensors"}]}
    )
    assert out["loras"] == [{"high": "h.safetensors", "low": "l.safetensors", "strength": 1.0}]
    with pytest.raises(ControlError, match="high- or low-noise"):
        controls.check(spec(PAIRS), {"loras": [{"strength": 1}]})


def test_opt_prefers_the_job_then_the_model_then_the_fallback():
    assert controls.opt({"steps": 12}, {"steps": 20}, "steps", 9) == 12
    assert controls.opt({}, {"steps": 20}, "steps", 9) == 20
    assert controls.opt({}, {}, "steps", 9) == 9


def test_every_registry_control_is_well_formed():
    for m in registry.default().models.values():
        for c in m.controls:
            assert c.label and c.help, f"{m.id}.{c.name} needs a label and help text"
            assert c.name not in controls.BASE.get(m.kind, set()), (
                f"{m.id}.{c.name} shadows a base param"
            )
            if c.type in ("int", "float"):
                assert c.min is not None and c.max is not None, f"{m.id}.{c.name} needs a range"
            if c.default is not None:
                assert controls.coerce(c, c.default) == c.default


def test_control_defaults_match_the_models_settings():
    """A control's recommended value is what the model runs with when the control is unset."""
    for m in registry.default().models.values():
        for c in m.controls:
            value = m.settings.get(c.name)
            if value is not None and not isinstance(value, bool | dict | list):
                assert c.default == value, (
                    f"{m.id}.{c.name}: default {c.default} but the model runs {value}"
                )


def test_choices_match_numbers_and_text_both_ways():
    sig = Control(name="sig", type="choice", label="Time", default="", options=("", "3", "4"))
    num = Control(name="n", type="choice", label="N", default=8, options=(8, 12))
    assert controls.coerce(sig, 4) == "4"
    assert controls.coerce(num, "12") == 12
    with pytest.raises(ControlError, match='"" \(any\), 3, 4'):
        controls.coerce(sig, 5)
