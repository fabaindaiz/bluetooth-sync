"""The stages package E connected to the engine (spec 2026-10-02 §5): each one, switched on,
does what it says on the engine's real output; switched off, the golden run still passes
(`test_chain_golden.py`)."""

import time

import numpy as np
import pytest

from aurasync import chain, motor
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import eq, loudness

SR = 48000
BLOCK = 4096


def _pink(n, seed, sr=SR):
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / sr)
    spectrum[1:] /= np.sqrt(f[1:])
    spectrum[0] = 0
    x = np.fft.irfft(spectrum, n)
    return 0.1 * x / np.sqrt(np.mean(x**2))


def _three(*, charge=False, eq_db=None):
    third = (
        Parlante("JBL Charge 6", "s2", ambiente=0.0, tipo="charge6")
        if charge
        else Parlante("Go 4 C", "s2", ambiente=0.55)
    )
    return Instalacion(
        parlantes=[
            Parlante("Go 4 A", "s0", pan=-0.7, ambiente=0.15, tipo="go4", ecualizacion_db=eq_db),
            Parlante("Go 4 B", "s1", pan=0.7, ambiente=0.15, tipo="go4", ecualizacion_db=eq_db),
            third,
        ]
    )


def _run(values, inst, left, right, *, ecualizar=True, **kwargs):
    m = motor.Motor(inst, SR, ecualizar=ecualizar, chain=values, bloque=BLOCK, **kwargs)
    return motor.procesar_completo(m, left, right, BLOCK), m


def _band_energy(x, low, high):
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(spectrum[(f >= low) & (f < high)].sum())


def _set(values, stage, **params):
    return values.with_change(chain.validate_set(stage, params=params))


@pytest.fixture(scope="module")
def pink_stereo():
    common = _pink(SR * 6, 1)
    return 0.7 * common + 0.3 * _pink(SR * 6, 2), 0.7 * common + 0.3 * _pink(SR * 6, 3)


def test_protect_takes_the_bass_a_go_4_cannot_play(pink_stereo):
    left, right = pink_stereo
    off, _ = _run(ChainValues(), _three(), left, right)
    on, m = _run(ChainValues().with_algorithm("bass", "protect"), _three(), left, right)
    skip = SR  # past the filters' start
    for name in ("Go 4 A", "Go 4 B", "Go 4 C"):
        drop = 10 * np.log10(_band_energy(off[name][skip:], 20, 60) / _band_energy(on[name][skip:], 20, 60))
        assert drop >= 18.0, f"{name}: only {drop:.1f} dB under 60 Hz"
        # What the Go 4 can play is left alone.
        mid = 10 * np.log10(_band_energy(on[name][skip:], 300, 3000) / _band_energy(off[name][skip:], 300, 3000))
        assert abs(mid) < 0.3
    metrics = m.metricas_cadena()["bass"]
    assert metrics["active"]
    assert all(v > 0 for v in metrics["removed_db"].values())


def test_protect_order_8_takes_more(pink_stereo):
    left, right = pink_stereo
    v4 = ChainValues().with_algorithm("bass", "protect")
    v8 = _set(v4, "bass", order=8)
    four, _ = _run(v4, _three(), left, right)
    eight, _ = _run(v8, _three(), left, right)
    assert _band_energy(eight["Go 4 A"][SR:], 50, 70) < 0.3 * _band_energy(four["Go 4 A"][SR:], 50, 70)


def test_harmonics_add_energy_above_the_cutoff_and_none_below(pink_stereo):
    left, right = pink_stereo
    protect = ChainValues().with_algorithm("bass", "protect")
    plain, _ = _run(protect, _three(), left, right)
    rich, m = _run(_set(protect, "bass", harmonics_db=0.0), _three(), left, right)
    a, b = plain["Go 4 A"][SR:], rich["Go 4 A"][SR:]
    rise = 10 * np.log10(_band_energy(b, 100, 400) / _band_energy(a, 100, 400))
    assert 1.0 < rise < 6.0
    assert _band_energy(b, 20, 60) < 2 * _band_energy(a, 20, 60) + 1e-12
    assert m.metricas_cadena()["bass"]["harmonics_db"]["Go 4 A"] is not None


def test_harmonics_are_live_and_minus_24_is_off(pink_stereo):
    left, right = pink_stereo
    protect = ChainValues().with_algorithm("bass", "protect")
    m = motor.Motor(_three(), SR, ecualizar=False, chain=protect, bloque=BLOCK)
    motor.procesar_completo(m, left[:SR], right[:SR], BLOCK)
    assert m.aplicar_cadena(_set(protect, "bass", harmonics_db=0.0)) == "live"
    assert not m.en_corte
    assert m.aplicar_cadena(_set(protect, "bass", harmonics_db=-24.0)) == "live"
    motor.procesar_completo(m, left[:BLOCK], right[:BLOCK], BLOCK)
    assert m.metricas_cadena()["bass"]["harmonics_db"]["Go 4 A"] is None


