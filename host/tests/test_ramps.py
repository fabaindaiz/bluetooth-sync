import numpy as np
import pytest

from aurasync.dsp.ramps import DecibelRamp, FadeGate, Smoothed

SR = 48000


def _run(param, n_blocks: int, block: int = 1024) -> np.ndarray:
    return np.concatenate([np.broadcast_to(param.block(block), (block,)) for _ in range(n_blocks)])


def test_smoothed_reaches_its_target_at_the_rate():
    s = Smoothed(0.0, 2.0, SR)
    s.target = 1.0
    values = _run(s, 30)  # 0.64 s
    # 0 -> 1 at 2 units/s takes 0.5 s.
    first = int(np.argmax(values >= 1.0))
    assert abs(first - SR * 0.5) <= 1
    assert s.settled


def test_smoothed_never_steps_more_than_its_rate():
    s = Smoothed(-1.0, 2.0, SR)
    s.target = 1.0
    values = np.concatenate([[-1.0], _run(s, 50)])
    assert np.max(np.abs(np.diff(values))) <= 2.0 / SR + 1e-12


def test_smoothed_is_a_scalar_at_rest():
    s = Smoothed(0.3, 2.0, SR)
    assert isinstance(s.block(512), float)


def test_smoothed_rejects_a_non_positive_rate():
    with pytest.raises(ValueError, match="positive"):
        Smoothed(0.0, 0.0)


def test_decibel_ramp_moves_linearly_in_db():
    r = DecibelRamp(0.0, 30.0, SR)
    r.target_db = -30.0
    gains = _run(r, 50)  # ~1.07 s
    db = 20 * np.log10(gains)
    assert abs(int(np.argmax(db <= -30.0)) - SR) <= 2
    assert np.max(np.abs(np.diff(db))) <= 30.0 / SR + 1e-9


def test_fade_reaches_exactly_zero_when_the_jump_happens():
    gate = FadeGate(SR)
    gate.request()
    jumps = []
    envelope = []
    for _ in range(20):
        env, jump = gate.block(1000)
        envelope.append(np.broadcast_to(env, (1000,)))
        if jump:
            jumps.append(len(envelope))
            assert np.broadcast_to(env, (1000,))[-1] == 0.0
    env = np.concatenate(envelope)
    assert len(jumps) == 1
    # Fully down and back up: the last sample is back at one, and nothing moved faster than
    # the raised cosine allows.
    assert env[-1] == 1.0
    assert not gate.busy
    assert np.max(np.abs(np.diff(env))) < 1e-3


def test_fade_that_ends_mid_block_keeps_the_rest_of_the_block_silent():
    gate = FadeGate(SR, fade_ms=10)  # 480 samples
    gate.request()
    env, jump = gate.block(1000)
    assert jump
    assert np.all(env[480:] == 0.0)


def test_a_second_request_during_the_fade_in_turns_around_without_a_step():
    gate = FadeGate(SR, fade_ms=10)
    gate.request()
    gate.block(480)  # down, jump
    up, _ = gate.block(200)
    gate.request()
    down, jump = gate.block(100)
    assert abs(down[0] - up[-1]) < 0.02
    assert not jump


def test_an_idle_gate_costs_nothing():
    env, jump = FadeGate(SR).block(4096)
    assert env == 1.0
    assert not jump


def test_glide_reaches_the_target_in_exactly_the_samples():
    s = Smoothed(0.0, 0.1, SR)
    s.target = 1.0
    s.glide(3840)
    values = np.concatenate([np.broadcast_to(s.block(1000), (1000,)) for _ in range(4)])[:3840]
    assert np.all(values[:3839] < 1.0)
    assert values[3839] == 1.0
    assert np.allclose(np.diff(np.concatenate([[0.0], values])), 1 / 3840, atol=1e-12)
    assert s.settled


def test_glide_then_the_rate_is_the_configured_one_again():
    s = Smoothed(0.0, 2.0, SR)
    s.target = 1.0
    s.glide(100)
    s.block(100)
    assert s.settled
    s.target = 0.0
    values = np.asarray(s.block(10))
    assert np.allclose(np.diff(np.concatenate([[1.0], values])), -2.0 / SR, atol=1e-12)


def test_glide_to_the_same_value_is_settled_at_once():
    s = Smoothed(0.5, 1.0, SR)
    s.glide(1000)
    assert s.settled
    assert s.block(10) == 0.5


def test_a_new_target_ends_a_glide():
    s = Smoothed(0.0, 2.0, SR)
    s.target = 1.0
    s.glide(1000)
    s.block(10)
    s.target = 0.0
    values = np.asarray(s.block(5))
    assert np.max(np.abs(np.diff(values))) <= 2.0 / SR + 1e-12


def test_decibel_ramp_glides_in_db():
    r = DecibelRamp(-20.0, 30.0, SR)
    r.target_db = 0.0
    r.glide(4800)
    db = np.concatenate([np.broadcast_to(r.block_db(1200), (1200,)) for _ in range(4)])
    assert db[-1] == 0.0
    assert np.allclose(np.diff(np.concatenate([[-20.0], db])), 20 / 4800, atol=1e-12)


def test_reassigning_the_same_target_keeps_the_glide():
    s = Smoothed(0.0, 0.1, SR)
    s.target = 1.0
    s.glide(3840)
    values = []
    for _ in range(4):
        s.target = 1.0  # the motor does this every block
        values.append(np.broadcast_to(s.block(1000), (1000,)))
    values = np.concatenate(values)[:3840]
    assert np.all(values[:3839] < 1.0)
    assert values[3839] == 1.0
