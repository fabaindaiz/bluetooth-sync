"""The loudness match as a pure unit (loudness_match.py): a makeup gain per mode that follows
`reference - candidate` slowly, capped, frozen in silence and on request, remembered per mode."""

import itertools
import math

import pytest

from aurasync.loudness_match import CAP_DB, LoudnessMatch

STEP_S = 0.1


def _run(match: LoudnessMatch, reference: float, candidate: float | None, seconds: float, **kw) -> list[tuple]:
    return [match.update(reference, candidate, STEP_S, **kw) for _ in range(round(seconds / STEP_S))]


def test_it_follows_the_difference_slowly_and_settles_on_it():
    m = LoudnessMatch()
    assert m.select("mix", 0.0) == 0.0
    _run(m, -20.0, -30.0, 1.0)
    assert 0.8 < m.current_db < 1.1, "about 10 % of a 10 dB error in the first second"
    assert m.status == "measuring"
    _run(m, -20.0, -30.0, 60.0)
    assert m.current_db == pytest.approx(10.0, abs=0.05)
    assert m.status == "locked"


def test_each_block_is_a_ramp_that_starts_where_the_last_one_ended():
    m = LoudnessMatch()
    m.select("mix", 0.0)
    ramps = _run(m, -20.0, -30.0, 5.0)
    for (_, end), (start, _) in itertools.pairwise(ramps):
        assert start == end
    assert max(abs(b - a) for a, b in ramps) < 0.11, "no step larger than 10 %/s of the error over 0.1 s"


def test_the_makeup_is_capped():
    m = LoudnessMatch()
    m.select("mix", 0.0)
    _run(m, -20.0, -50.0, 120.0)
    assert m.current_db == pytest.approx(CAP_DB, abs=0.01)
    assert m.current_db <= CAP_DB
    assert CAP_DB == 12.0
    m.select("binaural", 0.0)
    _run(m, -20.0, 10.0, 120.0)
    assert m.current_db == pytest.approx(-CAP_DB, abs=0.01)


def test_an_estimate_beyond_the_cap_is_capped():
    m = LoudnessMatch()
    assert m.select("binaural", 30.0) == CAP_DB


def test_a_pause_freezes_it():
    m = LoudnessMatch()
    m.select("mix", 3.0)
    _run(m, -70.0, -75.0, 10.0)
    assert m.current_db == 3.0
    assert m.status == "frozen"
    _run(m, -math.inf, -math.inf, 10.0)
    assert m.current_db == 3.0


def test_the_gate_can_be_a_faster_reading_than_the_reference():
    """The monitor gates on the momentary loudness: a pause freezes it in 0.4 s, not 3 s."""
    m = LoudnessMatch()
    m.select("mix", 3.0)
    _run(m, -20.0, -30.0, 2.0, gate_lufs=-80.0)
    assert m.current_db == 3.0
    assert m.status == "frozen"


def test_a_hold_freezes_it():
    m = LoudnessMatch()
    m.select("mix", 1.0)
    _run(m, -20.0, -30.0, 5.0, hold=True)
    assert m.current_db == 1.0
    assert m.status == "frozen"


def test_a_silent_candidate_under_a_loud_reference_freezes_it():
    """Every speaker muted: there is nothing to match, and +12 dB would be waiting at unmute."""
    m = LoudnessMatch()
    m.select("mix", 1.0)
    _run(m, -20.0, -math.inf, 5.0)
    assert m.current_db == 1.0
    assert m.status == "frozen"


def test_without_a_candidate_it_is_unmeasured():
    m = LoudnessMatch()
    m.select("binaural", 0.0)
    _run(m, -20.0, None, 5.0)
    assert m.current_db == 0.0
    assert m.status == "unmeasured"


def test_each_mode_keeps_its_own_makeup():
    m = LoudnessMatch()
    m.select("mix", 0.0)
    _run(m, -20.0, -26.0, 90.0)
    mix = m.current_db
    assert mix == pytest.approx(6.0, abs=0.05)
    assert m.select("stereo", 0.0) == 0.0
    _run(m, -20.0, -20.0, 5.0)
    assert m.select("mix", -9.0) == mix, "a mode seen before comes back at its remembered makeup, not the estimate"
    assert m.makeup == {"mix": mix, "stereo": 0.0}


def test_forget_drops_what_was_remembered():
    m = LoudnessMatch()
    m.select("mix", 0.0)
    _run(m, -20.0, -26.0, 30.0)
    m.forget()
    assert m.select("mix", -1.0) == -1.0


def test_with_a_gate_the_floor_is_on_the_gate_not_on_the_reference():
    """The monitor's reference is the input at the chosen volume; the gate is the input before it.
    At -40 dB of volume the reference sits under the floor while the music plays."""
    m = LoudnessMatch()
    m.select("mix", 0.0)
    _run(m, -54.0, -60.0, 5.0, gate_lufs=-14.0)
    assert m.status == "measuring"
    assert m.current_db > 0.0
