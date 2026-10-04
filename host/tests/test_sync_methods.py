"""The robust least-squares sync estimator against a known truth (spec 2026-10-03 §4.2). SIMULADO.

Every criterion runs in two seeds and with two window lengths (CLAUDE.md: a true result
survives a change of a parameter that should not matter, and shows up twice).
"""

import dataclasses
import time

import pytest

from aurasync import sync_sim
from aurasync.sync_measurement import Measurement
from aurasync.sync_methods import SyncSettings, fit, suggested_delays

SEEDS = (0, 1)
WINDOWS = (10, 20)


def _errors(sc, seed, window, times, **settings):
    ms = sync_sim.measurements(sc, seed)
    s = SyncSettings(window_min=window, **settings)
    out = []
    for t in times:
        f = fit([m for m in ms if m.t <= t], s, t, sc.anchor_position)
        out.append((t, f, sync_sim.alignment_error(sc, suggested_delays(f), t, sc.anchor_position)))
    return out


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("window", WINDOWS)
def test_drift_is_followed(seed, window):
    sc = sync_sim.standard("drift")
    for t, _, err in _errors(sc, seed, window, [600, 1500, 2400, 3500]):
        assert err < 0.25, (t, err)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("window", WINDOWS)
def test_a_real_jump_is_believed_after_two(seed, window):
    sc = sync_sim.standard("jump")
    ms = sync_sim.measurements(sc, seed)
    s = SyncSettings(window_min=window)
    one = fit([m for m in ms if m.t <= 1200], s, 1200, "server")
    assert not one.jumps
    two = fit([m for m in ms if m.t <= 1220], s, 1220, "server")
    assert [j[1] for j in two.jumps] == ["s1"]
    assert two.jumps[0][2] == pytest.approx(6.52, abs=0.3)
    err = sync_sim.alignment_error(sc, suggested_delays(two), 1220, "server")
    assert err < 0.3


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("window", WINDOWS)
def test_a_lone_outlier_is_not_a_jump(seed, window):
    sc = sync_sim.standard("drift")
    sc.outlier_rate = 0.05
    for t, f, err in _errors(sc, seed, window, [900, 1800, 3000]):
        assert not f.jumps, (t, f.jumps)
        assert err < 0.3, (t, err)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("window", WINDOWS)
def test_partial_microphones_are_combined(seed, window):
    """The anchor measured once; two phones that each hear two of three follow the drift."""
    sc = sync_sim.standard("partial")
    for t, f, err in _errors(sc, seed, window, [900, 1800, 3500]):
        assert set(f.latency_ms) == set(sc.speakers)
        assert err < 0.3, (t, err)


def test_a_speaker_without_anchor_gets_no_suggestion():
    sc = sync_sim.standard("two_positions")
    sc.hears["server"] = {"s0", "s1"}
    ms = sync_sim.measurements(sc, 0)
    f = fit([m for m in ms if m.t <= 900], SyncSettings(), 900, "server")
    assert set(f.latency_ms) == {"s0", "s1"}
    assert "s2" in (f.reason or "")
    assert set(suggested_delays(f)) == {"s0", "s1"}


def test_one_speaker_is_not_a_measurement():
    m = Measurement("server", "server", "continuous", None, 1.0, 10.0, {"s0": 500.0})
    f = fit([m], SyncSettings(), 20.0, "server")
    assert f.latency_ms == {}
    assert f.reason
    assert f.rejected


def test_a_vote_moves_towards_its_spot():
    def shift(weight):
        sc = sync_sim.standard("drift")
        sc.anchor_every_s = None
        sc.positions["sofa"] = dict(sc.positions["server"])
        sc.positions["sofa"]["s0"] += 1.0
        sc.hears["sofa"] = set(sc.speakers)
        sc.votes = {"sofa": weight}
        ms = sync_sim.measurements(sc, 0)
        f = fit([m for m in ms if m.t <= 300], SyncSettings(point_vote_weight=weight), 300, "server")
        d = suggested_delays(f)
        return sync_sim.alignment_error(sc, d, 300, "server")

    assert shift(1) < shift(10)


@pytest.mark.parametrize("seed", SEEDS)
def test_biased_continuous_microphone_does_not_move_offsets(seed):
    sc = sync_sim.standard("biased")
    for t, _, err in _errors(sc, seed, 10, [900, 2400]):
        assert err < 0.25, (t, err)


def test_the_settings_refuse_out_of_range():
    with pytest.raises(ValueError, match="window_min"):
        SyncSettings().replace(window_min=0.5)
    with pytest.raises(ValueError, match="method"):
        SyncSettings().replace(method="magic")
    assert SyncSettings.from_dict(SyncSettings().to_dict(), print) == SyncSettings()


