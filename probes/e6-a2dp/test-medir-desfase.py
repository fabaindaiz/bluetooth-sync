#!/usr/bin/env python3
"""Valida `medir-desfase.py` contra desfases conocidos, sin tocar hardware.

Sintetiza lo que grabaría el micrófono —la ráfaga de f1 puntual y la de f2 corrida un
desfase conocido, más ruido— y comprueba que el análisis recupera ese desfase **y que
cuenta bien las ráfagas**. Si esto falla, cualquier número medido con parlantes de
verdad no vale nada.

Usa las mismas funciones que la medición real (`medir`), no una copia.

El caso "reverberante" está porque la primera versión del detector contaba 13 ráfagas
donde había 6: la envolvente ondula dentro del tono, baja del umbral y vuelve a
disparar. Un caso limpio no lo mostraba.

Uso:  probes/e6-a2dp/test-medir-desfase.py
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np

# (desfase real en ms, con reverberación)
CASOS = (
    (0.0, False),
    (1.0, False),
    (2.5, False),
    (5.0, False),
    (7.3, False),
    (15.0, False),
    (40.0, False),
    (-6.0, False),
    (0.0, True),
    (4.0, True),
    (12.0, True),
    # Desfases grandes con reverberación: es el caso que rompía la ventana de análisis.
    # Con A2DP y códecs distintos el desfase real llegó a ~55 ms.
    (40.0, True),
    (55.0, True),
    (120.0, True),
)
RAFAGAS = 6


def cargar():
    ruta = Path(__file__).with_name("medir-desfase.py")
    spec = importlib.util.spec_from_file_location("medir_desfase", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sintetico(m, desfase_ms: float, *, reverb: bool, n: int = RAFAGAS, ruido: float = 0.002):
    """Simula la grabación del micrófono con un desfase conocido."""
    total = int(m.SR * (m.SILENCIO_INICIAL + n * m.PERIODO + 0.5))
    x = np.random.default_rng(0).normal(0, ruido, total)
    largo = int(m.SR * m.DUR_RAFAGA)
    v = np.hanning(largo)
    t = np.arange(largo) / m.SR
    off = int(round(m.SR * desfase_ms / 1000))
    for k in range(n):
        i = int(m.SR * (m.SILENCIO_INICIAL + k * m.PERIODO))
        x[i : i + largo] += 0.30 * v * np.sin(2 * math.pi * 1000 * t)
        x[i + off : i + off + largo] += 0.30 * v * np.sin(2 * math.pi * 3000 * t)
    if reverb:
        # Ecos decrecientes: hacen ondular la envolvente, que es lo que rompía el
        # detector original.
        y = x.copy()
        for retardo_ms, ganancia in ((11, 0.45), (23, 0.28), (37, 0.17), (53, 0.10)):
            d = int(m.SR * retardo_ms / 1000)
            y[d:] += ganancia * x[:-d]
        x = y
    return x


def main() -> int:
    m = cargar()
    print(f"precisión declarada: ~{m.PRECISION_MS:.1f} ms · {RAFAGAS} ráfagas por caso\n")
    print(f"{'real (ms)':>10}  {'reverb':>7}  {'medido':>9}  {'error':>8}  {'n':>4}")
    ok = True
    for real, reverb in CASOS:
        x = sintetico(m, real, reverb=reverb)
        filas, _ = m.medir(x, 1000.0, 3000.0)
        d = np.array([f[2] for f in filas])
        etiqueta = "sí" if reverb else "no"
        if len(d) == 0:
            print(f"{real:>10.1f}  {etiqueta:>7}  {'sin detectar':>9}")
            ok = False
            continue
        err = d.mean() - real
        marca = "" if len(d) == RAFAGAS else f"  ← esperaba {RAFAGAS}"
        print(f"{real:>10.1f}  {etiqueta:>7}  {d.mean():>+9.2f}  {err:>+8.2f}  {len(d):>4}{marca}")
        if abs(err) > m.PRECISION_MS or len(d) != RAFAGAS:
            ok = False
    print("\n→", "OK" if ok else "REVISAR: algún caso falla en el valor o en el conteo")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
