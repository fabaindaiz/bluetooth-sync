"""The recalibration loop inside a session, with the simulated room (`simulated.py`).

SIMULATED. The real engine, emission windows, microphone slices, estimators and loop; the
room delays each speaker by `simulated.ROOM_DELAYS_MS` (no drift: the drift is
`test_arrival_loop.py`'s hour). The clock is fake and advances one block per step; each
measurement is waited for, so the run is deterministic.
"""

import time as real_time

import numpy as np
import pytest

from aurasync import motor as motor_module
from aurasync import session as session_module
from aurasync.config import Instalacion, Parlante
from aurasync.session import SessionOptions
from aurasync.simulated import ROOM_DELAYS_MS, SimulatedSession
from tests import probe_room

SR = 48000
BLOCK = 4096


class _MusicInput:
    """The virtual sink, without pacing: synthetic music, correlated between the speakers."""

    pid = 1

    def __init__(self, seconds: float = 40.0, *, noise: bool = False) -> None:
        rng = np.random.default_rng(3)
        if noise:  # two independent channels: content the music correlation can measure
            self.left, self.right = (
                rng.standard_normal(int(seconds * SR)) * 0.1,
                rng.standard_normal(int(seconds * SR)) * 0.1,
            )
        else:
            self.left, self.right = probe_room.music(seconds, rng)
        self.pos = 0

    def leer(self, n: int, espera_s: float = 0.05):  # noqa: ARG002
        idx = (self.pos + np.arange(n)) % len(self.left)
        self.pos = (self.pos + n) % len(self.left)
        return self.left[idx], self.right[idx]


def _run(monkeypatch, n: int, *, probe: bool, minutes: float, decorrelate: bool = True, pans=None, noise=False):
    clock = {"t": 1000.0}
    monkeypatch.setattr(session_module.time, "monotonic", lambda: clock["t"])
    pans, ambience = pans or (-0.7, 0.7, 0.7), (0.15, 0.15, 0.55)
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=pans[i % 3], ambiente=ambience[i % 3]) for i in range(n)],
    )
    engine = motor_module.Motor(inst, SR, decorrelar=decorrelate)
    options = SessionOptions(block=BLOCK, microphone="simulado", probe=probe)
    s = SimulatedSession(inst, engine, options, lambda *_, **__: None)
    s.open()
    s._input = _MusicInput(noise=noise)  # noqa: SLF001
    s.source.kind = "app"
    s.enable_recalibration("simulado")
    spreads = []
    for _ in range(int(minutes * 60 * SR / BLOCK)):
        clock["t"] += BLOCK / SR
        s.step()
        while s._measurer.ocupado:  # noqa: SLF001
            real_time.sleep(0.005)
        rear = inst.retardo_traseros_ms
        heard = [
            engine.retardos_actuales_ms()[p.nombre] - p.ambiente * rear + ROOM_DELAYS_MS[i % len(ROOM_DELAYS_MS)]
            for i, p in enumerate(inst.parlantes)
        ]
        spreads.append(max(heard) - min(heard))
    log = list(s.loop.ajustes)
    s.close()
    return np.array(spreads), log, s


@pytest.mark.parametrize(("n", "decorrelate"), [(3, True), (8, False)])
def test_with_the_probe_the_loop_aligns_speakers_that_share_a_pan_and_stays(monkeypatch, n, decorrelate):
    """From uncalibrated (the room's 3-12 ms) to aligned within the dead band, and it stays:
    the Haas delay of the rear speakers is kept on top (the loop aligns `retardo_ms` only)."""
    spreads, log, s = _run(monkeypatch, n, probe=True, minutes=1.5, decorrelate=decorrelate)
    assert spreads[0] > 5.0
    last = spreads[-int(30 * SR / BLOCK) :]
    assert last.max() <= 0.5, (last.max(), [a.motivo for a in log[-5:]])
    assert s.probe_state()["active"]


def test_without_the_probe_the_loop_converges_instead_of_running_away(monkeypatch):
    """Against the music, as before, with content it can measure (two speakers, hard left and
    right of independent noise). The loop used to add every measurement to the delays (the
    references are taken after the delay line: `arrival_loop.py`): with the old controller
    the delays grew on every accepted round. Now they settle on what the room needs."""
    spreads, _, s = _run(monkeypatch, 2, probe=False, minutes=3.0, pans=(-1.0, 1.0), noise=True)
    assert spreads[0] > 4.0
    assert spreads[-1] <= 0.5
    assert (
        max(p.retardo_ms for p in s.installation.parlantes) <= max(ROOM_DELAYS_MS[:2]) - min(ROOM_DELAYS_MS[:2]) + 0.5
    )