def test_crossover_gives_the_bass_speaker_the_sum_of_the_low(pink_stereo):
    left, right = pink_stereo
    mono = (left + right) / 2  # the bass is in the centre, as in most music
    values = ChainValues().with_algorithm("bass", "crossover")
    off, _ = _run(ChainValues(), _three(charge=True), mono, mono)
    on, m = _run(values, _three(charge=True), mono, mono)
    skip = SR
    # The Go 4s lose their bass ...
    for name in ("Go 4 A", "Go 4 B"):
        assert _band_energy(off[name][skip:], 20, 60) / _band_energy(on[name][skip:], 20, 60) > 10**1.5
    # ... and the Charge 6 plays its own plus the fed low, in phase: ~ +6 dB under the cutoff.
    rise = 10 * np.log10(
        _band_energy(on["JBL Charge 6"][skip:], 25, 50) / _band_energy(off["JBL Charge 6"][skip:], 25, 50)
    )
    assert 5.0 < rise < 6.5, rise
    # Above the crossover it is untouched.
    high = _band_energy(on["JBL Charge 6"][skip:], 500, 5000) / _band_energy(off["JBL Charge 6"][skip:], 500, 5000)
    assert abs(10 * np.log10(high)) < 0.2
    metrics = m.metricas_cadena()["bass"]
    assert metrics["to"] == "JBL Charge 6"
    assert set(metrics["removed_db"]) == {"Go 4 A", "Go 4 B"}


