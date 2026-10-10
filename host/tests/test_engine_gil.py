"""The extension lets go of the interpreter while its per-block calls work (`Python::detach`).

Without it, while the engine thread processes a block in Rust every other Python thread of the
service (the HTTP server, the panel's events) waits for the GIL. Each per-block call here runs on
a block long enough to take tens of milliseconds, while another thread wakes every millisecond and
notes the time: it must have run in the middle of the call, which it cannot while the GIL is held.
The parity of each call with numpy is the business of its own `test_*_rust.py`.
"""

from __future__ import annotations

import threading
import time

import aurasync_engine  # fails, does not skip, when the extension is not built
import numpy as np
import pytest

from aurasync.dsp import backend, eq, limiter, loudness, spatial, virtual_bass
from aurasync.dsp.ambience import Extractor

SR = 48000
LONG = 1 << 21
"""Samples per call: 44 s of audio, tens of milliseconds of work for each call below."""


@pytest.fixture(autouse=True)
def _rust():
    backend.reset()
    backend.use(backend.RUST)
    yield
    backend.reset()


def _signal(n: int = LONG, seed: int = 1) -> np.ndarray:
    return 0.5 * np.random.default_rng(seed).standard_normal(n)


def _taps() -> np.ndarray:
    return eq.fir(np.full(len(eq.THIRDS), 3.0))


def _read():
    reader, data = aurasync_engine.Reader(), _signal(LONG + 64)
    position = 16.0 + np.arange(LONG) * 0.999
    return lambda: reader.read(data, position)


def _upmix():
    names = ["a", "b", "c", "d"]
    angles = {"a": -30.0, "b": 30.0, "c": -110.0, "d": 110.0}
    rust = spatial.SpatialUpmix(names, angles, set(), SR)._build_rust()  # noqa: SLF001
    x, y = _signal(LONG // 4), _signal(LONG // 4, seed=2)
    return lambda: rust.process(x, y)


def _extractor():
    rust = Extractor()._build_rust()  # noqa: SLF001
    x, y = _signal(LONG // 4), _signal(LONG // 4, seed=2)
    return lambda: rust.process(x, y)


def _one(build, call, n: int = LONG):
    """A call on one long block of noise: `call(object, x)` with `object = build()`."""

    def make():
        thing, x = build(), _signal(n)
        return lambda: call(thing, x)

    return make


CALLS = {
    "Reader.read": _read,
    "StreamingFIR.process": _one(lambda: aurasync_engine.StreamingFIR(_taps()), lambda f, x: f.process(x)),
    # The fastest of them: 19.6 ms on `HP-O16` with LONG, under the 20 ms the test needs to tell.
    "PartitionedFIR.process": _one(
        lambda: aurasync_engine.PartitionedFIR(_taps(), 4096), lambda f, x: f.process(x), n=4 * LONG
    ),
    "VirtualBass.process": _one(
        lambda: virtual_bass.VirtualBass(SR, 90.0, 0.0, 4096)._build_rust(),  # noqa: SLF001
        lambda v, x: v.process(x, 1.0, 1.0),
    ),
    "TruePeakLimiter.process": _one(
        lambda: limiter.TruePeakLimiter(SR)._build_rust(),  # noqa: SLF001
        lambda lim, x: lim.process(x),
    ),
    "LoudnessMeter.push": _one(
        lambda: loudness.LoudnessMeter(SR, 2)._build_rust(),  # noqa: SLF001
        lambda m, x: m.push(np.repeat(x, 2)),
    ),
    "SpatialUpmix.process": _upmix,
    "AmbienceExtractor.process": _extractor,
}


@pytest.mark.parametrize("name", list(CALLS))
def test_another_python_thread_runs_while_the_call_works(name):
    call = CALLS[name]()
    ticks: list[float] = []
    stop = threading.Event()

    def tick() -> None:
        while not stop.is_set():
            ticks.append(time.perf_counter())
            time.sleep(0.001)

    thread = threading.Thread(target=tick)
    thread.start()
    try:
        time.sleep(0.01)
        start = time.perf_counter()
        call()
        end = time.perf_counter()
    finally:
        stop.set()
        thread.join()
    span = end - start
    assert span > 0.02, f"{name} took {span * 1000:.1f} ms: too short to tell"
    middle = [t for t in ticks if start + 0.25 * span < t < start + 0.75 * span]
    assert middle, f"no tick in the middle of {span * 1000:.0f} ms of {name}: it held the GIL"
