"""The spatial renderer: per-bin panning to a ring of principals, ambience to the ambients
(spec 2026-10-04 §3). SIMULADO: synthetic sources with a known pan."""

import time

import numpy as np
import pytest

from aurasync.dsp.spatial import SpatialParams, SpatialUpmix, from_character

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


def _energy(x):
    return float(np.sum(x[SR:] ** 2))  # past the latency and the smoothing


def _panned(phi, seconds=4.0, seed=0):
    s = np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.1
    return np.cos(phi) * s, np.sin(phi) * s


def _db(a, b):
    return 10 * np.log10(max(a, 1e-30) / max(b, 1e-30))


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.parametrize(
    ("phi", "nearest", "farthest"), [(0.0, "L", "B"), (np.pi / 2, "R", "B"), (np.pi / 4, "L", "B")]
)
def test_a_source_comes_out_at_its_angle(seed, phi, nearest, farthest):
    up = SpatialUpmix(list(RING3), RING3, set(), SR, SpatialParams(arc_deg=60.0))
    out = _run(up, *_panned(phi, seed=seed))
    total = {n: _energy(d + a) for n, (d, a) in out.items()}
    assert _db(total[nearest], total[farthest]) >= 10, total


def test_wider_arc_moves_a_hard_left_source_back():
    left, right = _panned(0.0)
    narrow = _run(SpatialUpmix(list(RING3), RING3, set(), SR, SpatialParams(arc_deg=60.0)), left, right)
    wide = _run(SpatialUpmix(list(RING3), RING3, set(), SR, SpatialParams(arc_deg=150.0)), left, right)
    assert _energy(wide["B"][0]) > _energy(wide["L"][0])
    assert _energy(narrow["L"][0]) > _energy(narrow["B"][0])


@pytest.mark.parametrize("seed", [0, 1])
def test_ambient_feeds_are_decorrelated_enough(seed):
    rng = np.random.default_rng(seed)
    left, right = rng.standard_normal(4 * SR) * 0.1, rng.standard_normal(4 * SR) * 0.1  # diffuse
    angles = {"L": -60.0, "R": 60.0}
    up = SpatialUpmix(["L", "R", "A1", "A2"], angles, {"A1", "A2"}, SR, SpatialParams(ambience=1.0))
    out = _run(up, left, right)
    a1, a2 = out["A1"][1][SR:], out["A2"][1][SR:]
    assert _energy(out["A1"][1]) > 0
    assert abs(np.corrcoef(a1, a2)[0, 1]) < 0.3
    assert not np.any(out["A1"][0])  # an ambient speaker gets no direct sound


def test_more_character_more_ambience():
    rng = np.random.default_rng(3)
    common = rng.standard_normal(4 * SR)
    left = 0.1 * (common + 0.8 * rng.standard_normal(4 * SR))
    right = 0.1 * (common + 0.8 * rng.standard_normal(4 * SR))
    angles = {"L": -60.0, "R": 60.0}
    shares = []
    for c in (0.0, 0.5, 1.0):
        out = _run(SpatialUpmix(["L", "R", "A"], angles, {"A"}, SR, from_character(c)), left, right)
        amb = _energy(out["A"][1])
        total = sum(_energy(d + a) for d, a in out.values())
        shares.append(amb / total)
    assert shares[0] < shares[1] < shares[2], shares


@pytest.mark.parametrize("arc", [90.0, 105.0, 150.0])
def test_two_principals_do_not_wrap_through_the_back(arc):
    """Two principals at ±90° and one ambient: with nobody behind, a hard-left source must not go
    "around the back" and leak into the right speaker (it did at 17.6 dB of separation)."""
    up = SpatialUpmix(["L", "R", "A"], {"L": -90.0, "R": 90.0}, {"A"}, SR, SpatialParams(arc_deg=arc))
    out = _run(up, *_panned(0.0))
    assert _db(_energy(out["L"][0]), _energy(out["R"][0])) >= 30


def test_full_character_takes_real_ambience():
    """A mix with coherence ~0.75 (typical of music with some room): at character 1 a clear share
    goes as ambience; the extractor's default threshold (0.5) let almost nothing through."""
    rng = np.random.default_rng(7)
    common = rng.standard_normal(4 * SR)
    left = 0.1 * (common + 0.6 * rng.standard_normal(4 * SR))
    right = 0.1 * (common + 0.6 * rng.standard_normal(4 * SR))
    out = _run(SpatialUpmix(["L", "R", "A"], {"L": -60.0, "R": 60.0}, {"A"}, SR, from_character(1.0)), left, right)
    amb = _energy(out["A"][1])
    total = sum(_energy(d + a) for d, a in out.values())
    assert amb / total > 0.10, amb / total


def test_from_character_ends():
    lo, hi = from_character(0.0), from_character(1.0)
    assert (lo.arc_deg, lo.ambience, lo.ambient_level_db, lo.haas_ms) == (150.0, 0.2, 0.0, 8.0)
    assert (hi.arc_deg, hi.ambience, hi.ambient_level_db, hi.haas_ms) == (60.0, 0.8, 6.0, 20.0)


