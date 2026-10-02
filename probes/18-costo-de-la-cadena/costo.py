"""Costo del motor con la cadena completa encendida, y de los medidores de sonoridad.

Spec 2026-10-02 §5 (presupuesto: el motor ≥ 20× tiempo real con todo lo nuevo encendido) y
§6.2 (los medidores de sonoridad < 0,5 ms por bloque para 3 parlantes). Corre en cualquier
equipo; anota el resultado con el equipo en que se midió.

    cd host && hatch run python ../probes/18-costo-de-la-cadena/costo.py

MEDIDO 2026-10-02 en el Mac (arm64), con otros procesos cargando el equipo (load ~10), tres
corridas: por defecto 32-38× tiempo real; todo encendido con `protect` + armónicos 19-22×; con
`crossover` a un Charge 6 22-24×; los medidores de sonoridad 0,30-0,42 ms por bloque; el
resumen de calidad 0,05 ms. El presupuesto de la spec (≥ 20× en PC-Ryzen5) queda justo en el
Mac con `protect` + armónicos: hay que medirlo en PC-Ryzen5.
"""

from __future__ import annotations

import platform
import time

import numpy as np

from aurasync import chain, motor
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.quality import QualityMeter

SR, BLOCK, BLOCKS = 48000, 4096, 120


def installation(with_charge: bool) -> Instalacion:
    speakers = [
        Parlante("JBL Go 4 Red", "s0", pan=-0.7, ambiente=0.15, ecualizacion_db=[3.0] * 27),
        Parlante("JBL Go 4 Black", "s1", pan=0.7, ambiente=0.15, ecualizacion_db=[3.0] * 27),
        Parlante("JBL Charge 6" if with_charge else "JBL Go 4 Blue", "s2", ambiente=0.55, ecualizacion_db=[3.0] * 27),
    ]
    return Instalacion(parlantes=speakers)


def everything(bass: str) -> ChainValues:
    v = ChainValues()
    v = v.with_algorithm("diffuse", "noise_tail").with_algorithm("bass", bass).with_algorithm("limiter", "true_peak")
    if bass == "protect":
        v = v.with_change(chain.validate_set("bass", params={"harmonics_db": 0.0}))
    return v.with_change(chain.validate_set("eq", params={"budget_db": 3.0, "treble_cap_db": 3.0}))


def per_block_ms(m: motor.Motor, x: np.ndarray, quality: QualityMeter | None = None) -> np.ndarray:
    times = []
    for i in range(BLOCKS):
        a, b = x[i * BLOCK : (i + 1) * BLOCK, 0], x[i * BLOCK : (i + 1) * BLOCK, 1]
        t = time.perf_counter()
        out = m.procesar(a, b)
        if quality is not None:
            quality.push((a, b), out)
        times.append(time.perf_counter() - t)
    return np.array(times[10:]) * 1000


def main() -> None:
    rng = np.random.default_rng(0)
    x = 0.1 * rng.standard_normal((BLOCK * BLOCKS, 2))
    budget = BLOCK / SR * 1000
    print(f"equipo: {platform.node()} ({platform.machine()}), bloque {BLOCK}, 3 parlantes")
    cases = {
        "por defecto": (False, ChainValues()),
        "todo encendido (protect + armónicos)": (False, everything("protect")),
        "todo encendido (crossover a un Charge 6)": (True, everything("crossover")),
    }
    for label, (charge, values) in cases.items():
        m = motor.Motor(installation(charge), SR, ecualizar=True, chain=values, bloque=BLOCK)
        ms = per_block_ms(m, x)
        print(f"  {label}: mediana {np.median(ms):.2f} ms, p95 {np.percentile(ms, 95):.2f} ms → {budget / np.median(ms):.0f}× tiempo real")
    m = motor.Motor(installation(False), SR, ecualizar=True, bloque=BLOCK)
    quality = QualityMeter(SR, [p.nombre for p in m.instalacion.parlantes])
    with_q = per_block_ms(m, x, quality)
    m = motor.Motor(installation(False), SR, ecualizar=True, bloque=BLOCK)
    without = per_block_ms(m, x)
    print(f"  medidores de sonoridad (entrada + 3 salidas): {np.median(with_q) - np.median(without):.3f} ms por bloque (mediana)")
    times = []
    for _ in range(50):
        t = time.perf_counter()
        quality.summary(limiter_pct={})
        times.append(time.perf_counter() - t)
    print(f"  resumen de calidad (2 Hz): {np.median(times) * 1000:.3f} ms")


if __name__ == "__main__":
    main()
