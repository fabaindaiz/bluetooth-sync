"""The simulator the sync estimator is tested and explained with (spec 2026-10-03 §7). SIMULADO."""

import itertools

import numpy as np
import pytest

from aurasync import sync_sim


def test_true_latency_follows_drift_and_jumps():
    sc = sync_sim.standard("jump")
    assert sync_sim.true_latency(sc, "s0", 1000.0) - sync_sim.true_latency(sc, "s0", 0.0) == pytest.approx(
        22e-6 * 1000 * 1000, rel=1e-9
    )
    before = sync_sim.true_latency(sc, "s1", 1199.0)
    after = sync_sim.true_latency(sc, "s1", 1201.0)
    assert after - before == pytest.approx(6.52, abs=1e-6)


def test_partial_hears_only_its_subset():
    sc = sync_sim.standard("partial")
    for m in sync_sim.measurements(sc, seed=0):
        assert m.heard == frozenset(sc.hears[m.position_id])


def test_common_offset_cancels_in_differences():
    sc = sync_sim.standard("drift")
    for m in sync_sim.measurements(sc, seed=0)[:20]:
        names = sorted(m.heard)
        for a, b in itertools.pairwise(names):
            got = m.arrivals_ms[a] - m.arrivals_ms[b]
            want = (
                sync_sim.true_latency(sc, a, m.t)
                + sc.positions[m.position_id][a]
                - sync_sim.true_latency(sc, b, m.t)
                - sc.positions[m.position_id][b]
            )
            assert got == pytest.approx(want, abs=6 * sc.noise_ms)


def test_seeds_differ_and_repeat():
    sc = sync_sim.standard("drift")
    a, b, c = sync_sim.measurements(sc, 0), sync_sim.measurements(sc, 0), sync_sim.measurements(sc, 1)
    assert a == b
    assert a != c


def test_the_anchor_is_a_target_point_first():
    sc = sync_sim.standard("partial")
    ms = sync_sim.measurements(sc, 0)
    anchor = [m for m in ms if m.position_id == sc.anchor_position]
    assert anchor[0].kind == "point"
    assert anchor[0].role == "target"
    assert len(anchor) == 1  # `partial`: the anchor measures once, the partial microphones go on


def test_alignment_error_is_zero_for_the_true_delays():
    sc = sync_sim.standard("drift")
    t = 600.0
    lat = {s: sync_sim.true_latency(sc, s, t) + sc.positions[sc.anchor_position][s] for s in sc.speakers}
    delays = {s: max(lat.values()) - v for s, v in lat.items()}
    assert sync_sim.alignment_error(sc, delays, t, sc.anchor_position) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("name", ["drift", "jump", "partial", "two_positions", "moved", "biased"])
def test_every_standard_scenario_builds(name):
    sc = sync_sim.standard(name, 8)
    ms = sync_sim.measurements(sc, 0)
    assert ms
    assert all(np.isfinite(list(m.arrivals_ms.values())).all() for m in ms)
