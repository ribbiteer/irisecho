# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audio clean-up shared by the voice workers. Needs only numpy.

Audio is float32, shaped [samples] or [samples, channels].
"""

from __future__ import annotations

import numpy as np


def _level(audio: np.ndarray) -> np.ndarray:
    return np.abs(audio) if audio.ndim == 1 else np.max(np.abs(audio), axis=1)


def trim(audio: np.ndarray, sr: int, db: float = -42.0, pad_s: float = 0.06) -> np.ndarray:
    """Cut leading and trailing audio quieter than `db` dBFS, keeping a short pad."""
    loud = np.flatnonzero(_level(audio) > 10 ** (db / 20))
    if loud.size == 0:
        return audio
    pad = int(pad_s * sr)
    return audio[max(0, loud[0] - pad) : loud[-1] + pad + 1]


def normalize(audio: np.ndarray, db: float = -18.0, ceiling_db: float = -1.0) -> np.ndarray:
    """Scale to a target RMS loudness, never letting peaks pass the ceiling."""
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    if rms == 0:
        return audio
    gain = 10 ** (db / 20) / rms
    peak = float(np.max(np.abs(audio))) * gain
    ceiling = 10 ** (ceiling_db / 20)
    if peak > ceiling:
        gain *= ceiling / peak
    return (audio * gain).astype(np.float32)


def pitch_range(audio: np.ndarray, sr: int) -> float:
    """A rough measure of how lively a delivery is: spread of per-frame pitch, in semitones.

    Autocorrelation pitch per 40 ms voiced frame; flat reads score low.
    """
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    frame = int(0.04 * sr)
    lo, hi = int(sr / 400), int(sr / 70)
    pitches = []
    for start in range(0, len(mono) - frame, frame):
        x = mono[start : start + frame]
        if np.sqrt(np.mean(x * x)) < 0.02:
            continue
        x = x - x.mean()
        corr = np.correlate(x, x, mode="full")[frame - 1 :]
        if corr[0] <= 0 or hi >= len(corr):
            continue
        lag = lo + int(np.argmax(corr[lo:hi]))
        if corr[lag] / corr[0] > 0.3:
            pitches.append(sr / lag)
    if len(pitches) < 5:
        return 0.0
    semis = 12 * np.log2(np.array(pitches) / np.median(pitches))
    return float(np.percentile(semis, 90) - np.percentile(semis, 10))
