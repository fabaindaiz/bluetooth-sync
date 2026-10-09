import numpy as np
import pytest

from aurasync.dsp.transition import SHAPES, Crossfaded, Transition, fade_weights

LENGTH = 3840


def _all_weights(shape: str, block: int = 1000) -> tuple[np.ndarray, np.ndarray]:
    old, new = [], []
    for pos in range(0, LENGTH, block):
        o, n = fade_weights(shape, pos, min(block, LENGTH - pos), LENGTH)
        old.append(o)
        new.append(n)
    return np.concatenate(old), np.concatenate(new)


def test_equal_gain_weights_sum_to_one():
    old, new = _all_weights("equal_gain")
    assert len(old) == LENGTH
    assert np.allclose(old + new, 1.0, atol=1e-12)


def test_equal_power_squares_sum_to_one():
    old, new = _all_weights("equal_power")
    assert np.allclose(old**2 + new**2, 1.0, atol=1e-12)


@pytest.mark.parametrize("shape", SHAPES)
def test_weights_are_clamped_after_the_end(shape):
    old, new = fade_weights(shape, LENGTH - 2, 6, LENGTH)
    assert old[1:] == pytest.approx(0.0, abs=1e-12)
    assert np.all(new[1:] == 1.0)


def test_unknown_shape_is_an_error():
    with pytest.raises(ValueError, match="unknown fade shape"):
        fade_weights("linear", 0, 10, LENGTH)


def test_request_while_idle_starts_now():
    t = Transition()
    assert not t.busy
    assert t.request(lambda: None) is True


def test_requests_before_the_first_block_join_the_transition():
    t = Transition()
    a, b, c = (lambda: None), (lambda: None), (lambda: None)
    assert t.request(a) is True
    t.begin(LENGTH)
    assert t.request(b) is False
    assert t.request(c) is False
    assert t.take_starting() == [a, b, c]
    assert t.take_starting() == []


def test_fifty_requests_during_a_fade_make_one_pending_batch():
    t = Transition()
    t.request(None)
    t.begin(LENGTH)
    t.take_starting()
    assert t.advance(1) is None
    assert t.started
    actions = [(lambda i=i: i) for i in range(50)]
    assert not any(t.request(a) for a in actions)
    assert t.advance(LENGTH) == actions
    assert not t.busy
    assert t.advance(LENGTH) is None


def test_the_end_without_pending_returns_an_empty_batch():
    t = Transition()
    t.request(None)
    t.begin(100)
    assert t.advance(100) == []


def test_pending_batch_starts_the_next_transition():
    t = Transition()
    t.request(None)
    t.begin(100)
    t.advance(10)
    a = lambda: None  # noqa: E731
    t.request(a)
    batch = t.advance(100)
    t.begin(100, batch)
    assert t.take_starting() == [a]


def test_cancel_returns_every_queued_action_and_idles():
    t = Transition()
    a, b = (lambda: None), (lambda: None)
    t.request(a)
    t.begin(LENGTH)
    t.advance(1)
    t.request(b)
    assert t.cancel() == [a, b]
    assert not t.busy
    assert t.advance(LENGTH) is None


def test_equal_gain_is_cos_squared():
    old, new = fade_weights("equal_gain", 0, 4, 4)  # t = 0.25, 0.5, 0.75, 1
    assert old[0] == pytest.approx(np.cos(np.pi / 8) ** 2)
    assert old[0] == pytest.approx(0.853553, abs=1e-6)
    assert old[1] == pytest.approx(0.5)
    assert new[1] == pytest.approx(0.5)


def test_an_action_after_take_starting_waits_in_the_pending_batch():
    """A request that lands between `take_starting` and the first `advance` (re-entered from inside
    the first block: an action or a callback asking for a change) missed the start: it runs with
    the next transition, not never."""
    t = Transition()
    t.request(None)
    t.begin(100)
    assert t.take_starting() == []
    late = lambda: None  # noqa: E731
    assert t.request(late) is False
    assert t.advance(10) is None
    assert t.advance(100) == [late]


def test_a_bare_request_during_a_fade_still_asks_for_the_next_transition():
    """A request with no action (the installation already changed, as a slow control delay does)
    must still start one more transition at the end, or its targets would never be reached."""
    t = Transition()
    t.request(None)
    t.begin(100)
    t.advance(10)
    assert t.request(None) is False
    batch = t.advance(100)
    assert batch
    assert [action() for action in batch] == [None]


