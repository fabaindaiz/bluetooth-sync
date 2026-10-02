"""What changes while audio plays (spec §6): no steps, the same latency, and the fade."""

import numpy as np

from aurasync import motor
from aurasync.config import Instalacion, Parlante

SR = 48000
BLOCK = 1024


def _installation() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante("L", "s0", pan=-0.7, ambiente=0.0),
            Parlante("R", "s1", pan=0.7, ambiente=0.0),
            Parlante("B", "s2", pan=0.0, ambiente=0.5),
        ]
    )


def _tone(n: int, f: float = 440.0):
    t = np.arange(n) / SR
    return 0.5 * np.sin(2 * np.pi * f * t), 0.5 * np.sin(2 * np.pi * f * 1.01 * t)


def _stream(m: motor.Motor, n_blocks: int, change_at: int | None = None, change=None):
    izq, der = _tone(BLOCK * n_blocks)
    out = {p.nombre: [] for p in m.instalacion.parlantes}
    for i in range(n_blocks):
        if i == change_at:
            change()
        blocks = m.procesar(izq[i * BLOCK : (i + 1) * BLOCK], der[i * BLOCK : (i + 1) * BLOCK])
        for k, v in blocks.items():
            out[k].append(v)
    return {k: np.concatenate(v) for k, v in out.items()}


def _max_jump(x: np.ndarray) -> float:
    return float(np.max(np.abs(np.diff(x))))


def test_moving_pan_mid_stream_has_no_step():
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    reference = _max_jump(_stream(motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False), 40)["L"])

    def change():
        m.instalacion.por_nombre("L").pan = 0.9

    out = _stream(m, 40, change_at=10, change=change)["L"]
    assert _max_jump(out) <= reference * 1.05


def test_raw_pan_change_would_have_stepped():
    """The control of the previous test: without smoothing, the same change is a step."""
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    for s in m._pan.values():  # noqa: SLF001
        s.rate = 1e9

    def change():
        m.instalacion.por_nombre("L").pan = 0.9

    stepped = _stream(m, 40, change_at=10, change=change)["L"]
    smooth = _stream(motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False), 40)["L"]
    assert _max_jump(stepped) > _max_jump(smooth) * 1.5


def test_turning_the_extractor_off_keeps_the_latency():
    """An impulse comes out at the same sample with the extractor on or off."""
    inst = Instalacion(parlantes=[Parlante("A", "s0", pan=-1.0, ambiente=0.0), Parlante("B", "s1", pan=1.0)])
    impulse = np.zeros(SR)
    impulse[1000] = 1.0

    def peak(*, active: bool) -> int:
        m = motor.Motor(inst, SR, decorrelar=False)
        m.extraer_ambiente_activo = active
        m._mezcla_ambiente.jump()  # noqa: SLF001
        return int(np.argmax(np.abs(motor.procesar_completo(m, impulse, impulse * 0, BLOCK)["A"])))

    assert peak(active=True) == peak(active=False)
    m = motor.Motor(inst, SR, decorrelar=False)
    assert peak(active=False) == 1000 + m.latencia + m.latencia_retardo


def test_decorrelate_changes_only_at_the_bottom_of_the_fade():
    m = motor.Motor(_installation(), SR, extraer_ambiente=False)
    _stream(m, 5)
    m.cortar(lambda: setattr(m, "decorrelacion_activa", False))
    assert m.decorrelacion_activa  # not yet
    assert m.en_corte
    _stream(m, 5)  # 5120 samples, past the 3840 of the fade-out
    assert not m.decorrelacion_activa
    _stream(m, 5)
    assert not m.en_corte


def test_fade_output_is_continuous_and_reaches_zero():
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    reference = _max_jump(_stream(motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False), 30)["L"])

    def change():
        m.cortar(lambda: setattr(m.instalacion.por_nombre("L"), "pan", 1.0))

    out = _stream(m, 30, change_at=5, change=change)["L"]
    assert np.min(np.abs(out[5 * BLOCK : 15 * BLOCK])) == 0.0
    assert _max_jump(out) <= reference * 1.05


def test_volume_ramps_instead_of_stepping():
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    reference = _max_jump(_stream(motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False), 30)["L"])

    def change():
        m.volumen_db = -30.0

    out = _stream(m, 60, change_at=5, change=change)["L"]
    assert _max_jump(out) <= reference * 1.05
    tail = out[-BLOCK * 5 :]
    full = _stream(motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False), 60)["L"][-BLOCK * 5 :]
    assert np.allclose(tail, full * 10 ** (-30 / 20))


def test_large_delay_change_from_a_control_goes_through_the_fade():
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    m.instalacion.retardo_traseros_ms = 40.0  # B moves 0.5 * 28 = 14 ms: 28 s of ramp
    m.actualizar_desde_control()
    assert m.en_corte
    _stream(m, 10)
    assert abs(m.retardos_actuales_ms()["B"] - 20.0) < 1e-9


def test_small_delay_change_from_a_control_ramps():
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    m.instalacion.retardo_traseros_ms = 13.0  # B moves 0.5 ms: 1 s of ramp
    m.actualizar_desde_control()
    assert not m.en_corte


def test_the_loop_still_ramps_large_changes():
    """`actualizar` (the recalibration loop) never cuts, whatever the size."""
    m = motor.Motor(_installation(), SR, extraer_ambiente=False, decorrelar=False)
    m.instalacion.por_nombre("L").retardo_ms = 10.0
    m.actualizar()
    assert not m.en_corte
