"""The `direct` render (spec 2026-10-05-virtual-speakers-and-hot-join §9): "pure aligned stereo".

Each speaker plays its side of L/R by its pan, at constant power, and nothing else of the chain:
no ambience extraction, decorrelation, EQ, bass stage, diffuse tail, Haas delay or spatial upmix.
What it keeps is what belongs to the speaker and to the listener: the alignment delay, the
speaker's gain, mute, the master volume and the safety limiter. The oracle below is built from
those parts only, with every effect of the chain switched on in the motor, so a stage that leaked
into `direct` shows up as a difference. It runs once per engine: `direct` never reaches the Rust
spatial stage, and the delay read (the one shared part ported to Rust) reproduces numpy.
"""

import math

import numpy as np
import pytest

from aurasync import chain, chain_stages, spatial_docs
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend, eq
from aurasync.dsp.response import THIRDS
from aurasync.dsp.retardo import LineaDeRetardo
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096
TOLERANCE = 1e-9
SPEAKERS = (
    # name, pan, ambience, delay (ms), gain (dB)
    ("left", -0.8, 0.2, 3.25, -1.0),
    ("right", 0.6, 0.3, 0.0, 0.0),
    ("rear", 0.0, 0.9, 11.7, -3.0),
)
EQ_CURVE = [4.0] * len(THIRDS)
"""A boost on every third: the classic render plays it, `direct` must not."""


def _installation() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante(name, f"sink-{name}", pan=pan, ambiente=amb, retardo_ms=delay, ganancia_db=gain, tipo="go4")
            for name, pan, amb, delay, gain in SPEAKERS
        ],
        retardo_traseros_ms=15.0,
    )


def _everything_on(render: str = "direct") -> ChainValues:
    """Every effect switched on, so `direct` has to step around each of them."""
    return ChainValues.from_json(
        {
            "spatial": {"algorithm": render},
            "diffuse": {"algorithm": "noise_tail"},
            "bass": {"algorithm": "protect"},
        }
    )


