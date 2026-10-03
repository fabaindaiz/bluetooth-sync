"""Calibrating more than six speakers in two groups with an anchor (`group_calibration.py`).

SIMULATED: the session's own `Calibration` (stimulus timeline, slicing, joining) on a room with
known delays and gains, the Go 4's colouring and microphone noise.
"""

import numpy as np
import pytest

from aurasync import estimulos, group_calibration, medicion
from aurasync.dsp import response
from aurasync.session import CAL_GAP_S, MIC_SLACK_S, Calibration
from aurasync.simulated import ROOM_COLOUR_DB, ROOM_NOISE

SR = 48000
BLOCK = 4096


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (3, [[0, 1, 2]]),
        (6, [[0, 1, 2, 3, 4, 5]]),
        (7, [[0, 1, 2, 3, 4], [0, 1, 2, 5, 6]]),
        (8, [[0, 1, 2, 3, 4, 5], [0, 1, 2, 6, 7]]),
    ],
)
def test_more_than_six_go_in_two_groups_sharing_the_first_three(n, expected):
    names = [f"s{i}" for i in range(n)]
    assert group_calibration.groups(names) == [[names[i] for i in g] for g in expected]


def test_up_to_six_the_calibration_plays_what_it_always_played():
    names = [f"s{i}" for i in range(6)]
    cal = Calibration(names, 10.0, 0.1, SR)
    tracks = estimulos.calibracion(6, 10.0, semilla=0)
    assert len(cal.groups) == 1
    for name, track in zip(names, tracks, strict=True):
        assert np.array_equal(cal.references[name], 0.1 * track)
    assert cal.total == cal.before + len(tracks[0]) + cal.after


def test_with_eight_each_group_plays_in_its_own_stretch():
    names = [f"s{i}" for i in range(8)]
    cal = Calibration(names, 2.0, 0.1, SR)
    (a0, a_len), (b0, b_len) = cal.segments
    assert b0 == a0 + a_len + int(CAL_GAP_S * SR)
    for name in names:
        ref = cal.references[name]
        in_a, in_b = np.any(ref[a0 : a0 + a_len]), np.any(ref[b0 : b0 + b_len])
        assert in_a == (name in cal.groups[0])
        assert in_b == (name in cal.groups[1])
        assert not np.any(ref[a0 + a_len : b0])
    assert cal.describe()["groups"] == cal.groups


def _room_recording(cal: Calibration, delays_ms, gains, rng) -> np.ndarray:
    """Play the calibration block by block, through a room, and record as the session does."""
    played = {n: [] for n in cal.references}
    while not cal.emitted:
        for n, x in cal.next_blocks(BLOCK).items():
            played[n].append(x)
    outputs = [np.concatenate(played[n]) for n in cal.references]
    total = len(outputs[0])
    k = 1 << int(np.ceil(np.log2(total + SR)))
    f = np.fft.rfftfreq(k, 1 / SR)
    colour = 10 ** (np.interp(np.log10(np.maximum(f, 1)), np.log10(response.THIRDS), ROOM_COLOUR_DB) / 20)
    latency_ms = 480.0  # pw-play, A2DP and the speaker, common to all
    mic = np.zeros(total)
    for x, d, g in zip(outputs, delays_ms, gains, strict=True):
        shift = np.exp(-2j * np.pi * f * (latency_ms + d) / 1000)
        mic += np.fft.irfft(np.fft.rfft(x, k) * colour * shift, k)[:total] * g
    mic += ROOM_NOISE * rng.standard_normal(total)
    return mic[int(MIC_SLACK_S * SR) :]


@pytest.mark.parametrize("room", [0, 1, 2])
def test_eight_speakers_in_two_groups_get_delays_and_gains_right(room):
    rng = np.random.default_rng(100 + room)
    names = [f"s{i}" for i in range(8)]
    delays = rng.uniform(0, 30, 8)
    gains = 10 ** (rng.uniform(-8, 0, 8) / 20)
    cal = Calibration(names, 10.0, 0.1, SR)
    result = cal._calibrate(_room_recording(cal, delays, gains, rng))  # noqa: SLF001
    assert result is not None
    assert result.confiable, result.estabilidad_ms
    truth_delay = delays.max() - delays
    truth_gain = 20 * np.log10(gains.min() / gains)
    got_delay = np.array([result.retardos_ms[n] for n in names])
    got_gain = np.array([result.ganancias_db[n] for n in names])
    assert np.max(np.abs(got_delay - truth_delay)) < 0.05, got_delay - truth_delay
    assert np.max(np.abs(got_gain - truth_gain)) < 1.0, got_gain - truth_gain


def test_joining_refers_both_groups_to_the_anchor():
    def cal(arrivals, levels):
        last = max(arrivals.values())
        return medicion.Calibracion(
            retardos_ms={n: last - a for n, a in arrivals.items()},
            ganancias_db={},
            estabilidad_ms=dict.fromkeys(arrivals, 0.1),
            desfase_grueso_ms=500.0,
            niveles=levels,
        )

    groups = [["a", "b", "c"], ["a", "d"]]
    # Group 2 was recorded with a different playback offset (+40 ms) and a louder room (x2).
    first = cal({"a": 10.0, "b": 13.0, "c": 4.0}, {"a": 1.0, "b": 0.5, "c": 0.8})
    second = cal({"a": 50.0, "d": 47.0}, {"a": 2.0, "d": 0.5})
    joined = group_calibration.join([first, second], groups)
    assert joined.retardos_ms == pytest.approx({"a": 3.0, "b": 0.0, "c": 9.0, "d": 6.0})
    assert joined.niveles == pytest.approx({"a": 1.0, "b": 0.5, "c": 0.8, "d": 0.25})
    assert group_calibration.join([first, None], groups) is None


def test_joining_averages_over_the_shared_speakers():
    """With three shared, one shared speaker measured 1 dB off in one group moves the second
    group by a third of it, not by all of it."""

    def cal(arrivals, levels):
        last = max(arrivals.values())
        return medicion.Calibracion(
            retardos_ms={n: last - a for n, a in arrivals.items()},
            ganancias_db={},
            estabilidad_ms=dict.fromkeys(arrivals, 0.1),
            desfase_grueso_ms=500.0,
            niveles=levels,
        )

    groups = [["a", "b", "c", "d"], ["a", "b", "c", "e"]]
    off = 10 ** (1 / 20)
    first = cal({"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0}, {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0})
    second = cal({"a": 30.0, "b": 31.0, "c": 32.0, "e": 34.0}, {"a": 2.0 * off, "b": 2.0, "c": 2.0, "e": 2.0})
    joined = group_calibration.join([first, second], groups)
    assert 20 * np.log10(joined.niveles["e"]) == pytest.approx(-1 / 3, abs=1e-9)
    assert joined.retardos_ms["e"] == pytest.approx(0.0)
    assert joined.retardos_ms["d"] == pytest.approx(1.0)
