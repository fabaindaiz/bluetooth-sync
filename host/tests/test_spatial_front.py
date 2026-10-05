"""The "frente intacto" render (research/14 §4, Logic7's principle): the front pair plays the
stereo untouched; every other speaker gets only the ambience, boosted and delayed. SIMULADO."""

import numpy as np
import pytest

from aurasync.dsp.spatial import SpatialParams, SpatialUpmix, front_pair

SR = 48000
BLOCK = 4096
RING3 = {"L": -60.0, "R": 60.0, "B": 180.0}


def _run(up, left, right):
    outs = {n: ([], []) for n in up.names}
    for i in range(0, len(left), BLOCK):
        for n, (d, a) in up.process(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            outs[n][0].append(d)
            outs[n][1].append(a)
    return {n: (np.concatenate(d), np.concatenate(a)) for n, (d, a) in outs.items()}


def _music(seconds=4.0, seed=0):
    """Two partly correlated channels: a centre source plus independent room on each side."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    centre = rng.standard_normal(n) * 0.1
    return centre + rng.standard_normal(n) * 0.05, centre + rng.standard_normal(n) * 0.05


def _front(params=None, ring=RING3, ambient=()):
    return SpatialUpmix(list(ring), ring, set(ambient), SR, params or SpatialParams(front_intact=True))


def test_the_front_pair_is_the_nearest_principal_on_each_side():
    assert front_pair({"L": -60.0, "R": 60.0, "B": 180.0}) == ("L", "R")
    assert front_pair({"a": -135.0, "b": -45.0, "c": 45.0, "d": 135.0}) == ("b", "c")
    assert front_pair({"only": 0.0}) is None
    assert front_pair({"a": -45.0, "b": -135.0}) is None  # nobody on the right


def test_the_front_pair_plays_the_input_exactly_once_settled():
    left, right = _music()
    out = _run(_front(), left, right)
    lat = 2048
    settle = SR  # past the fade-in
    got_l, got_r = out["L"][0][settle:], out["R"][0][settle:]
    want_l, want_r = left[settle - lat : len(left) - lat], right[settle - lat : len(right) - lat]
    assert np.max(np.abs(got_l - want_l)) < 1e-9
    assert np.max(np.abs(got_r - want_r)) < 1e-9
    # Nothing of the ambience path on the front, so the decorrelator never touches it.
    assert np.max(np.abs(out["L"][1][settle:])) == 0.0


def test_the_rest_get_only_ambience():
    left, right = _music()
    out = _run(_front(), left, right)
    direct_b, amb_b = out["B"]
    assert np.max(np.abs(direct_b)) == 0.0
    assert np.sum(amb_b[SR:] ** 2) > 0.0


def test_a_mono_input_leaves_the_rear_nearly_silent():
    s = np.random.default_rng(3).standard_normal(4 * SR) * 0.1
    out = _run(_front(), s, s)
    rear = np.sum(out["B"][1][SR:] ** 2)
    front = np.sum(out["L"][0][SR:] ** 2)
    assert 10 * np.log10(max(rear, 1e-30) / front) < -30


@pytest.mark.parametrize("db", [0.0, 6.0])
def test_the_ambience_level_raises_the_rear(db):
    left, right = _music()
    low = _run(_front(SpatialParams(front_intact=True, ambient_level_db=db)), left, right)
    high = _run(_front(SpatialParams(front_intact=True, ambient_level_db=db + 6.0)), left, right)
    ratio = np.sum(high["B"][1][SR:] ** 2) / np.sum(low["B"][1][SR:] ** 2)
    assert 10 * np.log10(ratio) == pytest.approx(6.0, abs=0.2)


def test_without_a_front_pair_it_falls_back_to_the_spatial_render():
    ring = {"a": -45.0, "b": -135.0}
    left, right = _music()
    out = _run(_front(ring=ring), left, right)
    assert np.sum(out["a"][0][SR:] ** 2) > 0.0  # it plays, as the spatial mode would
