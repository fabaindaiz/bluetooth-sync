"""The golden run of the engine: what `motor.py` played before the chain refactor.

`docs/superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §4.2: before
`motor.py` was touched, this scenario was recorded with the engine as it was into
`tests/data/golden_motor.npz`. The refactored engine, with the chain at its defaults, must
reproduce it with `max |diff| <= 1e-9` (`tests/test_chain_golden.py`).

The scenario uses only the engine's public API of that day, so it runs on both versions:
pink noise plus synthetic music, three speakers, fixed seeds, and live changes of pan,
ambience, volume, mute, EQ, the extractor, a recalibration-style gain and delay move, and a
preset cut in the middle. The block size alternates between 4096 and 1024 inside one run,
so both sizes (and the boundaries between them) are covered. It runs once with the EQ stage
and once without.

Record again only on purpose, when the sound is meant to change (and say so in the log):

    cd host && hatch run python -m tests.golden_motor
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from aurasync import motor
from aurasync.config import Instalacion, Parlante
from aurasync.dsp.response import THIRDS

SR = 48000
SECONDS = 1.1
SEED = 20261002
PATH = Path(__file__).parent / "data" / "golden_motor.npz"
BLOCKS = (4096, 1024, 1024, 4096, 1024)
CONFIGS = {"eq": True, "noeq": False}


def _curve(peak_db: float, centre_hz: float) -> list[float]:
    """A lift of up to `peak_db` around `centre_hz`, like a calibration's correction."""
    octaves = np.log2(THIRDS / centre_hz)
    return [round(float(v), 2) for v in peak_db * np.exp(-(octaves**2))]


def installation() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante("L", "s0", pan=-0.7, ambiente=0.15, ecualizacion_db=_curve(5.0, 3000.0), tipo="go4"),
            Parlante("R", "s1", pan=0.7, ambiente=0.15, retardo_ms=3.2, ganancia_db=-1.5, tipo="go4"),
            Parlante(
                "B", "s2", pan=0.0, ambiente=0.55, retardo_ms=1.1, ganancia_db=-0.5, ecualizacion_db=_curve(6.0, 150.0)
            ),
        ],
        retardo_traseros_ms=12.0,
    )


def _pink(rng: np.random.Generator, n: int) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spectrum[1:] /= np.sqrt(f[1:])
    spectrum[0] = 0
    x = np.fft.irfft(spectrum, n)
    return x / np.abs(x).max()


def signal() -> tuple[np.ndarray, np.ndarray]:
    """Correlated pink noise under a few panned notes and a loud burst for the limiter."""
    n = round(SECONDS * SR)
    rng = np.random.default_rng(SEED)
    common, left_only, right_only = _pink(rng, n), _pink(rng, n), _pink(rng, n)
    izq = 0.15 * (0.7 * common + 0.3 * left_only)
    der = 0.15 * (0.7 * common + 0.3 * right_only)
    t = np.arange(n) / SR
    for start, f, pan in ((0.05, 220.0, -0.6), (0.3, 330.0, 0.5), (0.55, 440.0, 0.0), (0.8, 82.4, -0.2)):
        env = np.clip((t - start) * 40, 0, 1) * np.exp(-np.clip(t - start, 0, None) * 3)
        note = 0.3 * env * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t))
        izq += (1 - pan) / 2 * note
        der += (1 + pan) / 2 * note
    burst = (t > 0.70) & (t < 0.78)
    izq[burst] *= 8.0
    der[burst] *= 8.0
    return izq, der


def _events(m: motor.Motor) -> dict[int, object]:
    """What a control (or the loop) changes before block `i`."""
    inst = m.instalacion

    def preset() -> None:
        inst.por_nombre("L").pan, inst.por_nombre("L").ambiente = -0.5, 0.3
        inst.por_nombre("B").ganancia_db = -2.0
        m.decorrelacion_activa = False

    def preset_back() -> None:
        m.decorrelacion_activa = True

    def mute() -> None:
        m.silenciados = {"R"}

    def unmute() -> None:
        m.silenciados = set()

    def ambience_up() -> None:
        # 0.25 x 12 ms more of rear delay: too slow for the ramp, it goes through the cut.
        inst.por_nombre("B").ambiente = 0.8
        m.actualizar_desde_control()

    def loop_move() -> None:
        r = inst.por_nombre("R")
        r.ganancia_db, r.retardo_ms = -3.0, 3.35
        m.actualizar()

    def new_eq() -> None:
        inst.por_nombre("L").ecualizacion_db = _curve(4.0, 6000.0)
        m.actualizar_ecualizacion()

    def eq_off() -> None:
        m.ecualizacion_activa = False
        m.actualizar_ecualizacion()

    def rear_delay() -> None:
        inst.retardo_traseros_ms = 12.4
        m.actualizar_desde_control()

    return {
        1: lambda: setattr(m, "volumen_db", -4.0),
        2: lambda: setattr(inst.por_nombre("L"), "pan", -0.2),
        3: ambience_up,
        4: mute,
        5: loop_move,
        6: unmute,
        7: lambda: setattr(m, "extraer_ambiente_activo", False),
        8: lambda: m.cortar(preset),
        10: lambda: setattr(m, "extraer_ambiente_activo", True),
        11: new_eq,
        12: lambda: setattr(m, "volumen_db", 0.0),
        14: eq_off,
        15: rear_delay,
        17: lambda: m.cortar(preset_back),
    }


def run(motor_factory, *, ecualizar: bool) -> dict[str, np.ndarray]:
    """Play the scenario through a motor built by `motor_factory(installation, sr, ecualizar)`."""
    m = motor_factory(installation(), SR, ecualizar)
    izq, der = signal()
    events = _events(m)
    out: dict[str, list[np.ndarray]] = {p.nombre: [] for p in m.instalacion.parlantes}
    i = start = 0
    while start < len(izq):
        if i in events:
            events[i]()
        end = min(len(izq), start + BLOCKS[i % len(BLOCKS)])
        for name, block in m.procesar(izq[start:end], der[start:end]).items():
            out[name].append(block)
        start, i = end, i + 1
    return {name: np.concatenate(parts) for name, parts in out.items()}


def legacy_motor(inst: Instalacion, sr: int, ecualizar: bool) -> motor.Motor:  # noqa: FBT001
    return motor.Motor(inst, sr, ecualizar=ecualizar)


def record() -> None:
    arrays = {}
    for label, ecualizar in CONFIGS.items():
        for name, x in run(legacy_motor, ecualizar=ecualizar).items():
            arrays[f"{label}/{name}"] = x
    PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(PATH, **arrays)


if __name__ == "__main__":
    record()
    print(f"recorded {PATH} ({PATH.stat().st_size / 1e6:.2f} MB)")  # noqa: T201
