# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audio clean-up shared by the voice, music and sound workers. Needs only numpy.

Audio is float32, shaped [samples] or [samples, channels].
"""

from __future__ import annotations

import re

import numpy as np

# Output formats a job may ask for: name -> (container, sample format, file extension).
AUDIO_FORMATS = {
    "wav16": ("WAV", "PCM_16", "wav"),
    "wav24": ("WAV", "PCM_24", "wav"),
    "flac": ("FLAC", "PCM_24", "flac"),
}


def _level(audio: np.ndarray) -> np.ndarray:
    return np.abs(audio) if audio.ndim == 1 else np.max(np.abs(audio), axis=1)


def _fade(audio: np.ndarray, n: int, head: bool) -> None:
    n = min(n, len(audio))
    if n < 2:
        return
    ramp = np.sin(np.linspace(0.0, np.pi / 2, n, dtype=np.float32)) ** 2
    if audio.ndim == 2:
        ramp = ramp[:, None]
    if head:
        audio[:n] *= ramp
    else:
        audio[-n:] *= ramp[::-1]


def trim(
    audio: np.ndarray,
    sr: int,
    below_peak_db: float = 45.0,
    floor_db: float = -65.0,
    pad_s: float = 0.1,
    fade_s: float = 0.01,
) -> np.ndarray:
    """Cut leading and trailing silence, keeping a pad and fading any cut edge.

    Silence is anything `below_peak_db` under the loudest sample (and never
    above `floor_db`), so quiet word endings and decays stay.
    """
    level = _level(audio)
    if not level.size:
        return audio
    threshold = max(float(level.max()) * 10 ** (-below_peak_db / 20), 10 ** (floor_db / 20))
    loud = np.flatnonzero(level > threshold)
    if loud.size == 0:
        return audio
    pad = int(pad_s * sr)
    start, end = max(0, loud[0] - pad), min(len(audio), loud[-1] + pad + 1)
    out = audio[start:end].astype(np.float32, copy=True)
    fade = int(fade_s * sr)
    if start > 0:
        _fade(out, fade, head=True)
    if end < len(audio):
        _fade(out, fade, head=False)
    return out


def _biquad(b: tuple, a: tuple, w: np.ndarray) -> np.ndarray:
    z = np.exp(-1j * w)
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def _k_weighting(sr: int, freqs: np.ndarray) -> np.ndarray:
    """Complex response of the ITU-R BS.1770 K-weighting filter at `freqs` Hz."""
    w = 2 * np.pi * freqs / sr
    # Stage 1: high shelf, +4 dB above about 1.5 kHz.
    gain, q, fc = 4.0, 1 / np.sqrt(2), 1500.0
    big_a = 10 ** (gain / 40)
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * q)
    cos = np.cos(w0)
    root = 2 * np.sqrt(big_a) * alpha
    shelf = _biquad(
        (
            big_a * ((big_a + 1) + (big_a - 1) * cos + root),
            -2 * big_a * ((big_a - 1) + (big_a + 1) * cos),
            big_a * ((big_a + 1) + (big_a - 1) * cos - root),
        ),
        (
            (big_a + 1) - (big_a - 1) * cos + root,
            2 * ((big_a - 1) - (big_a + 1) * cos),
            (big_a + 1) - (big_a - 1) * cos - root,
        ),
        w,
    )
    # Stage 2: high pass at about 38 Hz.
    q, fc = 0.5, 38.0
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * q)
    cos = np.cos(w0)
    high = _biquad(((1 + cos) / 2, -(1 + cos), (1 + cos) / 2), (1 + alpha, -2 * cos, 1 - alpha), w)
    return shelf * high


def loudness(audio: np.ndarray, sr: int) -> float:
    """Integrated loudness in LUFS (BS.1770: K-weighted, 400 ms blocks, gated).

    The filter runs in the frequency domain over the whole clip, padded so
    its ringing is not wrapped around. Returns -inf for silence.
    """
    chans = audio[:, None] if audio.ndim == 1 else audio
    n = int(0.4 * sr)
    hop = n // 4
    if len(chans) < n:
        return float("-inf")
    size = 1 << int(np.ceil(np.log2(len(chans) + sr)))
    response = _k_weighting(sr, np.fft.rfftfreq(size, 1 / sr))
    filtered = np.fft.irfft(np.fft.rfft(chans, size, axis=0) * response[:, None], size, axis=0)
    energy = np.concatenate(
        [np.zeros((1, chans.shape[1])), np.cumsum(filtered[: len(chans)] ** 2, axis=0)]
    )
    starts = np.arange(0, len(chans) - n + 1, hop)
    z = ((energy[starts + n] - energy[starts]) / n).sum(axis=1)  # every channel weighted 1
    z = z[z > 0]
    if not z.size:
        return float("-inf")
    z = z[-0.691 + 10 * np.log10(z) > -70]
    if not z.size:
        return float("-inf")
    relative = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > relative]
    return float(-0.691 + 10 * np.log10(z.mean()))


def true_level(audio: np.ndarray, factor: int = 4) -> np.ndarray:
    """Per-sample peak level including the peaks between samples (BS.1770 true peak).

    Upsamples `factor` times in the frequency domain and keeps the loudest of
    each sample's sub-samples, across channels.
    """
    chans = audio[:, None] if audio.ndim == 1 else audio
    n = len(chans)
    if n < 2:
        return _level(audio)
    up = np.fft.irfft(np.fft.rfft(chans, axis=0), n * factor, axis=0) * factor
    return np.abs(up).reshape(n, factor, -1).max(axis=(1, 2))


def limit(
    audio: np.ndarray, sr: int, ceiling_db: float = -1.0, window_s: float = 0.005
) -> np.ndarray:
    """Hold true peaks under the ceiling with a smooth, fast gain dip around each one."""
    ceiling = 10 ** (ceiling_db / 20)
    level = true_level(audio)
    if not level.size or float(level.max()) <= ceiling:
        return audio
    half = max(1, int(window_s * sr) // 2)
    need = np.minimum(1.0, ceiling / np.maximum(level, 1e-9))
    # Minimum over +-half samples, then a Hann smooth of the same width: every
    # value averaged at a peak already includes that peak's gain, so the
    # smoothed gain never lets it through.
    padded = np.pad(need, half, constant_values=1.0)
    lowest = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1).min(axis=1)
    kernel = np.hanning(2 * half + 3)[1:-1]
    kernel /= kernel.sum()
    gain = np.convolve(np.pad(lowest, half, mode="edge"), kernel, mode="valid")
    gain = gain[: len(audio)]
    if audio.ndim == 2:
        gain = gain[:, None]
    return (audio * gain).astype(np.float32)


def normalize(
    audio: np.ndarray, sr: int, lufs: float = -19.0, ceiling_db: float = -1.0
) -> np.ndarray:
    """Bring the integrated loudness to `lufs`, limiting peaks at `ceiling_db`."""
    current = loudness(audio, sr)
    if not np.isfinite(current):
        return audio
    out = audio
    # Limiting a loud peak takes some loudness with it; a second pass makes it up.
    for _ in range(2):
        out = limit((out * 10 ** ((lufs - current) / 20)).astype(np.float32), sr, ceiling_db)
        current = loudness(out, sr)
        if not np.isfinite(current) or abs(current - lufs) < 0.2:
            break
    return out


HOP = 512


def onset_strength(audio: np.ndarray, sr: int) -> np.ndarray:
    """Positive spectral flux per HOP samples: how sharply each frame starts a note."""
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    n = 2048
    if len(mono) < n:
        return np.zeros(1, dtype=np.float32)
    # Centre frame i on sample i * HOP.
    padded = np.concatenate([np.zeros(n // 2, dtype=mono.dtype), mono])
    frames = np.lib.stride_tricks.sliding_window_view(padded, n)[::HOP]
    mag = np.sqrt(np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    return (flux - flux.mean()).clip(min=0).astype(np.float32)


def beat_grid(audio: np.ndarray, sr: int, bpm: float) -> tuple[float, int]:
    """Find the tempo (within 2 % of `bpm`) and first downbeat (in samples) the music follows.

    Scores every candidate tempo and bar phase by the onset strength landing on
    its beats, with downbeats counted double.
    """
    env = onset_strength(audio, sr)
    best = (-1.0, float(bpm), 0)
    for tempo in np.linspace(bpm * 0.98, bpm * 1.02, 41):
        beat = sr * 60 / tempo / HOP
        steps = np.arange(0, len(env) / beat) * beat
        for phase in range(int(beat * 4)):
            at = np.round(phase + steps).astype(int)
            at = at[at < len(env)]
            if at.size < 8:
                continue
            downbeats = at[::4]
            score = float(env[at].sum() + env[downbeats].sum()) / at.size
            if score > best[0]:
                best = (score, float(tempo), phase * HOP)
    tempo, start = best[1], best[2]
    return tempo, _refine(audio, sr, tempo, start)


def _refine(audio: np.ndarray, sr: int, tempo: float, start: int) -> int:
    """Move the downbeat to the sample where the beats' attacks actually begin.

    Averages a fine energy envelope over every beat of the grid and picks the
    steepest rise near the coarse estimate.
    """
    mono = np.abs(audio.mean(axis=1) if audio.ndim == 2 else audio)
    block = 32
    env = np.log1p(1000 * mono[: len(mono) // block * block].reshape(-1, block).mean(axis=1))
    beat = sr * 60 / tempo / block
    reach = 3 * HOP // block
    centre = start // block
    at = np.round(centre + np.arange(0, (len(env) - centre - reach - 1) / beat) * beat).astype(int)
    if at.size < 4:
        return start
    rises = [
        float(
            np.sum(
                env[np.clip(at + d, 0, len(env) - 1)] - env[np.clip(at + d - 2, 0, len(env) - 1)]
            )
        )
        for d in range(-reach, reach + 1)
    ]
    best = centre - reach + int(np.argmax(rises)) - 1
    return max(0, best * block)


def loop_cut(audio: np.ndarray, sr: int, bpm: int, xfade: float = 0.05) -> np.ndarray:
    """Cut a whole number of 4/4 bars starting on a downbeat, and blend the seam.

    The music after the last bar is faded into the first bar so the wrap
    continues exactly as the track would have.
    """
    tempo, start = beat_grid(audio, sr, float(bpm))
    bar = sr * 240 / tempo
    fade = int(sr * xfade)
    bars = int((len(audio) - start - fade) // bar)
    if bars < 1:
        return audio
    end = start + int(round(bars * bar))
    body = audio[start:end].copy()
    tail = audio[end : end + fade]
    t = np.linspace(0.0, np.pi / 2, len(tail), dtype=np.float32)
    if body.ndim == 2:
        t = t[:, None]
    body[: len(tail)] = body[: len(tail)] * np.sin(t) + tail * np.cos(t)
    return body


def text_chunks(text: str, limit: int = 300) -> list[str]:
    """Split text into pieces of whole sentences, each at most about `limit` characters.

    A sentence longer than the limit is split at commas, then at spaces.
    """
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+|\n+", text.strip()) if s.strip()]
    pieces: list[str] = []
    for sentence in sentences:
        parts = [sentence.strip()]
        if len(sentence) > limit:
            parts = _split_long(sentence.strip(), limit)
        for part in parts:
            if pieces and len(pieces[-1]) + 1 + len(part) <= limit:
                pieces[-1] = f"{pieces[-1]} {part}"
            else:
                pieces.append(part)
    return pieces or [text.strip()]


def _split_long(sentence: str, limit: int) -> list[str]:
    out: list[str] = []
    for clause in re.split(r"(?<=[,;:])\s+", sentence):
        words = clause.split()
        while words:
            line = words.pop(0)
            while words and len(line) + 1 + len(words[0]) <= limit:
                line = f"{line} {words.pop(0)}"
            if out and len(out[-1]) + 1 + len(line) <= limit:
                out[-1] = f"{out[-1]} {line}"
            else:
                out.append(line)
    return out


def write_audio(path, audio: np.ndarray, sr: int, fmt: str = "wav16") -> None:
    """Write `audio` as one of AUDIO_FORMATS (16-bit WAV unless asked otherwise)."""
    import soundfile as sf  # in every audio engine's environment; the rest needs only numpy

    if fmt not in AUDIO_FORMATS:
        raise ValueError(f"unknown audio format {fmt}")
    container, subtype, _ = AUDIO_FORMATS[fmt]
    sf.write(path, audio, sr, subtype=subtype, format=container)
