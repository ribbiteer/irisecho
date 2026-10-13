# SPDX-License-Identifier: AGPL-3.0-or-later
"""The audio clean-up the voice, music and sound workers share."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "src/irisecho_core/engines/protocol"))
import irisecho_audio as au  # noqa: E402

SR = 48000


def clicks(bpm: float, seconds: float, offset: float) -> np.ndarray:
    """A click track with accented downbeats, starting `offset` seconds in."""
    audio = np.zeros(int(SR * seconds), dtype=np.float32)
    beat = 60 / bpm
    burst = np.random.default_rng(1).standard_normal(400).astype(np.float32)
    k = 0
    while offset + k * beat < seconds - 0.01:
        at = int((offset + k * beat) * SR)
        gain = 0.9 if k % 4 == 0 else 0.4
        n = min(len(burst), len(audio) - at)
        audio[at : at + n] += gain * burst[:n]
        k += 1
    return np.stack([audio, audio], axis=1)


def test_beat_grid_finds_late_downbeat_and_tempo():
    tempo, start = au.beat_grid(clicks(101.0, 20, 0.37), SR, 100.0)
    assert abs(tempo - 101.0) < 0.3
    assert abs(start / SR - 0.37) < 0.02


def test_loop_cut_is_whole_bars_from_the_downbeat():
    audio = clicks(120.0, 20, 0.25)
    loop = au.loop_cut(audio, SR, 120)
    bar = SR * 2.0
    assert abs(len(loop) / bar - round(len(loop) / bar)) < 0.01
    assert np.max(np.abs(loop[:200])) > 0.3  # opens on the downbeat click


def test_loop_cut_mono_and_too_short():
    mono = clicks(120.0, 12, 0.0)[:, 0]
    assert au.loop_cut(mono, SR, 120).ndim == 1
    short = np.zeros(SR, dtype=np.float32)
    assert len(au.loop_cut(short, SR, 120)) == SR


def sine(freq: float, amp: float, seconds: float, sr: int = SR) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_loudness_matches_bs1770_reference_tone():
    # BS.1770: a 997 Hz sine at 0 dBFS in one channel reads -3.01 LUFS.
    assert abs(au.loudness(sine(997, 1.0, 5), SR) + 3.01) < 0.1
    assert abs(au.loudness(sine(997, 0.1, 5, 24000), 24000) + 23.01) < 0.1
    stereo = np.stack([sine(997, 0.1, 5)] * 2, axis=1)
    assert abs(au.loudness(stereo, SR) + 20.0) < 0.1
    assert au.loudness(np.zeros(SR * 2, dtype=np.float32), SR) == float("-inf")


def test_normalize_hits_target_and_limits_peaks():
    quiet = sine(220, 0.02, 4)
    quiet[SR : SR + 50] = 0.9  # one spike
    out = au.normalize(quiet, SR, lufs=-19.0, ceiling_db=-1.0)
    assert abs(au.loudness(out, SR) + 19.0) < 0.3
    assert np.max(np.abs(out)) <= 10 ** (-1 / 20) + 1e-4


def test_trim_keeps_quiet_tail_and_fades_cut_edges():
    sr = 24000
    voice = sine(200, 0.5, 1.0, sr)
    tail = sine(200, 0.5 * 10 ** (-35 / 20), 0.3, sr)  # a soft word ending
    audio = np.concatenate([np.zeros(sr, np.float32), voice, tail, np.zeros(sr, np.float32)])
    out = au.trim(audio, sr)
    assert len(out) >= len(voice) + len(tail)
    assert len(out) < len(audio) - sr
    assert abs(out[0]) < 1e-6 and abs(out[-1]) < 1e-6


def test_text_chunks_keep_sentences_whole():
    short = "Hello there. How are you?"
    assert au.text_chunks(short) == [short]
    text = " ".join(f"Sentence number {i} is here." for i in range(40))
    pieces = au.text_chunks(text, limit=120)
    assert all(len(p) <= 120 for p in pieces)
    assert all(p.endswith(".") for p in pieces)
    assert " ".join(pieces) == text


def test_text_chunks_split_long_sentence_and_lines():
    long = ", ".join(["a clause of several words"] * 30) + "."
    pieces = au.text_chunks(long, limit=100)
    assert all(len(p) <= 100 for p in pieces)
    assert " ".join(pieces) == long
    assert au.text_chunks("Line one\nLine two", limit=10) == ["Line one", "Line two"]
