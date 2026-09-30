# SPDX-License-Identifier: AGPL-3.0-or-later
"""Engines IrisEcho can install and run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from irisecho_core.engines.base import Engine

if TYPE_CHECKING:
    from irisecho_core.app import App


def build_engines(app: App) -> dict[str, Engine]:
    from irisecho_core.engines.acestep import AceStepEngine
    from irisecho_core.engines.chatterbox import ChatterboxEngine
    from irisecho_core.engines.comfy import ComfyEngine
    from irisecho_core.engines.kokoro import KokoroEngine
    from irisecho_core.engines.promptwriter import PromptWriterEngine
    from irisecho_core.engines.stableaudio import StableAudioEngine

    engines: list[Engine] = [
        KokoroEngine(app.hw),
        ComfyEngine(app.hw, app),
        AceStepEngine(app.hw),
        StableAudioEngine(app.hw),
        ChatterboxEngine(app.hw),
        PromptWriterEngine(app.hw),
    ]
    return {e.id: e for e in engines}
