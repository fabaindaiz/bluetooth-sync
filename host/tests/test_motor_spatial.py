"""The spatial stage in the motor (spec 2026-10-04 §3-§4): classic is untouched, spatial renders
direct to the principals and ambience to the ambients."""

import numpy as np
import pytest

from aurasync import control
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp.spatial import from_character
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096


def _installation(ambient=()):
    angles = control.auto_angles(3)
    parlantes = []
    for i, a in enumerate(angles):
        pan, amb = control.role_from_angle(a)
        parlantes.append(Parlante(f"s{i}", f"sink{i}", pan=pan, ambiente=amb))
    parlantes.append(Parlante("amb", "sink9", role_kind="ambient" if "amb" in ambient else "principal"))
    return Instalacion(parlantes=parlantes)


def _chain(render="spatial", **params):
    return ChainValues.from_json({"spatial": {"algorithm": render, "params": params}})


def _run(m, left, right):
    out = {p.nombre: [] for p in m.instalacion.parlantes}
    for i in range(0, len(left), BLOCK):
        for n, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            out[n].append(x)
    return {n: np.concatenate(v) for n, v in out.items()}


def _music(seconds=3.0, seed=0):
    rng = np.random.default_rng(seed)
    common = rng.standard_normal(int(seconds * SR))
    return 0.1 * (common + 0.4 * rng.standard_normal(len(common))), 0.1 * (
        common + 0.4 * rng.standard_normal(len(common))
    )


def test_classic_is_bit_exact():
    left, right = _music()
    plain = _run(Motor(_installation(), SR, semilla=1), left, right)
    classic = _run(Motor(_installation(), SR, semilla=1, chain=_chain("classic")), left, right)
    for name, x in plain.items():
        assert np.array_equal(x, classic[name]), name


def test_spatial_output_differs_and_is_finite():
    left, right = _music()
    classic = _run(Motor(_installation(), SR, semilla=1), left, right)
    spatial = _run(Motor(_installation(), SR, semilla=1, chain=_chain()), left, right)
    for name, x in spatial.items():
        assert np.all(np.isfinite(x))
        assert not np.allclose(x, classic[name])


def test_render_switch_goes_through_the_cut():
    m = Motor(_installation(), SR, semilla=1)
    assert m.aplicar_cadena(_chain()) == "cut"
    assert m.en_corte


def test_manual_off_uses_character():
    m = Motor(_installation(), SR, semilla=1, chain=_chain(character=0.8, arc_deg=40.0))
    assert m.espacial.params == from_character(0.8)
    m2 = Motor(_installation(), SR, semilla=1, chain=_chain(character=0.8, manual=True, arc_deg=40.0))
    assert m2.espacial.params.arc_deg == pytest.approx(40.0)


def test_a_character_change_is_live():
    m = Motor(_installation(), SR, semilla=1, chain=_chain(character=0.2))
    assert m.aplicar_cadena(_chain(character=0.9)) == "live"
    assert m.espacial.params == from_character(0.9)


def _three():
    ps = []
    for i, a in enumerate(control.auto_angles(3)):
        pan, amb = control.role_from_angle(a)
        ps.append(Parlante(f"s{i}", f"k{i}", pan=pan, ambiente=amb))
    return Instalacion(parlantes=ps)


def _click_ratio(m, action, block=1024):
    """Peak second difference around `action` against the steady state, on a 300 Hz sine (review 2026-10-04)."""
    t = np.arange(6 * SR) / SR
    sig = 0.1 * np.sin(2 * np.pi * 300 * t)
    at = 2 * SR - (2 * SR) % block
    out = []
    for i in range(0, len(sig), block):
        if i == at:
            action(m)
        out.append(m.procesar(sig[i : i + block], sig[i : i + block])["s0"])
    d2 = np.abs(np.diff(np.concatenate(out), 2))
    return float(d2[2 * SR : 4 * SR].max() / d2[SR : 2 * SR].max())


def test_switching_to_spatial_does_not_click():
    m = Motor(_three(), SR, semilla=1)
    assert _click_ratio(m, lambda m: m.aplicar_cadena(_chain())) < 3


def test_a_layout_change_does_not_click():
    def nudge(m):
        m.instalacion.parlantes[1].pan -= 0.01
        m.actualizar()

    assert _click_ratio(Motor(_three(), SR, semilla=1, chain=_chain()), nudge) < 3


def test_a_live_character_change_does_not_click():
    m = Motor(_three(), SR, semilla=1, chain=_chain(character=0.5))
    assert _click_ratio(m, lambda m: m.aplicar_cadena(_chain(character=1.0))) < 3


def test_a_front_rear_swap_rebuilds_the_ring():
    m = Motor(_installation(), SR, semilla=1, chain=_chain())
    a, c = m.instalacion.parlantes[0], m.instalacion.parlantes[2]
    (a.pan, a.ambiente), (c.pan, c.ambiente) = (c.pan, c.ambiente), (a.pan, a.ambiente)
    m.actualizar_desde_control()
    s = np.zeros(BLOCK)
    for _ in range(20):
        m.procesar(s, s)
    ring = dict(zip(m.espacial.principal, m.espacial._ring, strict=True))  # noqa: SLF001
    assert ring["s2"] == pytest.approx(-60, abs=1)
    assert ring["s0"] == pytest.approx(180, abs=1)


@pytest.mark.parametrize(("n", "ambient"), [(2, False), (3, False), (4, False), (3, True)])
def test_spatial_is_as_loud_as_classic(n, ambient):
    """Spec §6: switching the render must not change the loudness by more than 1 dB (power sum
    of the speakers), or the A/B would compare volume, not space. 3 principals were +1.4 dB."""

    def inst():
        ps = []
        for i, a in enumerate(control.auto_angles(n)):
            pan, amb = control.role_from_angle(a)
            ps.append(Parlante(f"s{i}", f"k{i}", pan=pan, ambiente=amb))
        if ambient:
            ps.append(Parlante("amb", "k9", role_kind="ambient"))
        return Instalacion(parlantes=ps)

    left, right = _music(5.0)
    power = {}
    for render in ("classic", "spatial"):
        out = _run(Motor(inst(), SR, semilla=1, chain=_chain(render)), left, right)
        power[render] = sum(float(x[SR:] @ x[SR:]) for x in out.values())
    assert abs(10 * np.log10(power["spatial"] / power["classic"])) < 1.0, power


def test_ambient_speaker_gets_no_direct():
    # A source hard left: no ambience in it, so the ambient speaker stays (almost) silent.
    s = np.random.default_rng(2).standard_normal(3 * SR) * 0.1
    out = _run(Motor(_installation(ambient=("amb",)), SR, semilla=1, chain=_chain()), s, np.zeros_like(s))
    loud = max(float(np.sum(x[SR:] ** 2)) for n, x in out.items() if n != "amb")
    quiet = float(np.sum(out["amb"][SR:] ** 2))
    assert 10 * np.log10(loud / max(quiet, 1e-30)) > 20


def test_front_intact_builds_the_renderer_with_the_front_untouched():
    m = Motor(_installation(), SR, semilla=1, chain=_chain("front"))
    assert m.espacial is not None
    assert m.espacial.params.front_intact
    assert m.espacial.front == ("s0", "s1")


def test_switching_spatial_to_front_keeps_a_renderer():
    m = Motor(_installation(), SR, semilla=1, chain=_chain("spatial"))
    m.aplicar_cadena(_chain("front"))
    left, right = _music(1.0)
    _run(m, left, right)
    assert m.espacial is not None
    assert m.espacial.params.front_intact
