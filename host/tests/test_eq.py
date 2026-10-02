import numpy as np
import pytest

from aurasync.dsp import eq
from aurasync.dsp.response import THIRDS


def test_a_flat_response_needs_no_correction():
    assert not np.any(eq.correction([0.0] * len(THIRDS)))


def test_a_bump_is_never_cut_and_a_dip_is_lifted():
    measured = np.where((THIRDS > 200) & (THIRDS < 320), 6.0, 0.0)
    measured[(THIRDS > 2000) & (THIRDS < 3200)] = -5.0
    curve = eq.correction(measured)
    assert curve.min() == 0.0
    assert curve[int(np.argmin(np.abs(THIRDS - 250)))] == 0.0
    assert curve[int(np.argmin(np.abs(THIRDS - 2520)))] > 2.5


def test_lifting_stays_inside_the_band_and_is_limited():
    measured = np.full(len(THIRDS), -20.0)
    curve = eq.correction(measured, band=(90.0, 20000.0))
    assert np.all(curve[THIRDS < 80] == 0.0)
    assert curve.max() <= eq.MAX_BOOST_DB


def test_an_old_cutting_curve_is_lifted_back_to_zero():
    old = list(np.full(len(THIRDS), -18.0))
    assert not np.any(eq.correction(np.zeros(len(THIRDS)), previous_db=old))


def test_small_differences_are_noise_and_left_alone():
    rng = np.random.default_rng(0)
    assert not np.any(eq.correction(rng.uniform(-0.6, 0.6, len(THIRDS))))


def test_the_correction_builds_on_the_previous_curve():
    first = eq.correction(np.where(THIRDS > 8000, -4.0, 0.0))
    again = eq.correction(np.zeros(len(THIRDS)), previous_db=first)
    assert np.allclose(again, first)


def test_the_filter_follows_the_curve():
    curve = np.interp(np.log10(THIRDS), np.log10([50, 1000, 20000]), [-6, 0, -9])
    got = eq.response_of(eq.fir(curve))
    band = (THIRDS >= 100) & (THIRDS <= 16000)
    assert np.abs(got[band] - curve[band]).max() < 1.0


def test_no_curve_is_a_delayed_impulse_of_the_same_latency():
    h = eq.fir(None)
    assert int(np.argmax(np.abs(h))) == eq.LATENCY_SAMPLES
    assert np.count_nonzero(h) == 1
    curve = np.full(len(THIRDS), -3.0)
    assert int(np.argmax(np.abs(eq.fir(curve)))) == eq.LATENCY_SAMPLES


def test_streaming_equals_one_convolution():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(20000)
    h = eq.fir(np.where(THIRDS > 2000, -6.0, 0.0))
    f = eq.StreamingFIR(h)
    out = np.concatenate([f.process(x[i : i + 1500]) for i in range(0, len(x), 1500)])
    assert np.allclose(out, np.convolve(x, h)[: len(x)], atol=1e-9)


@pytest.mark.parametrize("on", [True, False])
def test_switching_eq_does_not_move_the_timing(on):
    from aurasync import motor
    from aurasync.config import Instalacion, Parlante

    inst = Instalacion(
        parlantes=[
            Parlante("a", "s0", pan=-1.0, ecualizacion_db=list(np.full(len(THIRDS), -2.0))),
            Parlante("b", "s1", pan=1.0),
        ]
    )
    m = motor.Motor(inst, 48000, extraer_ambiente=False, decorrelar=False, ecualizar=True)
    m.ecualizacion_activa = on
    m.reiniciar()
    impulso = np.zeros(8192)
    impulso[100] = 1.0
    out = motor.procesar_completo(m, impulso, np.zeros(8192), 1024)["a"]
    assert int(np.argmax(np.abs(out))) == 100 + eq.LATENCY_SAMPLES + m.latencia_retardo