def test_a_method_not_built_yet_suggests_nothing_and_says_so():
    sc = sync_sim.standard("drift")
    ms = sync_sim.measurements(sc, 0)
    f = fit([m for m in ms if m.t <= 300], SyncSettings(method="kalman"), 300, "server")
    assert f.latency_ms == {}
    assert "kalman" in f.reason


def test_the_anchor_stays_on_the_target_spot():
    """A phone took a target and went on measuring there: its target row is superseded by the
    newer recordings, but the anchor is still the phone, not the server's microphone."""
    sc = sync_sim.standard("two_positions")
    ms = sync_sim.measurements(sc, 0)
    later = next(i for i, m in enumerate(ms) if m.position_id == "phone_a" and m.t >= 300)
    ms[later] = dataclasses.replace(ms[later], kind="point", role="target")
    f = fit([m for m in ms if m.t <= 1800], SyncSettings(), 1800, "server")
    assert f.anchor == "phone_a"
    assert sync_sim.alignment_error(sc, suggested_delays(f), 1800, "phone_a") < 0.25


def test_drift_is_reported_relative_to_the_others():
    sc = sync_sim.standard("drift")
    ms = sync_sim.measurements(sc, 0)
    f = fit([m for m in ms if m.t <= 1200], SyncSettings(), 1200, "server")
    assert f.drift_ppm["s0"] - f.drift_ppm["s1"] == pytest.approx(22.0, abs=1.0)
    assert f.drift_ppm["s2"] - f.drift_ppm["s1"] == pytest.approx(-15.0, abs=1.0)
    assert sum(f.drift_ppm.values()) == pytest.approx(0.0, abs=1e-6)


def test_many_speakers_jumping_at_once_do_not_crash_the_fit():
    """Review 2026-10-03: a jump found on the last pass was appended but never solved (KeyError)."""
    sc = sync_sim.standard("drift", 6)
    sc.jumps = [(300.0, s, 2.0 + 1.5 * i) for i, s in enumerate(sc.speakers)]
    ms = sync_sim.measurements(sc, 0)
    for t in (320, 340, 360, 400):
        fit([m for m in ms if m.t <= t], SyncSettings(), t, "server")


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("window", WINDOWS)
def test_a_real_jump_stays_believed_for_the_whole_window(seed, window):
    """Review 2026-10-03: jumps were rediscovered from scratch each fit and lost after two, leaving
    the suggestion up to 2 ms off for ~6 min. Carried from fit to fit, it holds the whole time."""
    sc = sync_sim.standard("jump")
    sc.jumps = [(300.0, "s1", 6.52)]
    ms = sync_sim.measurements(sc, seed)
    s = SyncSettings(window_min=window)
    known: tuple = ()
    for t in range(320, 300 + 900, 20):
        f = fit([m for m in ms if m.t <= t], s, t, "server", known_jumps=known)
        known = tuple(f.jumps)
        err = sync_sim.alignment_error(sc, suggested_delays(f), t, "server")
        assert err < 0.3, (t, err, f.jumps)


@pytest.mark.parametrize("weight", [1.0, 3.0, 10.0])
def test_a_vote_lands_at_its_weighted_mean(weight):
    """A vote of weight w against the anchor (weight 1) lands w/(1+w) of the way to its spot."""
    sc = sync_sim.standard("drift")
    sc.anchor_every_s = None
    sc.positions["sofa"] = dict(sc.positions["server"])
    sc.positions["sofa"]["s0"] += 1.0
    sc.hears["sofa"] = set(sc.speakers)
    sc.votes = {"sofa": weight}
    ms = sync_sim.measurements(sc, 0)
    f = fit([m for m in ms if m.t <= 300], SyncSettings(point_vote_weight=weight), 300, "server")
    d = suggested_delays(f)
    at_anchor = sync_sim.alignment_error(sc, d, 300, "server")
    assert at_anchor == pytest.approx(weight / (1 + weight), abs=0.05)


@pytest.mark.parametrize("name", ["jump_repeats", "min_speakers"])
def test_integer_settings_refuse_fractions(name):
    with pytest.raises(ValueError, match=name):
        SyncSettings().replace(**{name: 2.5})
    warnings: list[str] = []
    assert getattr(SyncSettings.from_dict({name: 2.5}, warnings.append), name) == getattr(SyncSettings(), name)
    assert warnings


def test_a_fit_over_eight_speakers_is_cheap():
    sc = sync_sim.standard("two_positions", 8)
    ms = [m for m in sync_sim.measurements(sc, 0) if m.t <= 600]
    start = time.perf_counter()
    fit(ms, SyncSettings(), 600, "server")
    assert time.perf_counter() - start < 0.5