def test_crossover_without_a_bass_speaker_does_nothing(pink_stereo):
    left, right = pink_stereo
    values = ChainValues().with_algorithm("bass", "crossover")
    on, m = _run(values, _three(), left[: SR // 2], right[: SR // 2])
    off, _ = _run(ChainValues(), _three(), left[: SR // 2], right[: SR // 2])
    for name in on:
        assert np.array_equal(on[name], off[name])
    assert m.metricas_cadena()["bass"]["reason"]


def _band(x, low=500.0, high=4000.0):
    spectrum = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spectrum[(f < low) | (f > high)] = 0
    return np.fft.irfft(spectrum, len(x))


def test_diffuse_makes_the_speakers_less_alike(pink_stereo):
    """Two speakers with the same mix (worst case): their correlation 500 Hz-4 kHz drops."""
    left, right = pink_stereo
    mono = (left + right) / 2

    def two():
        return Instalacion(parlantes=[Parlante("A", "s0"), Parlante("B", "s1")])

    plain, _ = _run(ChainValues(), two(), mono, mono, decorrelar=False, extraer_ambiente=False)
    v = ChainValues().with_algorithm("diffuse", "noise_tail")  # at its default level, -12 dB
    wet, m = _run(v, two(), mono, mono, decorrelar=False, extraer_ambiente=False)

    def corr(x, y):
        x, y = _band(x[SR:]), _band(y[SR:])
        return float(np.dot(x, y) / np.sqrt(np.dot(x, x) * np.dot(y, y)))

    assert corr(plain["A"], plain["B"]) > 0.999
    # MEDIDO 2026-10-02 (Mac): 0.90 at the default -12 dB.
    assert corr(wet["A"], wet["B"]) < 0.92
    louder, _ = _run(_set(v, "diffuse", level_db=-6.0), two(), mono, mono, decorrelar=False, extraer_ambiente=False)
    assert corr(louder["A"], louder["B"]) < corr(wet["A"], wet["B"]) - 0.1
    assert m.metricas_cadena()["diffuse"]["active"]
    # The level is live; the rest of the knobs go through the cut.
    assert m.aplicar_cadena(_set(v, "diffuse", level_db=-20.0)) == "live"
    assert m.aplicar_cadena(_set(v, "diffuse", rt60_s=1.0)) == "cut"


def test_true_peak_keeps_every_output_under_the_ceiling_oversampled(pink_stereo):
    left, right = pink_stereo
    loud_l, loud_r = 6 * left, 6 * right  # ~-4 dBFS RMS, peaks far over full scale
    boost = [6.0] * 27
    v = ChainValues().with_algorithm("limiter", "true_peak")
    on, m = _run(v, _three(eq_db=boost), loud_l[: 2 * SR], loud_r[: 2 * SR])
    for name, x in on.items():
        assert loudness.true_peak_dbtp(x, SR) <= -1.0 + 1e-3, name
    # The peak limiter only watches samples: between them it goes over.
    peak, _ = _run(ChainValues(), _three(eq_db=boost), loud_l[: 2 * SR], loud_r[: 2 * SR])
    assert max(loudness.true_peak_dbtp(x, SR) for x in peak.values()) > -1.0 + 0.05
    # The two speakers with the EQ lift were limited most of the time; the third never went over.
    pct = m.uso_limitador_pct()
    assert pct["Go 4 A"] > 50
    assert pct["Go 4 B"] > 50


def test_new_latency_is_the_same_on_every_speaker():
    """A click goes through the engine: the true-peak look-ahead moves every speaker alike,
    and the other new stages move none."""
    n = SR // 2
    click = np.zeros(n)
    click[1000] = 0.5

    def arrival(values):
        out, _ = _run(values, _three(charge=True), click, click, decorrelar=False, extraer_ambiente=False)
        return {name: int(np.argmax(np.abs(x))) for name, x in out.items()}

    base = arrival(ChainValues())
    tp = arrival(ChainValues().with_algorithm("limiter", "true_peak"))
    assert {k: tp[k] - base[k] for k in base} == dict.fromkeys(base, 144)
    extra = chain.latency_ms(ChainValues().with_algorithm("limiter", "true_peak")) - chain.latency_ms(ChainValues())
    assert extra == pytest.approx(3.0)


def test_eq_budget_and_treble_cap_act_on_what_plays_and_keep_the_stored_curve():
    curve = [0.0] * 20 + [6.0] * 7  # a lift above ~8 kHz
    inst = _three(eq_db=curve)
    m = motor.Motor(inst, SR, ecualizar=True, bloque=BLOCK)
    before = m.metricas_cadena()["eq"]["boost_energy_db"]["Go 4 A"]
    assert m.aplicar_cadena(_set(ChainValues(), "eq", treble_cap_db=2.0)) == "cut"
    motor.procesar_completo(m, np.zeros(SR // 2), np.zeros(SR // 2), BLOCK)
    sounding = m.curva_sonando(inst.por_nombre("Go 4 A"))
    assert max(sounding[22:]) == pytest.approx(2.0)  # the thirds from 8 kHz up
    assert inst.por_nombre("Go 4 A").ecualizacion_db == curve
    assert m.metricas_cadena()["eq"]["boost_energy_db"]["Go 4 A"] < before
    m.aplicar_cadena(_set(ChainValues(), "eq", budget_db=0.5))
    motor.procesar_completo(m, np.zeros(SR // 2), np.zeros(SR // 2), BLOCK)
    assert eq.boost_energy_db(m.curva_sonando(inst.por_nombre("Go 4 A"))) <= 0.5 + 1e-6


def test_eq_dead_band_reaches_the_correction():
    measured = np.full(27, -1.5)
    assert np.any(eq.correction(measured))
    assert not np.any(eq.correction(measured, dead_band_db=2.0))


def test_decorrelate_knobs_change_the_filters_and_their_shared_delay():
    from aurasync.dsp import decorrelate

    default = decorrelate.banco_decorrelador(3)
    same = decorrelate.banco_decorrelador(3, retardo_medio_ms=2.5, variacion_ms=1.5)
    assert all(np.array_equal(a, b) for a, b in zip(default, same, strict=True))
    later = decorrelate.banco_decorrelador(3, largo=512, retardo_medio_ms=4.0, variacion_ms=0.0)
    # No spread: each filter is a pure delay of the mean (192 samples).
    assert all(int(np.argmax(np.abs(h))) == 192 for h in later)
    assert decorrelate.largo_necesario(256, 4.0, 2.0) > 256
    m = motor.Motor(_three(), SR, bloque=BLOCK)
    assert m.aplicar_cadena(_set(ChainValues(), "decorrelate", mean_ms=4.0, spread_ms=2.0)) == "cut"
    motor.procesar_completo(m, np.zeros(SR // 2), np.zeros(SR // 2), BLOCK)
    assert m.metricas_cadena()["decorrelate"]["length"] == decorrelate.largo_necesario(256, 4.0, 2.0)


def test_everything_on_keeps_the_engine_well_ahead_of_real_time(pink_stereo):
    """Spec §5: ≥ 20x real time with every new stage on, 3 speakers. The probe
    (`probes/18-costo-de-la-cadena/costo.py`) gives the number; this keeps a loose floor
    (10x) so a slow CI machine does not flake, and catches an order-of-magnitude regression."""
    left, right = pink_stereo
    v = ChainValues().with_algorithm("diffuse", "noise_tail").with_algorithm("bass", "protect")
    v = _set(v.with_algorithm("limiter", "true_peak"), "bass", harmonics_db=0.0)
    m = motor.Motor(_three(eq_db=[3.0] * 27), SR, ecualizar=True, chain=v, bloque=BLOCK)
    times = []
    for i in range(40):
        a, b = left[i * BLOCK : (i + 1) * BLOCK], right[i * BLOCK : (i + 1) * BLOCK]
        t = time.perf_counter()
        m.procesar(a, b)
        times.append(time.perf_counter() - t)
    # The fastest tenth, not the median: other processes only ever add time, and with the
    # machine loaded (load average 18-28 while parallel suites ran, 2026-10-02) the median
    # fell to 8x and failed a check that is about the engine, not the machine.
    realtime = (BLOCK / SR) / float(np.percentile(times[5:], 10))
    assert realtime >= 10, f"{realtime:.0f}x real time"
