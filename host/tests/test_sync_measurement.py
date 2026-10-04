"""One shape for every measurement the sync estimator takes (spec 2026-10-03 §4.1)."""

import math

import pytest

from aurasync.sync_measurement import MAX_AGE_S, Measurement, MeasurementError

SPEAKERS = {"Red", "Black", "Blue"}
NOW = 10_000.0


def _data(**over):
    data = {
        "source_id": "phone-1",
        "position_id": "phone-1@sofa",
        "kind": "point",
        "role": "target",
        "weight": 1.0,
        "t": NOW - 5,
        "arrivals_ms": {"Red": 512.3, "Black": 515.1},
        "halves_ms": {"Red": [512.31, 512.29], "Black": [515.12, 515.08]},
        "levels_db": {"Red": -21.0, "Black": -24.5},
        "quality": {"echo_cancellation": False, "noise_suppression": False, "auto_gain_control": False},
        "origin": "browser",
    }
    data.update(over)
    return data


def test_round_trip():
    m = Measurement.from_dict(_data(), SPEAKERS, NOW)
    assert Measurement.from_dict(m.to_dict(), SPEAKERS, NOW) == m
    assert m.heard == frozenset({"Red", "Black"})


def test_unknown_speaker_is_rejected():
    with pytest.raises(MeasurementError) as caught:
        Measurement.from_dict(_data(arrivals_ms={"Red": 1.0, "Green": 2.0}), SPEAKERS, NOW)
    assert caught.value.code == "out_of_range"


def test_point_needs_a_role():
    with pytest.raises(MeasurementError) as caught:
        Measurement.from_dict(_data(role=None), SPEAKERS, NOW)
    assert caught.value.code == "bad_request"


def test_continuous_has_no_role():
    with pytest.raises(MeasurementError) as caught:
        Measurement.from_dict(_data(kind="continuous", role="target"), SPEAKERS, NOW)
    assert caught.value.code == "bad_request"


def test_too_old_is_rejected():
    with pytest.raises(MeasurementError) as caught:
        Measurement.from_dict(_data(t=NOW - MAX_AGE_S - 1), SPEAKERS, NOW)
    assert caught.value.code == "out_of_range"


def test_one_speaker_is_kept_but_flagged():
    """The estimator, not the format, decides that one speaker is not a measurement."""
    m = Measurement.from_dict(_data(arrivals_ms={"Red": 512.3}, halves_ms={}, levels_db={}), SPEAKERS, NOW)
    assert m.heard == frozenset({"Red"})


def test_non_finite_arrival_is_rejected():
    with pytest.raises(MeasurementError) as caught:
        Measurement.from_dict(_data(arrivals_ms={"Red": math.nan, "Black": 1.0}), SPEAKERS, NOW)
    assert caught.value.code == "out_of_range"


def test_unknown_field_is_rejected():
    with pytest.raises(MeasurementError) as caught:
        Measurement.from_dict(_data(gains_db={"Red": 0.0}), SPEAKERS, NOW)
    assert caught.value.code == "unknown_field"