def test_mono_silence_one_channel():
    up = SpatialUpmix(list(RING3), RING3, set(), SR)
    s = np.random.default_rng(4).standard_normal(2 * SR) * 0.1
    for left, right in ((s, s), (np.zeros(2 * SR), np.zeros(2 * SR)), (s, np.zeros(2 * SR))):
        out = _run(up, left, right)
        for d, a in out.values():
            assert np.all(np.isfinite(d))
            assert np.all(np.isfinite(a))
    silent = _run(SpatialUpmix(list(RING3), RING3, set(), SR), np.zeros(SR), np.zeros(SR))
    assert all(not np.any(d) and not np.any(a) for d, a in silent.values())


def test_all_ambient_still_plays():
    up = SpatialUpmix(["A", "B"], {}, {"A", "B"}, SR)
    out = _run(up, *_panned(np.pi / 4))
    assert all(_energy(d + a) > 0 for d, a in out.values())


@pytest.mark.parametrize("character", [0.0, 0.5, 1.0])
def test_spatial_keeps_the_loudness(character):
    rng = np.random.default_rng(5)
    common = rng.standard_normal(4 * SR)
    left = 0.1 * (common + 0.5 * rng.standard_normal(4 * SR))
    right = 0.1 * (common + 0.5 * rng.standard_normal(4 * SR))
    up = SpatialUpmix(["L", "R", "B", "A"], {"L": -60.0, "R": 60.0, "B": 180.0}, {"A"}, SR, from_character(character))
    out = _run(up, left, right)
    total = sum(_energy(d) + _energy(a) for d, a in out.values())
    assert abs(_db(total, _energy(left) + _energy(right))) < 1.0


@pytest.mark.parametrize(("n", "limit_ms"), [(3, 4.0), (8, 8.0)])
def test_cost_per_block_is_flat(n, limit_ms):
    names = [f"s{i}" for i in range(n)]
    angles = {nm: -180 + 360 * (i + 0.5) / n for i, nm in enumerate(names)}
    up = SpatialUpmix(names, angles, set(), SR)
    rng = np.random.default_rng(6)
    times = []
    for _ in range(40):
        left, right = rng.standard_normal(BLOCK), rng.standard_normal(BLOCK)
        t0 = time.perf_counter()
        up.process(left, right)
        times.append((time.perf_counter() - t0) * 1000)
    assert np.median(times[5:]) < limit_ms, np.median(times)
    assert np.median(times[-10:]) < 1.5 * np.median(times[5:15]) + 0.5  # flat


def test_a_haas_change_crossfades():
    """Review 2026-10-04: a live Haas change jumped the read point of the delay line (a click)."""
    up = SpatialUpmix(["A"], {}, {"A"}, SR, SpatialParams(haas_ms=8.0))
    t = np.arange(4 * BLOCK) / SR
    tone = 0.1 * np.sin(2 * np.pi * 300 * t)
    out = []
    for k, i in enumerate(range(0, len(tone), BLOCK)):
        if k == 2:
            up.set_params(SpatialParams(haas_ms=20.0))
        out.append(up._delay("A", tone[i : i + BLOCK]))  # noqa: SLF001
    d2 = np.abs(np.diff(np.concatenate(out), 2))
    assert d2[2 * BLOCK - 4 : 3 * BLOCK].max() < 3 * d2[BLOCK : 2 * BLOCK - 4].max()


def test_a_new_renderer_fades_in():
    up = SpatialUpmix(list(RING3), RING3, set(), SR)
    t = np.arange(4 * BLOCK) / SR
    tone = 0.1 * np.sin(2 * np.pi * 300 * t)
    out = np.concatenate(
        [up.process(tone[i : i + BLOCK], tone[i : i + BLOCK])["L"][0] for i in range(0, len(tone), BLOCK)]
    )
    d2 = np.abs(np.diff(out, 2))
    assert d2[: 2 * BLOCK].max() < 3 * d2[3 * BLOCK :].max()


def test_a_layout_change_keeps_the_latency():
    """Review 2026-10-04: `set_layout` reset the output buffers and added 1536-2047 samples."""
    up = SpatialUpmix(list(RING3), RING3, set(), SR)

    def impulse_at(up):
        x = np.zeros(4 * BLOCK)
        x[100] = 1.0
        out = np.concatenate([up.process(x[i : i + BLOCK], x[i : i + BLOCK])["L"][0] for i in range(0, len(x), BLOCK)])
        return int(np.argmax(np.abs(out))) - 100

    first = impulse_at(up)
    up.set_layout({"L": -50.0, "R": 60.0, "B": 180.0}, set())
    assert impulse_at(up) == first == up.latency


def test_latency_is_the_extractor_s():
    up = SpatialUpmix(list(RING3), RING3, set(), SR)
    assert up.latency == 2048
