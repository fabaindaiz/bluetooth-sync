import threading

import numpy as np

from aurasync.dsp.response import THIRDS
from aurasync.telemetry import CHUNK, Telemetry

SR = 48000


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _block(level, n=4096):
    return np.full(n, level)


def test_each_block_gives_one_frame_per_chunk_timed_to_the_ear():
    clock = Clock()
    tel = Telemetry(SR, clock)
    tel.set_latency(500.0)
    x = np.concatenate([np.full(CHUNK, 0.1), np.full(CHUNK, 0.5), np.zeros(2 * CHUNK)])
    tel.record({"a": x}, None)
    assert tel.meters_at(100.4) is None  # written, not heard yet
    first = tel.meters_at(100.5)
    assert first["meters"]["a"]["rms_db"] == round(20 * np.log10(0.1), 1)
    second = tel.meters_at(100.5 + CHUNK / SR)
    assert second["meters"]["a"]["rms_db"] == round(20 * np.log10(0.5), 1)
    assert first["synced"]


def test_the_frame_depends_only_on_the_instant_asked():
    clock = Clock()
    tel = Telemetry(SR, clock)
    for i in range(10):
        tel.record({"a": _block(0.01 * (i + 1))}, None)
        clock.t += 4096 / SR
    instant = 100.0 + 3.3 * 4096 / SR
    forward = [tel.meters_at(t) for t in np.linspace(100.0, instant, 7)][-1]
    backward = [tel.meters_at(t) for t in np.linspace(instant + 0.5, instant, 7)][-1]
    assert forward["meters"] == backward["meters"]


def test_the_ring_is_bounded():
    clock = Clock()
    tel = Telemetry(SR, clock)
    for _ in range(500):
        tel.record({"a": _block(0.1)}, (_block(0.1), _block(0.1)))
        clock.t += 4096 / SR
    assert len(tel._meters) == tel._meters.maxlen  # noqa: SLF001
    assert len(tel._input) <= tel._input.maxlen  # noqa: SLF001


def test_input_frame_has_a_spectrum_and_correlation():
    clock = Clock()
    tel = Telemetry(SR, clock)
    rng = np.random.default_rng(0)
    x = rng.standard_normal(4096) * 0.1
    tel.record({"a": x}, (x, x))
    frame = tel.input_at()
    assert len(frame["bands_db"]) == len(THIRDS)
    assert frame["correlation"] == 1.0


def test_readers_never_stop_the_writer():
    clock = Clock()
    tel = Telemetry(SR, clock)
    stop = threading.Event()

    def read():
        while not stop.is_set():
            tel.meters_at()

    readers = [threading.Thread(target=read) for _ in range(4)]
    for r in readers:
        r.start()
    for _ in range(200):
        tel.record({"a": _block(0.1)}, None)
        clock.t += 0.01
    stop.set()
    for r in readers:
        r.join()
    assert tel.meters_at() is not None


def test_the_microphone_is_shown_now_not_delayed():
    clock = Clock()
    tel = Telemetry(SR, clock)
    tel.set_latency(500.0)
    tel.record({"a": _block(0.1)}, None)
    tel.record_microphone(np.full(480, 0.2))
    clock.t += 0.6
    assert tel.meters_at()["meters"]["mic"]["rms_db"] == round(20 * np.log10(0.2), 1)
    clock.t += 2.0
    assert "mic" not in tel.meters_at()["meters"]