def _music(seconds: float, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    common = rng.standard_normal(int(seconds * SR))
    left = 0.3 * (common + 0.5 * rng.standard_normal(len(common)))
    right = 0.3 * (common + 0.5 * rng.standard_normal(len(common)))
    return left, right


def _motor(volume_db: float = -2.0, render: str = "direct") -> Motor:
    inst = _installation()
    for p in inst.parlantes:
        p.ecualizacion_db = list(EQ_CURVE)
    return Motor(inst, SR, ecualizar=True, volumen_db=volume_db, chain=_everything_on(render))


def _run(m: Motor, left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
    out = {p.nombre: [] for p in m.instalacion.parlantes}
    for i in range(0, len(left), BLOCK):
        for n, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            out[n].append(x)
    return {n: np.concatenate(v) for n, v in out.items()}


def _delayed(x: np.ndarray, samples: int) -> np.ndarray:
    return np.concatenate([np.zeros(samples), x])[: len(x)]


def _oracle(m: Motor, left: np.ndarray, right: np.ndarray, volume_db: float) -> dict[str, np.ndarray]:
    """Constant-power pan of L/R, the alignment delay, gain, volume and the limiter: nothing else.

    The latencies every render shares stay (the extractor's and the EQ's, equal on every speaker),
    so that switching render never moves the speakers in time: the EQ's is a pure delay here."""
    left_d, right_d = _delayed(left, m.latencia), _delayed(right, m.latencia)
    out = {}
    for name, pan, _amb, delay, gain in SPEAKERS:
        theta = (pan + 1) * math.pi / 4
        x = math.cos(theta) * left_d + math.sin(theta) * right_d
        line = LineaDeRetardo(SR, maximo_ms=250.0, sinc=True)
        line.saltar_a(delay)
        limiter = chain_stages.new_limiter(ChainValues(), SR)
        aligned = np.concatenate([line.procesar(x[i : i + BLOCK]) for i in range(0, len(x), BLOCK)])
        y = _delayed(aligned, eq.LATENCY_SAMPLES) * 10 ** (gain / 20) * 10 ** (volume_db / 20)
        out[name] = np.concatenate([limiter.process(y[i : i + BLOCK]) for i in range(0, len(y), BLOCK)])
    return out


def test_direct_is_the_constant_power_pan_through_delay_gain_volume_and_limiter_only(engine):
    volume_db = -2.0
    left, right = _music(3.0)
    m = _motor(volume_db)
    assert m.render == "direct"
    assert m.espacial is None, "direct never builds the spatial upmix (nor its Rust port)"
    got = _run(m, left, right)
    expected = _oracle(m, left, right, volume_db)
    for name, x in got.items():
        diff = float(np.max(np.abs(x - expected[name])))
        assert diff <= TOLERANCE, f"{name}: max |diff| = {diff:.3g}"
    # The golden reached the limiter (the music is hot), and the delay reads went through the engine.
    assert max(float(np.max(np.abs(x))) for x in got.values()) == pytest.approx(10 ** (-1 / 20), abs=1e-9)
    assert bool(engine.rust_calls) == (engine.name == "rust")
    assert backend.failure() is None


def test_mute_still_works_in_direct():
    left, right = _music(1.0)
    m = _motor()
    m.silenciados = {"rear"}
    out = _run(m, left, right)
    assert np.max(np.abs(out["rear"][BLOCK:])) == 0.0
    assert np.max(np.abs(out["left"][BLOCK:])) > 0.01


def test_direct_leaves_out_the_haas_delay_and_the_eq_curve():
    m = _motor()
    assert m.retardos_efectivos_ms() == {name: delay for name, _p, _a, delay, _g in SPEAKERS}
    assert all(m.curva_sonando(p) is None for p in m.instalacion.parlantes)
    assert m.metricas_cadena()["eq"]["active"] is False
    classic = _motor(render="classic")
    assert classic.retardos_efectivos_ms()["rear"] == pytest.approx(11.7 + 0.9 * 15.0)
    assert classic.curva_sonando(classic.instalacion.parlantes[0]) is not None


def test_switching_to_direct_and_back_goes_through_the_cut():
    left, right = _music(2.0)
    m = _motor(render="classic")
    _run(m, left[: 4 * BLOCK], right[: 4 * BLOCK])
    assert m.aplicar_cadena(_everything_on("direct")) == "cut"
    assert m.render == "classic", "the render changes at the cut's bottom, not before"
    _run(m, left[4 * BLOCK : 8 * BLOCK], right[4 * BLOCK : 8 * BLOCK])
    assert m.render == "direct"
    assert m.retardos_actuales_ms()["rear"] == pytest.approx(11.7)
    assert m.aplicar_cadena(_everything_on("classic")) == "cut"
    _run(m, left[8 * BLOCK : 12 * BLOCK], right[8 * BLOCK : 12 * BLOCK])
    assert m.render == "classic"
    assert m.retardos_actuales_ms()["rear"] == pytest.approx(11.7 + 0.9 * 15.0)
    assert m.curva_sonando(m.instalacion.parlantes[0]) is not None


def test_after_the_switch_direct_sounds_as_a_motor_that_started_in_direct():
    """Once the cut's fade-in and the delay lines have passed, nothing of the processed render is
    left in the output (a decorrelator tail, the Haas delay, the EQ)."""
    left, right = _music(4.0)
    # Under the limiter's ceiling: its state is the one thing that would remember the cut.
    switched = _motor(volume_db=-12.0, render="classic")
    fresh = _motor(volume_db=-12.0)
    head = 6 * BLOCK
    _run(switched, left[:head], right[:head])
    _run(fresh, left[:head], right[:head])
    switched.aplicar_cadena(_everything_on("direct"))
    a = _run(switched, left[head:], right[head:])
    b = _run(fresh, left[head:], right[head:])
    settled = 6 * BLOCK
    for name in a:
        assert np.max(np.abs(a[name][settled:] - b[name][settled:])) <= TOLERANCE, name


def test_a_render_makeup_is_a_ramped_output_gain_that_jumps_at_a_cut():
    left, right = _music(2.0)
    plain = _run(_motor(volume_db=-12.0), left, right)  # under the limiter's ceiling
    m = _motor(volume_db=-12.0)
    m.jump_render_makeup(-6.0)
    quieter = _run(m, left, right)
    ratio = np.sum(quieter["left"][BLOCK:] ** 2) / np.sum(plain["left"][BLOCK:] ** 2)
    assert 10 * np.log10(ratio) == pytest.approx(-6.0, abs=0.01)
    # Moved live, it ramps: no block carries a step.
    m = _motor()
    _run(m, left[:BLOCK], right[:BLOCK])
    m.render_makeup_db = -6.0
    assert m.render_makeup_db == -6.0
    _run(m, left[BLOCK : 2 * BLOCK], right[BLOCK : 2 * BLOCK])
    block_db = m.render_makeup_block_db
    assert isinstance(block_db, np.ndarray)
    assert np.max(np.abs(np.diff(block_db))) < 0.01


def test_the_switch_asks_for_the_render_makeup_at_the_bottom():
    """Whoever keeps the makeups (`render_match.RenderMatch`) answers at the cut's bottom, and the
    makeup jumps with the output at zero: a switch never ramps through the music."""
    asked = []

    def makeup_for(render: str) -> float:
        asked.append(render)
        return {"direct": 2.5, "classic": 0.0}[render]

    left, right = _music(2.0)
    m = _motor(render="classic")
    m.on_render_switch = makeup_for
    m.aplicar_cadena(_everything_on("direct"))
    assert asked == []
    _run(m, left[: 4 * BLOCK], right[: 4 * BLOCK])
    assert asked == ["direct"]
    assert m.render_makeup_db == 2.5
    assert m.metricas_cadena()["spatial"]["makeup_db"] == 2.5
    assert m.metricas_cadena()["spatial"]["render"] == "direct"


def test_leaving_direct_replays_nothing_of_before_it():
    """Review 2026-10-06: the diffuse tail (0.6 s) and the bass filters are not fed in `direct`;
    kept as they were, leaving `direct` replayed the music from before it. Here the music stops
    while `direct` plays: back in classic, after the fade, nothing may sound."""
    left, right = _music(3.0)
    m = _motor(volume_db=-12.0, render="classic")
    _run(m, left, right)
    m.aplicar_cadena(_everything_on("direct"))
    silence = np.zeros(int(2.5 * SR))
    _run(m, silence, silence)
    assert m.render == "direct"
    m.aplicar_cadena(_everything_on("classic"))
    out = _run(m, silence[: 12 * BLOCK], silence[: 12 * BLOCK])
    assert m.render == "classic"
    for name, x in out.items():
        assert np.max(np.abs(x)) < 1e-9, f"{name}: {np.max(np.abs(x)):.3g} of music from before direct"


def test_after_leaving_direct_classic_sounds_as_a_motor_that_started_in_it():
    left, right = _music(8.0)
    switched = _motor(volume_db=-12.0)
    fresh = _motor(volume_db=-12.0, render="classic")
    head = 6 * BLOCK
    _run(switched, left[:head], right[:head])
    _run(fresh, left[:head], right[:head])
    switched.aplicar_cadena(_everything_on("classic"))
    a = _run(switched, left[head:], right[head:])
    b = _run(fresh, left[head:], right[head:])
    settled = round(2.0 * SR)  # the diffuse tail, the decorrelator and the delay lines filled again
    for name in a:
        assert np.max(np.abs(a[name][settled:] - b[name][settled:])) <= 1e-7, name


def test_the_chain_latency_in_direct_leaves_out_the_decorrelator():
    """`direct` skips the decorrelator's group delay (`mean_ms`): a switch moves every speaker by
    that, through the cut, and the reported latency says so."""
    classic, direct = _everything_on("classic"), _everything_on("direct")
    mean = classic.param("decorrelate", "mean_ms")
    assert chain.latency_ms(direct) == pytest.approx(chain.latency_ms(classic) - mean, abs=1e-3)


def test_the_render_figure_has_a_direct_bar():
    ring = {"s0": (-60.0, "principal"), "s1": (60.0, "principal"), "a": (None, "ambient")}
    fig = spatial_docs.figure("render", _everything_on("direct"), ring).to_dict()
    labels = [row["label"] for row in fig["series"]]
    assert "directo" in labels
    row = next(r for r in fig["series"] if r["label"] == "directo")
    assert row["style"] == "current"
    assert row["points"][1][1] == 0.0, "no ambience in direct"
