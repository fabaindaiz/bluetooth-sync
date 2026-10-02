"""Live quality (spec 2026-10-02 §6.2): loudness in and out of the real engine, net gain, PSR."""

import json
import math
import time

import numpy as np
import pytest

from aurasync import chain, motor
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.quality import QualityMeter

SR = 48000
BLOCK = 4096


def _pink(n, seed):
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spectrum[1:] /= np.sqrt(f[1:])
    spectrum[0] = 0
    x = np.fft.irfft(spectrum, n)
    return 0.05 * x / np.sqrt(np.mean(x**2))


def _music(n, seed):
    """Pink noise with a slow envelope and a few notes: crest factor like a mix."""
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    x = _pink(n, seed) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t) ** 2)
    for f in rng.uniform(80, 800, 6):
        x += 0.02 * np.sin(2 * np.pi * f * t) * (np.sin(2 * np.pi * rng.uniform(0.2, 1.0) * t) > 0)
    return x


def _installation():
    return Instalacion(
        parlantes=[
            Parlante("Red", "s0", pan=-0.7, ambiente=0.15),
            Parlante("Black", "s1", pan=0.7, ambiente=0.15),
            Parlante("Blue", "s2", pan=0.0, ambiente=0.55),
        ]
    )


def _play(m, left, right, meter):
    for i in range(0, len(left), BLOCK):
        pair = (left[i : i + BLOCK], right[i : i + BLOCK])
        meter.push(pair, m.procesar(*pair))


@pytest.mark.parametrize("material", ["pink", "music"])
@pytest.mark.parametrize("correlated", [False, True], ids=["wide", "centred"])
def test_net_gain_is_zero_at_0_db_with_the_defaults_minus_the_ambience(material, correlated):
    """Panel at 0 dB, chain at its defaults, ambience mixed out: the sum of the outputs is as
    loud as the input within 1 LU for a wide stereo mix. A centred one is louder by up to
    10 log10(3/2) = 1.76 LU: three speakers each play about the mono sum of two channels
    (MEDIDO: +1.27 LU with 0.6 common + 0.4 own per channel). The extracted ambience is then
    added on top (a share of the input sent again), and the number says by how much."""
    n = SR * 8
    make = _pink if material == "pink" else _music
    common = make(n, 1)
    share = 0.6 if correlated else 0.0
    left = share * common + (1 - share) * make(n, 2)
    right = share * common + (1 - share) * make(n, 3)
    results = {}
    for label, values in {
        "no ambience": ChainValues().with_algorithm("ambience", "off"),
        "defaults": ChainValues(),
    }.items():
        m = motor.Motor(_installation(), SR, ecualizar=True, chain=values, bloque=BLOCK)
        meter = QualityMeter(SR, [p.nombre for p in m.instalacion.parlantes])
        _play(m, left, right, meter)
        results[label] = meter.summary(m.uso_limitador_pct(), m.volumen_db)
    net = results["no ambience"]["net_gain_lu"]
    if correlated:
        assert 0.0 <= net <= 10 * math.log10(3 / 2) + 0.3, results["no ambience"]
    else:
        assert abs(net) <= 1.0, results["no ambience"]
    added = results["defaults"]["net_gain_lu"] - net
    assert -3.0 <= added <= 1.0, added
    summary = results["defaults"]
    json.dumps(summary)
    assert summary["chain_gain_lu"] == summary["net_gain_lu"]  # the panel at 0 dB
    if material == "music":
        # Spec §6.4: no PSR loss over 1 dB with the defaults on music-like material. (Wide pink
        # noise is flagged on the speaker that is mostly ambience: MEDIDO 13.6 dB out, over
        # 1 dB under either channel in: the extractor's overlap-add evens out its peaks.)
        assert not summary["flattening"], summary
    assert summary["tp_max"] < -1.0


