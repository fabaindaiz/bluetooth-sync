"""The headphone monitor inside a session, with the simulated room (spec
2026-10-04-headphone-monitor-design.md §3). SIMULATED: no PipeWire."""

import numpy as np

from aurasync import motor as motor_module
from aurasync.config import Instalacion, Parlante
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedSession

SR = 48000
BLOCK = 4096


class _Input:
    pid = 1

    def __init__(self) -> None:
        rng = np.random.default_rng(5)
        self.left, self.right = rng.standard_normal((2, 20 * BLOCK)) * 0.1
        self.pos = 0

    def leer(self, n: int, espera_s: float = 0.05):  # noqa: ARG002
        a, b = self.left[self.pos : self.pos + n], self.right[self.pos : self.pos + n]
        self.pos += n
        return a, b


class _FakeMonitor:
    def __init__(self, *, fail: bool = False) -> None:
        self.pushed: list[tuple] = []
        self.closed = False
        self.fail = fail

    def push(self, pair, blocks) -> None:
        if self.fail:
            msg = "pw-play died"
            raise OSError(msg)
        self.pushed.append((pair, {k: v.copy() for k, v in blocks.items()}))

    def close(self) -> None:
        self.closed = True


def _session():
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=p, ambiente=0.2) for i, p in enumerate((-0.7, 0.7))]
    )
    s = SimulatedSession(inst, motor_module.Motor(inst, SR), SessionOptions(block=BLOCK), lambda *_, **__: None)
    s.open()
    s._input = _Input()  # noqa: SLF001
    played: list[dict] = []
    room_play = s.room.play
    s.room.play = lambda blocks: (played.append({k: v.copy() for k, v in blocks.items()}), room_play(blocks))[1]
    return s, played


def test_the_monitor_gets_every_block_and_the_speakers_are_unchanged():
    plain, played_plain = _session()
    for _ in range(6):
        plain.step()
    plain.close()

    with_monitor, played = _session()
    fake = _FakeMonitor()
    with_monitor.attach_monitor(fake)
    for _ in range(6):
        with_monitor.step()
    with_monitor.close()

    assert len(fake.pushed) == 6
    pair, blocks = fake.pushed[0]
    assert pair is not None
    assert len(pair[0]) == BLOCK
    assert set(blocks) == {"s0", "s1"}
    assert len(played) == len(played_plain) == 6
    for a, b in zip(played, played_plain, strict=True):
        for name in a:
            assert np.array_equal(a[name], b[name])
    assert fake.closed


def test_a_failing_monitor_is_dropped_and_the_speakers_go_on():
    s, played = _session()
    s.attach_monitor(_FakeMonitor(fail=True))
    for _ in range(4):
        s.step()
    assert s.monitor is None
    assert len(played) == 4
    s.close()


def test_attaching_another_monitor_closes_the_previous_one():
    s, _ = _session()
    first, second = _FakeMonitor(), _FakeMonitor()
    s.attach_monitor(first)
    s.attach_monitor(second)
    assert first.closed
    assert not second.closed
    s.attach_monitor(None)
    assert second.closed
    s.close()
