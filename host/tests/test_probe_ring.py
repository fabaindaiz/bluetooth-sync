"""What the server keeps of the probe it sent, so a phone can measure against it (spec 2026-10-03
§6.2): a fixed ring per speaker, with the server time of each sample."""

import numpy as np
import pytest

from aurasync.probe_ring import ProbeRing, RingError

SR = 48000
BLOCK = 4096


def _fill(ring, seconds, t0=100.0, names=("a", "b")):
    rng = np.random.default_rng(0)
    sent = {n: [] for n in names}
    t = t0
    for _ in range(int(seconds * SR / BLOCK)):
        block = {n: rng.standard_normal(BLOCK) * 0.01 for n in names}
        ring.write(t, block)
        for n in names:
            sent[n].append(block[n])
        t += BLOCK / SR
    return {n: np.concatenate(v) for n, v in sent.items()}, t


def test_a_span_comes_back_as_it_was_sent():
    ring = ProbeRing(["a", "b"], SR, keep_s=10)
    sent, _ = _fill(ring, 4)
    start = 100.0 + 1.0
    ref = ring.reference(start, 2.0)
    i = round(1.0 * SR)
    assert np.allclose(ref["a"], sent["a"][i : i + 2 * SR])
    assert np.allclose(ref["b"], sent["b"][i : i + 2 * SR])


def test_what_is_older_than_the_ring_is_gone():
    ring = ProbeRing(["a", "b"], SR, keep_s=2)
    _fill(ring, 5)
    with pytest.raises(RingError, match="gone"):
        ring.reference(100.0, 1.0)


def test_what_was_not_played_yet_is_refused():
    ring = ProbeRing(["a", "b"], SR, keep_s=10)
    _, end = _fill(ring, 2)
    with pytest.raises(RingError, match="not played yet"):
        ring.reference(end - 0.5, 1.0)


def test_writing_is_fixed_size():
    ring = ProbeRing(["a"], SR, keep_s=3)
    size = ring.nbytes
    _fill(ring, 20, names=("a",))
    assert ring.nbytes == size


def test_a_speaker_without_probe_in_a_block_is_silence():
    ring = ProbeRing(["a", "b"], SR, keep_s=10)
    ring.write(100.0, {"a": np.ones(BLOCK)})
    ring.write(100.0 + BLOCK / SR, {"a": np.ones(BLOCK)})
    ref = ring.reference(100.0, BLOCK / SR)
    assert np.allclose(ref["b"], 0.0)
    assert np.allclose(ref["a"], 1.0)
