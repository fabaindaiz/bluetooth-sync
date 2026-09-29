#!/usr/bin/env python3
"""Valida `medir-n.py` con 4 canales y desfases conocidos, sin tocar hardware.

Dos condiciones que hay que comprobar antes de creerle a una medición de 4 parlantes:

1. **Distorsión armónica.** Se comprueba metiendo armónicos a −30 dB, peor que un
   parlante razonable. Las frecuencias se eligieron **sin relación armónica** justamente
   por esto (ver `medir-n.py`).
2. **Reverberación** con desfases grandes, que es lo que rompió la versión de 2 canales.
3. **Canales mudos**: un parlante sin stream no debe producir un número, sino avisar.

La tolerancia no es la misma en los dos regímenes, y está declarada en `medir-n.py`:
con señal limpia el error es <0,05 ms, y con reverberación fuerte llega a ~2,2 ms.

Uso:  probes/e6-a2dp/test-medir-n.py
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np

RAFAGAS = 6
# Las mismas frecuencias que usa medir-n.py: sin relación armónica entre ellas.
CANALES = [("FL", 2350.0, "A"), ("FR", 3250.0, "B"), ("RL", 4150.0, "C"), ("RR", 5200.0, "D")]
# (desfases reales en ms respecto de A, con armónicos, con reverberación)
CASOS = (
    ((0.0, 0.0, 0.0), False, False),
    ((2.0, 5.0, 12.0), False, False),
    ((2.0, 5.0, 12.0), True, False),
    ((2.0, 5.0, 12.0), True, True),
    ((50.0, -8.0, 120.0), True, True),
    ((15.0, 30.0, 45.0), True, True),
)
# Canales que no suenan, para comprobar que se detectan como mudos en vez de devolver un
# número inventado a partir de la fuga de otro parlante. Es el error que apareció de
# verdad con las frecuencias armónicas.
CASOS_MUDOS = (("C",), ("B", "D"))


def cargar(nombre: str):
    ruta = Path(__file__).with_name(nombre)
    spec = importlib.util.spec_from_file_location(nombre.replace("-", "_").removesuffix(".py"), ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sintetico(m, n_mod, desfases, *, armonicos: bool, reverb: bool, ruido: float = 0.002,
              mudos: tuple[str, ...] = ()):
    """Simula la grabación: el canal A puntual y los otros corridos lo indicado.

    Los canales en `mudos` no emiten nada, para probar que se detectan como tales.
    """
    total = int(m.SR * (m.SILENCIO_INICIAL + RAFAGAS * m.PERIODO + 0.7))
    x = np.random.default_rng(0).normal(0, ruido, total)
    largo = int(m.SR * m.DUR_RAFAGA)
    v = np.hanning(largo)
    t = np.arange(largo) / m.SR
    offs = [0.0, *desfases]
    for k in range(RAFAGAS):
        base = int(m.SR * (m.SILENCIO_INICIAL + k * m.PERIODO))
        for (_, frec, nombre), off_ms in zip(CANALES, offs, strict=True):
            if nombre in mudos:
                continue
            i = base + int(round(m.SR * off_ms / 1000))
            if i < 0 or i + largo > total:
                continue
            onda = np.sin(2 * math.pi * frec * t)
            if armonicos:
                # −30 dB en el 2.º y 3.er armónico: peor que un parlante razonable.
                onda = onda + 0.0316 * np.sin(4 * math.pi * frec * t) + 0.0316 * np.sin(6 * math.pi * frec * t)
            x[i : i + largo] += 0.28 * v * onda
    if reverb:
        y = x.copy()
        for retardo_ms, ganancia in ((11, 0.45), (23, 0.28), (37, 0.17), (53, 0.10)):
            d = int(m.SR * retardo_ms / 1000)
            y[d:] += ganancia * x[:-d]
        x = y
    return x


def main() -> int:
    dsp = cargar("medir-desfase.py")
    n_mod = cargar("medir-n.py")
    print(f"4 canales · {RAFAGAS} ráfagas por caso")
    print(f"tolerancia: {n_mod.PRECISION_MS:.2f} ms limpio · {n_mod.PRECISION_REVERB_MS:.2f} ms con reverberación\n")
    cab = f"{'desfases reales':>22s}  {'arm':>3s} {'rev':>3s}  {'medidos':>26s}  {'error máx':>9s}  {'tol':>5s}  {'n':>3s}"
    print(cab)
    ok = True
    for reales, armonicos, reverb in CASOS:
        x = sintetico(dsp, n_mod, reales, armonicos=armonicos, reverb=reverb)
        filas, _, _ = n_mod.medir_n(dsp, x, CANALES)
        if len(filas) != RAFAGAS:
            print(f"{str(reales):>22s}  {'sí' if armonicos else 'no':>3s} {'sí' if reverb else 'no':>3s}  "
                  f"{'ráfagas: ' + str(len(filas)):>26s}  {'—':>9s}  {len(filas):>3}  ← esperaba {RAFAGAS}")
            ok = False
            continue
        medidos, errores = [], []
        for nombre, real in zip([c[2] for c in CANALES[1:]], reales, strict=True):
            d = np.median([f[nombre] - f["A"] for f in filas])
            medidos.append(d)
            errores.append(abs(d - real))
        err = max(errores)
        txt = ", ".join(f"{v:+.1f}" for v in medidos)
        tol = n_mod.PRECISION_REVERB_MS if reverb else n_mod.PRECISION_MS
        print(f"{str(reales):>22s}  {'sí' if armonicos else 'no':>3s} {'sí' if reverb else 'no':>3s}  "
              f"{txt:>26s}  {err:>9.2f}  {tol:>5.2f}  {len(filas):>3}")
        # La tolerancia depende de la condición, y está declarada en medir-n.py: con
        # reverberación el método pierde precisión y eso se asume, no se esconde.
        tolerancia = n_mod.PRECISION_REVERB_MS if reverb else n_mod.PRECISION_MS
        if err > tolerancia:
            ok = False
    print("\n== canales mudos: se tienen que detectar, no inventar un número ==")
    print(f"{'mudos de verdad':>20s}  {'detectados como mudos':>24s}")
    for mudos in CASOS_MUDOS:
        x = sintetico(dsp, n_mod, (2.0, 5.0, 12.0), armonicos=True, reverb=True, mudos=mudos)
        _, _, nivel = n_mod.medir_n(dsp, x, CANALES)
        detectados = tuple(sorted(n for n, v in nivel.items() if v < n_mod.NIVEL_MINIMO_RELATIVO))
        bien = detectados == tuple(sorted(mudos))
        print(f"{str(mudos):>20s}  {str(detectados):>24s}  {'ok' if bien else 'MAL'}")
        if not bien:
            ok = False

    print("\n→", "OK" if ok else "REVISAR: algún caso se sale de la precisión o falla el conteo")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
