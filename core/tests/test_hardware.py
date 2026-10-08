# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio

import pytest

from irisecho_core import hardware
from irisecho_core.hardware import Gpu

# nvidia-smi lists cards in PCI bus order; CUDA's default order puts the fastest first.
SMI = (
    "0, GPU-aaaa, NVIDIA GeForce RTX 3060, 12288, 591.86, 8.6\n"
    "1, GPU-bbbb, NVIDIA GeForce RTX 5090, 32607, 591.86, 12.0\n"
    "2, GPU-cccc, NVIDIA GeForce GTX 1080, 8192, 591.86, 6.1\n"
)


def test_nvidia_smi_rows_are_read_with_index_and_uuid():
    gpus = hardware.parse_gpus(SMI + "garbage\n")
    assert [(g.index, g.uuid, g.vram_mb) for g in gpus] == [
        (0, "GPU-aaaa", 12288),
        (1, "GPU-bbbb", 32607),
        (2, "GPU-cccc", 8192),
    ]
    assert gpus[1].name == "NVIDIA GeForce RTX 5090" and gpus[1].compute_cap == 12.0


def test_the_chosen_card_decides_the_builds():
    gpus = hardware.parse_gpus(SMI)
    assert hardware.classify("Windows", "AMD64", gpus) == ("cuda", "fp4", "cu130")
    assert hardware.classify("Windows", "AMD64", gpus, "GPU-aaaa") == ("cuda", "int4", "cu130")
    # A card the engines cannot run on, or one no longer there, falls back to automatic.
    assert hardware.choose(gpus, "GPU-cccc").uuid == "GPU-bbbb"
    assert hardware.choose(gpus, "GPU-gone").uuid == "GPU-bbbb"


def _hw(gpus, wanted=""):
    gpu = hardware.choose(gpus, wanted)
    return hardware.Hardware(
        system="Windows",
        machine="AMD64",
        ram_mb=32768,
        tier="supported",
        backend="cuda",
        quant="int4",
        cuda_tag="cu130",
        gpus=gpus,
        gpu_uuid=gpu.uuid,
        gpu_pinned=bool(wanted) and gpu.uuid == wanted,
    )


def test_engines_see_only_the_chosen_card(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    gpus = hardware.parse_gpus(SMI)
    env = hardware.gpu_env(_hw(gpus, "GPU-aaaa"))
    assert env == {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "GPU-aaaa"}
    # Picked automatically on a machine with several cards: still pinned to the one
    # the builds were chosen for.
    assert hardware.gpu_env(_hw(gpus))["CUDA_VISIBLE_DEVICES"] == "GPU-bbbb"
    assert _hw(gpus, "GPU-aaaa").public()["gpu"]["index"] == 0


def test_one_card_or_the_persons_own_choice_is_left_alone(monkeypatch):
    one = [Gpu("NVIDIA GeForce RTX 4070", 12282, "591.86", 8.9, 0, "GPU-only")]
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    assert hardware.gpu_env(_hw(one)) == {}
    gpus = hardware.parse_gpus(SMI)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    assert hardware.gpu_env(_hw(gpus)) == {}
    # A card chosen in Settings wins over the environment.
    assert hardware.gpu_env(_hw(gpus, "GPU-aaaa"))["CUDA_VISIBLE_DEVICES"] == "GPU-aaaa"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("IRISECHO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(hardware, "_nvidia_gpus", lambda: hardware.parse_gpus(SMI))
    monkeypatch.setattr(hardware.platform, "system", lambda: "Windows")
    hardware.detect.cache_clear()
    from irisecho_core.app import App

    yield App()
    hardware.detect.cache_clear()


def test_choosing_a_card_moves_every_engine_and_is_remembered(app):
    from irisecho_core import settings

    assert app.hw.gpu.uuid == "GPU-bbbb" and app.hw.quant == "fp4"
    released = []
    for engine in app.engines.values():

        async def release(engine=engine):
            released.append(engine.id)

        engine.release = release
    asyncio.run(app.set_gpu("GPU-aaaa"))
    assert app.hw.gpu.uuid == "GPU-aaaa" and app.hw.quant == "int4" and app.hw.gpu_pinned
    assert all(e.hw is app.hw for e in app.engines.values())
    assert sorted(released) == sorted(app.engines)
    assert settings.load().gpu == "GPU-aaaa"
    with pytest.raises(ValueError):
        asyncio.run(app.set_gpu("GPU-nope"))


def test_the_card_is_not_switched_under_a_running_job(app):
    from irisecho_core.app import Busy

    app.current_job = "a1"
    with pytest.raises(Busy):
        asyncio.run(app.set_gpu("GPU-aaaa"))
    assert app.hw.gpu.uuid == "GPU-bbbb"
