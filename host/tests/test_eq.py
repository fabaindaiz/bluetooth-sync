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


class _UncachedFIR:
    """The StreamingFIR as it was before the taps' FFT was cached (reference for the cache)."""

    def __init__(self, taps):
        self.taps = np.asarray(taps, dtype=float)
        self._tail = np.zeros(len(self.taps) - 1)

    def set_taps(self, taps):
        taps = np.asarray(taps, dtype=float)
        if len(taps) != len(self.taps):
            self._tail = np.zeros(len(taps) - 1)
        self.taps = taps

    def process(self, x):
        n = len(x)
        if n == 0:
            return np.zeros(0)
        size = 1 << int(np.ceil(np.log2(n + len(self.taps) - 1)))
        full = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(self.taps, size), size)[: n + len(self.taps) - 1]
        full[: len(self._tail)] += self._tail
        self._tail = full[n:].copy()
        return full[:n]


def test_the_cached_fir_gives_the_same_output_as_before():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(60000)
    h1 = eq.fir(np.where(THIRDS > 2000, -6.0, 0.0))
    h2 = eq.fir(np.where(THIRDS < 300, 4.0, 0.0))
    new, old = eq.StreamingFIR(h1), _UncachedFIR(h1)
    i, sizes, out_new, out_old = 0, [4096, 4096, 1024, 4096, 333, 4096, 4096, 0, 2048], [], []
    for k, n in enumerate(sizes * 3):
        if k == 10:
            new.set_taps(h2)
            old.set_taps(h2)
        if k == 20:
            new.set_taps(h1[:1000])
            old.set_taps(h1[:1000])
        out_new.append(new.process(x[i : i + n]))
        out_old.append(old.process(x[i : i + n]))
        i += n
    assert np.abs(np.concatenate(out_new) - np.concatenate(out_old)).max() <= 1e-12


def test_the_taps_spectrum_is_computed_once_per_size(monkeypatch):
    calls = []
    real = np.fft.rfft

    def counting(a, n=None, *args, **kwargs):
        calls.append(len(a))
        return real(a, n, *args, **kwargs)

    f = eq.StreamingFIR(eq.fir(None))
    monkeypatch.setattr(np.fft, "rfft", counting)
    for _ in range(10):
        f.process(np.ones(4096))
    assert len(calls) == 11
    f.taps = eq.fir(np.full(len(THIRDS), 2.0))
    f.process(np.ones(4096))
    assert len(calls) == 13


def _pink(n, seed=0):
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / eq.SR)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    return np.fft.irfft(spec, n)


def test_no_budget_and_no_cap_leave_the_curve_as_it_was():
    measured = np.where(THIRDS > 3000, -9.0, -2.0)
    plain = eq.correction(measured)
    assert np.array_equal(eq.correction(measured, budget_db=None, treble_cap_db=None), plain)
    assert np.array_equal(eq.correction(measured, budget_db=0.0), plain)


def test_the_budget_scales_the_whole_curve_down_to_the_pink_noise_energy_rise():
    measured = np.where(THIRDS > 1000, -12.0, -3.0)
    full = eq.correction(measured)
    assert eq.boost_energy_db(full) > 4
    capped = eq.correction(measured, budget_db=3.0)
    assert eq.boost_energy_db(capped) == pytest.approx(3.0, abs=0.02)
    lifted = full > 0
    ratio = capped[lifted] / full[lifted]
    assert np.ptp(ratio) < 0.01
    x = _pink(1 << 19)
    y = eq.StreamingFIR(eq.fir(capped)).process(x)
    f = np.fft.rfftfreq(len(x), 1 / eq.SR)
    band = (f >= THIRDS[0] / 2 ** (1 / 6)) & (f < THIRDS[-1] * 2 ** (1 / 6))
    rise = 10 * np.log10(np.sum(np.abs(np.fft.rfft(y))[band] ** 2) / np.sum(np.abs(np.fft.rfft(x))[band] ** 2))
    assert rise == pytest.approx(3.0, abs=0.3)


def test_a_curve_inside_the_budget_is_untouched():
    measured = np.where((THIRDS > 2000) & (THIRDS < 3200), -4.0, 0.0)
    assert np.array_equal(eq.correction(measured, budget_db=3.0), eq.correction(measured))


def test_the_treble_cap_limits_the_boost_above_8_khz_only():
    measured = np.full(len(THIRDS), -8.0)
    curve = eq.correction(measured, treble_cap_db=3.0)
    plain = eq.correction(measured)
    assert curve[THIRDS >= 8000].max() <= 3.0
    assert plain[THIRDS >= 8000].max() > 3.0
    assert np.array_equal(curve[THIRDS < 8000], plain[THIRDS < 8000])


@pytest.mark.parametrize(("partition", "sizes"), [(4096, [4096]), (1024, [1024, 1024, 300, 1024, 5000]), (512, [700])])
def test_the_partitioned_fir_equals_one_convolution_for_any_block(partition, sizes):
    rng = np.random.default_rng(3)
    x = rng.standard_normal(30000)
    h = rng.standard_normal(5000) * np.exp(-np.arange(5000) / 800)
    f = eq.PartitionedFIR(h, partition)
    out, i, k = [], 0, 0
    while i < len(x):
        n = sizes[k % len(sizes)]
        out.append(f.process(x[i : i + n]))
        i, k = i + n, k + 1
    assert np.abs(np.concatenate(out) - np.convolve(x, h)[: len(x)]).max() <= 1e-9


def test_the_partitioned_fir_can_skip_input_and_resume_exactly():
    rng = np.random.default_rng(4)
    x = rng.standard_normal(20000)
    h = rng.standard_normal(3000)
    f = eq.PartitionedFIR(h, 1024)
    f.process(x[:4096])
    f.skip(x[4096:8192])
    resumed = np.concatenate([f.process(x[i : i + 1024]) for i in range(8192, 20000, 1024)])
    assert np.abs(resumed - np.convolve(x, h)[8192:20000]).max() <= 1e-9
