"""The loop on absolute arrivals (`arrival_loop.py`): per speaker, following each one's drift.

The first tests are the traps (a measurement that does not include the loop's own corrections,
a wrong peak, a real jump); the last ones are one simulated hour per case (`tests/drift_room.py`,
the model of experimentos/16 §4.2 with the product's controller).
"""

import numpy as np
import pytest

from aurasync import medicion, motor, sincronia
from aurasync.arrival_loop import ArrivalLoop
from aurasync.config import Instalacion, Parlante
from tests.drift_room import simulate

SR = 48000


def _installation(**delays) -> Instalacion:
    return Instalacion(
        parlantes=[Parlante(n, f"sink_{n}", retardo_ms=d) for n, d in delays.items()],
        retardo_traseros_ms=0.0,
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _arrivals_after(inst: Instalacion, latency: dict[str, float]) -> dict[str, float]:
    """Total arrival of each speaker: its delay plus its playback latency."""
    return {n: inst.por_nombre(n).retardo_ms + lat for n, lat in latency.items()}


# -- what is measured: the references are taken after the delay line ------------------------


def test_the_loop_measures_absolute_arrivals_the_applied_delay_is_not_in_them():
    """The fact the controller is built on (SIMULADO): the real engine, its output as the
    reference, a room with known delays; three different applied delays measure the same."""
    room_ms = {"a": 3.0, "b": 7.5, "c": 12.0}
    rng = np.random.default_rng(1)
    left, right = rng.standard_normal(SR * 6) * 0.1, rng.standard_normal(SR * 6) * 0.1
    seen = []
    for applied in ((0.0, 0.0, 0.0), (0.0, 5.0, 0.0), (4.0, 0.0, 2.0)):
        inst = Instalacion(
            parlantes=[
                Parlante(n, f"s_{n}", retardo_ms=d, pan=p)
                for (n, d, p) in zip(room_ms, applied, (-0.7, 0.7, 0.0), strict=True)
            ],
            retardo_traseros_ms=0.0,
        )
        out = motor.procesar_completo(motor.Motor(inst, SR, extraer_ambiente=False), left, right)
        mic = np.zeros(len(left))
        for n, x in out.items():
            d = round(room_ms[n] * SR / 1000)
            mic[d:] += x[: len(x) - d]
        cal = medicion.calibrar(mic, out, SR)
        seen.append([cal.retardos_ms[n] for n in room_ms])
    assert np.allclose(seen, seen[0], atol=0.05), seen


def test_repeating_the_same_arrivals_converges_and_stays():
    """`sincronia.Controlador.proponer` adds every measurement to what is applied: fed the same
    absolute arrivals it keeps moving the delays (the growing proposals of experimentos/09 §5).
    This loop settles and then stays where it is."""
    latency = {"a": 3.0, "b": 7.5, "c": 12.0}
    inst = _installation(a=0.0, b=0.0, c=0.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock, track_drift=False)
    for k in range(20):
        clock.t = 4.0 * k
        loop.propose(latency, set(latency), clock.t)
    totals = _arrivals_after(inst, latency)
    assert max(totals.values()) - min(totals.values()) <= 0.5
    settled = {p.nombre: p.retardo_ms for p in inst.parlantes}
    for k in range(20, 30):
        clock.t = 4.0 * k
        loop.propose(latency, set(latency), clock.t)
    assert {p.nombre: p.retardo_ms for p in inst.parlantes} == settled

    # The old controller, on the same feed, does not stop.
    old_inst = _installation(a=0.0, b=0.0, c=0.0)
    old = sincronia.Controlador(old_inst, factor=0.5)
    corrections = medicion.correcciones(dict(latency))
    for _ in range(10):
        old.proponer(medicion.Calibracion(corrections, dict.fromkeys(latency, 0.0), dict.fromkeys(latency, 0.1), 0.0))
    assert max(p.retardo_ms for p in old_inst.parlantes) > 2 * max(corrections.values())


def test_a_common_offset_between_measurements_does_not_matter():
    latency = {"a": 3.0, "b": 7.5, "c": 12.0}
    inst = _installation(a=0.0, b=0.0, c=0.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock, track_drift=False)
    rng = np.random.default_rng(2)
    for k in range(20):
        clock.t = 4.0 * k
        common = rng.uniform(-80, 80)
        loop.propose({n: v + common for n, v in latency.items()}, set(latency), clock.t)
    totals = _arrivals_after(inst, latency)
    assert max(totals.values()) - min(totals.values()) <= 0.5


def test_one_measurement_is_never_enough():
    inst = _installation(a=0.0, b=0.0, c=0.0)
    loop = ArrivalLoop(inst, clock=lambda: 0.0)
    adjustment = loop.propose({"a": 0.0, "b": 6.0, "c": 0.0}, {"a", "b", "c"}, 0.0)
    assert not adjustment.hubo_cambios
    assert all(p.retardo_ms == 0.0 for p in inst.parlantes)


def test_a_wrong_peak_on_one_speaker_is_held_and_the_others_are_still_corrected():
    latency = {"a": 3.0, "b": 7.5, "c": 12.0, "d": 5.0}
    inst = _installation(a=0.0, b=0.0, c=0.0, d=0.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock, track_drift=False)
    for k in range(2):
        clock.t = 4.0 * k
        loop.propose(latency, set(latency), clock.t)
    before = {p.nombre: p.retardo_ms for p in inst.parlantes}
    clock.t = 8.0
    wrong = {**latency, "b": latency["b"] + 8.0}  # one speaker's peak off by 8 ms, once
    adjustment = loop.propose(wrong, set(wrong), clock.t)
    assert "b" not in adjustment.cambios_ms
    # b moves only through the normalisation (common to all), never by its wrong 8 ms.
    shift = inst.por_nombre("a").retardo_ms - before["a"]
    assert inst.por_nombre("b").retardo_ms - before["b"] == pytest.approx(shift, abs=1.0)


def test_a_speaker_that_fails_its_own_check_does_not_discard_the_others():
    latency = {"a": 3.0, "b": 7.5, "c": 12.0}
    inst = _installation(a=0.0, b=0.0, c=0.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock, track_drift=False)
    for k in range(12):
        clock.t = 4.0 * k
        loop.propose(latency, {"a", "c"}, clock.t)  # b is never valid
    assert inst.por_nombre("b").retardo_ms == 0.0 or "b" not in loop.ajustes[-1].cambios_ms
    totals = _arrivals_after(inst, {"a": 3.0, "c": 12.0})
    assert abs(totals["a"] - totals["c"]) <= 0.5


def test_a_jump_that_repeats_is_followed():
    """A stream that resynchronises comes back with a different latency, and stays there."""
    latency = {"a": 3.0, "b": 7.5, "c": 12.0}
    inst = _installation(a=0.0, b=0.0, c=0.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock, track_drift=False)
    for k in range(15):
        clock.t = 4.0 * k
        loop.propose(latency, set(latency), clock.t)
    latency["b"] += 42.67  # one PipeWire quantum
    for k in range(15, 60):
        clock.t = 4.0 * k
        loop.propose(latency, set(latency), clock.t)
    totals = _arrivals_after(inst, latency)
    assert max(totals.values()) - min(totals.values()) <= 0.5


def test_the_drift_of_each_speaker_is_estimated():
    rates_ppm = {"a": 0.0, "b": 22.0, "c": -15.0}
    inst = _installation(a=10.0, b=10.0, c=10.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock)
    for k in range(40):
        clock.t = 4.0 * k
        loop.propose({n: r * 1e-3 * clock.t for n, r in rates_ppm.items()}, set(rates_ppm), clock.t)
    ppm = loop.drift_ppm()
    # Against the others' frame (the median speaker), so compare differences.
    assert ppm["b"] - ppm["a"] == pytest.approx(22.0, abs=1.0)
    assert ppm["c"] - ppm["a"] == pytest.approx(-15.0, abs=1.0)


def test_advance_moves_each_delay_at_its_rate_between_measurements():
    rates_ppm = {"a": 0.0, "b": 40.0}
    inst = _installation(a=10.0, b=10.0)
    clock = _Clock()
    loop = ArrivalLoop(inst, clock=clock)
    for k in range(10):
        clock.t = 4.0 * k
        loop.propose({n: r * 1e-3 * clock.t for n, r in rates_ppm.items()}, set(rates_ppm), clock.t)
        loop.advance(clock.t)
    gap = inst.por_nombre("a").retardo_ms - inst.por_nombre("b").retardo_ms
    loop.advance(clock.t + 100.0)
    # b falls behind 40 ppm: in 100 s it needs 4 ms less delay than a.
    after = inst.por_nombre("a").retardo_ms - inst.por_nombre("b").retardo_ms
    assert after - gap == pytest.approx(4.0, abs=0.3)
    assert min(p.retardo_ms for p in inst.parlantes) == 0.0


# -- one hour in a drifting room ---------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.parametrize("ppm", [22.0, 50.0])
@pytest.mark.parametrize("n", [3, 8])
def test_one_hour_of_drift_stays_under_half_a_millisecond(n, ppm, seed):
    """experimentos/16 §4.2 reached 0.50-0.78 ms (p95) with its prototype; with the dead band on
    the spread and the drift followed, the product's loop holds the misalignment under 0.5 ms
    for the whole hour after the first 10 minutes, every speaker measured every 4 s."""
    result = simulate(n, ppm, seed)
    assert result["p95_ms"] < 0.5, result
    assert result["max_ms"] < 0.6, result


@pytest.mark.parametrize("seed", [0, 1])
def test_one_hour_with_wrong_peaks_passing_as_valid(seed):
    """2 % of the measurements off by up to 5 ms *and believed valid* by the estimator: the
    repetition check holds them."""
    result = simulate(8, 50.0, seed, outliers=0.02)
    assert result["p95_ms"] < 0.5, result
    assert result["max_ms"] < 1.0, result


def test_without_following_the_drift_the_same_hour_does_not_hold():
    """The drift tracking is what holds it: without it the dead band has to absorb the drift."""
    result = simulate(8, 50.0, 0, track_drift=False)
    assert result["p95_ms"] > 1.0, result