def _warm_transition(samples: int = 1000, length: int = LENGTH) -> tuple[Transition, list]:
    t = Transition()
    pending_action = lambda: None  # noqa: E731
    t.request(lambda: t.need_warm(samples))
    t.begin(length)
    for action in t.take_starting():
        action()
    return t, [pending_action]


def test_warm_then_fade_then_idle():
    t, (later,) = _warm_transition(1000)
    assert t.state == Transition.WARM
    assert t.advance(400) is None
    t.request(later)  # during WARM the transition has started: the pending batch
    assert t.advance(400) is None
    assert t.advance(400) is Transition.FADE_STARTS
    assert t.state == Transition.FADE
    assert t.advance(LENGTH - 1) is None
    assert t.advance(1) == [later]
    assert not t.busy


def test_need_warm_is_capped_at_one_second():
    t, _ = _warm_transition(10**6)
    assert t.advance(47999) is None
    assert t.advance(1) is Transition.FADE_STARTS
    t2 = Transition(sr=16000)
    t2.request(lambda: t2.need_warm(10**6))
    t2.begin(100)
    t2.take_starting()[0]()
    assert t2.advance(15999) is None
    assert t2.advance(1) is Transition.FADE_STARTS


def test_block_weights_in_each_phase():
    t = Transition()
    assert t.block_weights(100, "equal_gain") is None
    t, _ = _warm_transition(1000)
    assert t.block_weights(100, "equal_gain") == (1.0, 0.0)
    t.advance(1000)
    old, new = t.block_weights(100, "equal_gain")
    ref_old, ref_new = fade_weights("equal_gain", 0, 100, LENGTH)
    assert np.array_equal(old, ref_old)
    assert np.array_equal(new, ref_new)
    t.advance(100)  # pure: asking did not move, advancing did
    assert t.block_weights(100, "equal_gain")[0] == pytest.approx(fade_weights("equal_gain", 100, 100, LENGTH)[0])


def test_no_warm_is_stage_1():
    t = Transition()
    t.request(None)
    t.begin(100)
    assert t.take_starting() == []
    assert t.state == Transition.FADE
    assert t.advance(60) is None
    assert t.advance(40) == []


class _Gain:
    """A stateful fake stage: counts calls, returns a constant array."""

    def __init__(self, value: float) -> None:
        self.value, self.calls = value, 0
        self.label = f"gain{value}"

    def process(self, x):
        self.calls += 1
        return np.full_like(x, self.value)

    def nothing(self, _x):
        self.calls += 1


def test_crossfaded_mixes_arrays_and_reads_attributes_from_new():
    old, new = _Gain(1.0), _Gain(3.0)
    cf = Crossfaded(old, new, lambda: (0.25, 0.75))
    out = cf.process(np.zeros(4))
    assert np.allclose(out, 0.25 * 1.0 + 0.75 * 3.0)
    assert cf.label == "gain3.0"
    assert cf.resolve() is new
    assert cf.old is old
    assert cf.nothing(np.zeros(2)) is None
    per_sample = Crossfaded(old, new, lambda: (np.array([1.0, 0.5]), np.array([0.0, 0.5])))
    assert np.allclose(per_sample.process(np.zeros(2)), [1.0, 2.0])


def test_crossfaded_runs_new_during_warm():
    old, new = _Gain(1.0), _Gain(9.0)
    t, _ = _warm_transition(1000)
    cf = Crossfaded(old, new, lambda: t.block_weights(100, "equal_gain"))
    out = cf.process(np.zeros(100))
    assert np.allclose(out, 1.0)  # only the old is heard
    assert (old.calls, new.calls) == (1, 1)  # but the new one ran
    idle = Crossfaded(old, new, lambda: None)
    assert np.allclose(idle.process(np.zeros(3)), 9.0)
    assert (old.calls, new.calls) == (1, 2)


def test_crossfaded_refuses_nesting():
    a, b, c = _Gain(1), _Gain(2), _Gain(3)
    inner = Crossfaded(a, b, lambda: None)
    with pytest.raises(ValueError, match="nest"):
        Crossfaded(inner, c, lambda: None)
    with pytest.raises(ValueError, match="nest"):
        Crossfaded(c, inner, lambda: None)