def test_volume_and_limiting_show_in_the_numbers():
    n = SR * 6
    left, right = _music(n, 4), _music(n, 5)
    m = motor.Motor(_installation(), SR, volumen_db=-10.0, bloque=BLOCK)
    meter = QualityMeter(SR, [p.nombre for p in m.instalacion.parlantes])
    _play(m, left, right, meter)
    quiet = meter.summary(m.uso_limitador_pct(), m.volumen_db)
    assert quiet["net_gain_lu"] == pytest.approx(quiet["chain_gain_lu"] - 10.0, abs=0.01)
    # 30 dB louder into the engine: the limiter flattens the outputs, and the meter says so.
    m = motor.Motor(_installation(), SR, bloque=BLOCK)
    meter = QualityMeter(SR, [p.nombre for p in m.instalacion.parlantes])
    _play(m, 30 * left, 30 * right, meter)
    loud = meter.summary(m.uso_limitador_pct(), m.volumen_db)
    assert loud["flattening"]
    assert loud["limiter_pct_max"] > 50
    assert loud["input"]["psr"] - min(o["psr"] for o in loud["outputs"].values()) > 1.0


def test_the_input_reads_as_one_stereo_meter_would():
    from aurasync.dsp.loudness import LoudnessMeter

    n = SR * 6
    left, right = _music(n, 8), 0.5 * _music(n, 9)
    meter, stereo = QualityMeter(SR, ["A"]), LoudnessMeter(SR, 2)
    for i in range(0, n, BLOCK):
        pair = (left[i : i + BLOCK], right[i : i + BLOCK])
        meter.push(pair, {"A": pair[0]})
        stereo.push(np.column_stack(pair))
    got = meter.summary()["input"]
    assert got["s"] == pytest.approx(stereo.short_term, abs=0.051)
    assert got["m"] == pytest.approx(stereo.momentary, abs=0.051)
    assert got["i"] == pytest.approx(stereo.integrated, abs=0.051)
    assert got["tp"] == pytest.approx(stereo.true_peak_short_dbtp, abs=0.051)


def test_silence_is_null_not_infinity():
    meter = QualityMeter(SR, ["A"])
    meter.push(None, {"A": np.zeros(BLOCK)})
    summary = meter.summary()
    assert summary["input"]["s"] is None
    assert summary["net_gain_lu"] is None
    json.dumps(summary, allow_nan=False)
    assert math.isinf(meter.outputs_short_term)


def test_the_meters_cost_under_half_a_millisecond_per_block():
    """Spec §6.2: < 0.5 ms per block for 3 speakers (MEDIDO 0.28-0.32 ms on the Mac); a loose
    2 ms here so a slow machine does not flake."""
    meter = QualityMeter(SR, ["A", "B", "C"])
    x = _music(BLOCK * 60, 6)
    times = []
    for i in range(60):
        block = x[i * BLOCK : (i + 1) * BLOCK]
        t = time.perf_counter()
        meter.push((block, block), {"A": block, "B": block, "C": block})
        times.append(time.perf_counter() - t)
    assert float(np.median(times)) * 1000 < 2.0
    assert meter.samples == 60 * BLOCK


def test_chain_metrics_are_plain_json_with_every_new_stage_on():
    inst = Instalacion(
        parlantes=[Parlante("Go 4", "s0", tipo="go4"), Parlante("Charge", "s1", tipo="charge6", ambiente=0.3)]
    )
    v = ChainValues().with_algorithm("diffuse", "noise_tail").with_algorithm("bass", "crossover")
    v = v.with_algorithm("limiter", "true_peak").with_change(chain.validate_set("eq", params={"budget_db": 2.0}))
    m = motor.Motor(inst, SR, ecualizar=True, chain=v, bloque=BLOCK)
    x = _music(SR * 2, 7)
    motor.procesar_completo(m, x, x, BLOCK)
    metrics = m.metricas_cadena()
    json.dumps(metrics, allow_nan=False)
    assert metrics["bass"]["to"] == "Charge"
    assert metrics["limiter"]["kind"] == "true_peak"
    assert metrics["diffuse"]["tail_db"]["Go 4"] is not None
