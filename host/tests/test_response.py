"""The response per third, its coherence and its random error (`dsp/response.py`). SIMULATED."""

import numpy as np
import pytest

from aurasync import estimulos
from aurasync.dsp import response

SR = 48000
GAINS = (1.0, 0.8, 0.6, 0.9, 0.7, 0.95, 0.75, 0.85)


def _recording(n: int, seconds: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Speaker 0's noise and the microphone: N independent noises plus microphone noise."""
    tracks = [0.1 * t for t in estimulos.calibracion(n, seconds, semilla=seed)]
    rng = np.random.default_rng(seed + 100)
    mic = sum(g * t for g, t in zip(GAINS, tracks, strict=False)) + 0.001 * rng.standard_normal(len(tracks[0]))
    return mic, tracks[0]


def test_the_response_is_the_same_as_before():
    mic, ref = _recording(3, 6.0, 1)
    bands, _, _ = response.response_with_coherence(mic, ref, 0, SR)
    assert np.array_equal(bands, response.response_bands(mic, ref, 0, SR))


@pytest.mark.parametrize(("n", "expected"), [(1, 1.0), (3, 1 / (1 + 0.8**2 + 0.6**2))])
def test_the_coherence_is_this_speakers_share_of_what_the_microphone_hears(n, expected):
    mic, ref = _recording(n, 6.0, 2)
    _, coherence, _ = response.response_with_coherence(mic, ref, 0, SR)
    mid = (response.THIRDS >= 200) & (response.THIRDS <= 8000)
    # The estimate is biased up by about (1 - gamma^2)^2 / averages: +0.03 at 6 s with 3.
    assert np.allclose(coherence[mid], expected, atol=0.06), coherence[mid]


@pytest.mark.parametrize(("n", "seconds"), [(3, 10.0), (3, 5.0), (8, 10.0)])
def test_the_predicted_error_matches_the_spread_over_independent_noises(n, seconds):
    """`error_db` is what the panel blanks on: it has to be the real scatter of a third, within
    a factor of two, over 12 independent realisations of every noise."""
    runs = [response.response_with_coherence(*_recording(n, seconds, seed), 0, SR) for seed in range(12)]
    bands = np.array([r[0] for r in runs])
    predicted = np.mean([r[2] for r in runs], axis=0)
    sel = (response.THIRDS >= 100) & (response.THIRDS <= 8000)
    ratio = bands.std(axis=0)[sel] / predicted[sel]
    assert np.all((ratio > 0.5) & (ratio < 2.0)), ratio


def test_silent_segments_of_a_group_calibration_are_not_averaged():
    """A speaker of the other group is silent half of the recording: its coherence must not
    fall for that (the silent half is skipped)."""
    mic, ref = _recording(3, 6.0, 3)
    gap = np.zeros(int(2.5 * SR))  # `session.CAL_GAP_S`
    half = np.concatenate([ref, gap, np.zeros_like(ref)])
    other_group, _ = _recording(4, 6.0, 4)  # the other group plays while this speaker is silent
    longer = np.concatenate([mic, 0.001 * np.random.default_rng(9).standard_normal(len(gap)), other_group])
    _, coherence, _ = response.response_with_coherence(longer, half, 0, SR)
    _, alone, _ = response.response_with_coherence(mic, ref, 0, SR)
    mid = (response.THIRDS >= 200) & (response.THIRDS <= 8000)
    assert np.allclose(coherence[mid], alone[mid], atol=0.02)
