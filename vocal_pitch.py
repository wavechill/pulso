"""Pitch and confidence from a bounded block of separated vocal magnitudes."""
from functools import lru_cache

import numpy as np


MIDI = np.arange(37, 90, .25, dtype=np.float32)
SPECTRAL_BINS = 250
BIN_HZ = 44100 / 4096


@lru_cache(maxsize=1)
def _harmonic_filters():
    hz = 440 * 2 ** ((MIDI - 69) / 12)
    cycles = np.arange(SPECTRAL_BINS) * BIN_HZ / hz[:, None]
    harmonic = np.rint(cycles)
    distance = np.abs(cycles - harmonic)
    bins_per_harmonic = hz[:, None] / BIN_HZ
    # Reward spectral peaks at the harmonics and penalize energy between them.
    # The second term distinguishes a real fundamental from its lowest strong
    # formant or a subharmonic that also happens to fit the same peaks.
    peaks = np.exp(-.5 * (distance * bins_per_harmonic / .9) ** 2)
    peaks *= (harmonic >= 1) & (distance < .36)
    peaks *= np.maximum(1, harmonic) ** -.70
    valleys = np.exp(-.5 * ((.5 - distance) * bins_per_harmonic / 1.44) ** 2)
    valleys *= (cycles >= .45) * np.maximum(1, cycles) ** -.70
    return ((peaks - .65 * valleys) / np.linalg.norm(peaks, axis=1, keepdims=True)).astype(np.float32)


def estimate_pitch(vocals):
    """Return MIDI and spectral confidence, omitting the one-frame halos."""
    magnitude = vocals[:, :SPECTRAL_BINS].copy(order="C")
    padded = np.pad(magnitude, ((0, 0), (5, 6)), mode="edge")
    accumulated = np.cumsum(np.pad(padded, ((0, 0), (1, 0))), axis=1)
    envelope = (accumulated[:, 12:] - accumulated[:, :-12]) / 12
    # Mildly flatten loud formants so weaker harmonics still contribute to F0.
    magnitude /= (np.maximum(1.e-4, envelope) + .2 * np.percentile(magnitude, 70, axis=1)[:, None]) ** .24
    magnitude[:, :5] = 0
    magnitude /= np.maximum(1.e-7, np.linalg.norm(magnitude, axis=1, keepdims=True))
    score = np.einsum("tk,pk->tp", magnitude, _harmonic_filters(), optimize=False, dtype=np.float32,
                      casting="unsafe")
    # Average 36 ms of evidence to reject a single-frame flip without merging
    # neighbouring syllables. The caller supplies halos across block boundaries.
    score = .64 * score[1:-1] + .18 * (score[:-2] + score[2:])
    index = score.argmax(axis=1)
    pitch = MIDI[index]
    best = score[np.arange(len(index)), index]
    competing = np.max(score, where=np.abs(MIDI[None, :] - pitch[:, None]) >= 2, axis=1, initial=-1.)
    margin = np.clip((best - competing) / .17, 0, 1)
    confidence = np.clip((best - .22) / .47, 0, 1) * (.53 + .47 * margin)
    return pitch, confidence


def vocal_pitch_confidence(confidence, voice, mixed_voice):
    """Reject a periodic instrumental leak when the vocal stem is quiet."""
    voice_level = np.linalg.norm(voice, axis=1)
    mix_level = np.linalg.norm(mixed_voice, axis=1)
    share = voice_level / np.maximum(.5, mix_level)
    loudness = voice_level / max(.5, np.percentile(mix_level, 85))
    presence = np.minimum(np.clip((share - .14) / .21, 0, 1), np.clip((loudness - .025) / .095, 0, 1))
    return confidence * presence
