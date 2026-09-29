#!/usr/bin/env python3
"""Prueba el lazo de recalibración en simulación, sin parlantes ni micrófono.

Se simula el micrófono sumando lo que emitió cada parlante con un retardo conocido, más
una cola reverberante y ruido. Como el retardo verdadero se conoce, se puede medir **el
error del estimador**, que con parlantes de verdad es justamente lo que no se puede saber.

Responde tres preguntas:

1. ¿acierta el estimador cuando un parlante llega **antes** que la mediana? (destapó un
   error real en `gcc_phat`);
2. ¿se puede medir usando **el propio contenido** como referencia, que es lo que
   permitiría recalibrar sin interrumpir?;
3. cuando falla, ¿lo dice?

    cd host && hatch run python ../probes/lazo-simulado/simular.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

from aurasync import medicion  # noqa: E402
from aurasync.config import Instalacion, Parlante  # noqa: E402
from aurasync.motor import Motor  # noqa: E402

SR = 48000
REALES = {"izq": 0.0, "der": 3.4, "atras": 7.1}
"""El desfase que el lazo tiene que descubrir. Del orden de lo que midió E6 entre Go 4."""

ESPERADO = {n: max(REALES.values()) - REALES[n] for n in REALES}
"""La corrección correcta: al que llega último no se le agrega nada."""

rng = np.random.default_rng(7)


def reiniciar_azar() -> None:
    """Cada prueba arranca con la misma semilla, o sus números no serían reproducibles:
    el generador es compartido y el orden en que se lo consume cambiaría el resultado."""
    global rng  # noqa: PLW0603
    rng = np.random.default_rng(7)


def musica(segundos: float, sr: int = SR) -> np.ndarray:
    """Ruido rosa con parciales que entran y salen: estructura espectral de música."""
    n = int(sr * segundos)
    espectro = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / sr)
    espectro[1:] /= np.sqrt(f[1:])
    x = np.fft.irfft(espectro, n)
    t = np.arange(n) / sr
    for hz in (110, 220, 330, 587):
        envolvente = 0.5 * (1 + np.sin(2 * np.pi * (0.3 + hz / 2000) * t))
        x += 0.3 * envolvente * np.sin(2 * np.pi * hz * t)
    return x / (np.abs(x).max() + 1e-9) * 0.5


def microfono(salidas: dict[str, np.ndarray], reales_ms=REALES, sr=SR, ruido=0.02, reverb=True) -> np.ndarray:
    """Lo que captaría el micrófono: la suma de los parlantes, con sala y ruido."""
    n = max(len(v) for v in salidas.values())
    mic = np.zeros(n + int(sr * 0.2))
    for nombre, x in salidas.items():
        d = int(round(sr * reales_ms[nombre] / 1000))
        mic[d : d + len(x)] += x
    if reverb:
        ir = np.zeros(int(sr * 0.15))
        ir[0] = 1.0
        golpes = rng.integers(1, len(ir), 400)
        ir[golpes] += rng.standard_normal(400) * 0.3 * np.exp(-golpes / (sr * 0.05))
        mic = np.convolve(mic, ir)[: len(mic)]
    return mic + ruido * rng.standard_normal(len(mic))


def instalacion() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante("izq", "s1", pan=-0.8, ambiente=0.2),
            Parlante("der", "s2", pan=+0.8, ambiente=0.2),
            Parlante("atras", "s3", pan=0.0, ambiente=0.9),
        ],
        retardo_traseros_ms=12.0,
    )


def error_de(cal) -> float:
    return max(abs(cal.retardos_ms[n] - ESPERADO[n]) for n in REALES)


def correlacion_cruzada_maxima(salidas: dict[str, np.ndarray]) -> float:
    """Cuánto se parecen entre sí las referencias. Por FFT: la directa es O(n^2)."""

    def corr(a, b):
        k = min(len(a), len(b))
        a, b = a[:k] - a[:k].mean(), b[:k] - b[:k].mean()
        m = 1 << (2 * k - 1).bit_length()
        c = np.fft.irfft(np.fft.rfft(a, m) * np.conj(np.fft.rfft(b, m)), m)
        return float(np.abs(c).max() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))

    nombres = list(salidas)
    return max(corr(salidas[a], salidas[b]) for i, a in enumerate(nombres) for b in nombres[i + 1 :])


# -- 1. el parlante que llega antes que la mediana ------------------------------------


def prueba_retardo_negativo() -> None:
    reiniciar_azar()
    print("\n1. Un parlante que llega ANTES que el desfase grueso (referencias independientes)")
    print("   El grueso es la mediana, así que el de 0 ms queda con residuo negativo.\n")
    for etiqueta, kw in (
        ("sin reverb ni ruido", {"reverb": False, "ruido": 0.0}),
        ("sin reverb, con ruido", {"reverb": False, "ruido": 0.02}),
        ("con reverb y ruido", {"reverb": True, "ruido": 0.02}),
    ):
        refs = {n: np.random.default_rng(i).standard_normal(SR * 10) * 0.3 for i, n in enumerate(REALES)}
        cal = medicion.calibrar(microfono(refs, **kw), refs, SR)
        detalle = "  ".join(f"{n}={cal.retardos_ms[n]:+6.2f} (esp {ESPERADO[n]:+5.2f})" for n in REALES)
        print(f"   {etiqueta:<22} grueso={cal.desfase_grueso_ms:>6.2f}  error={error_de(cal):>6.2f} ms   {detalle}")

    print("\n   Y lo mismo con el mínimo en cero, que es lo que hacía el código antes del arreglo:")
    for etiqueta, kw in (
        ("sin reverb ni ruido", {"reverb": False, "ruido": 0.0}),
        ("con reverb y ruido", {"reverb": True, "ruido": 0.02}),
    ):
        refs = {n: np.random.default_rng(i).standard_normal(SR * 10) * 0.3 for i, n in enumerate(REALES)}
        mic = microfono(refs, **kw)
        grueso = medicion.alineacion_gruesa(mic, refs, SR)
        alineadas = medicion.alinear(mic, refs, grueso, SR)
        for minimo in (0.0, -120.0):
            medianas, dispersiones = medicion.calibrar_por_ventanas(
                mic, alineadas, SR, ventana_s=0.5, retardo_maximo_ms=120.0, retardo_minimo_ms=minimo
            )
            base = medianas["izq"]
            correcciones = medicion.correcciones({n: v - base for n, v in medianas.items()})
            err = max(abs(correcciones[n] - ESPERADO[n]) for n in REALES)
            print(
                f"   {etiqueta:<22} mínimo={minimo:>7.1f} ms  error={err:>6.2f} ms  "
                f"dispersión={max(dispersiones.values()):>5.2f} ms   "
                + "  ".join(f"{n}={correcciones[n]:+7.2f}" for n in REALES)
            )


# -- 2. el contenido como referencia ---------------------------------------------------


def prueba_contenido() -> None:
    reiniciar_azar()
    print("\n2. ¿Sirve el propio contenido como referencia? (es lo que evita interrumpir)\n")

    def una(etiqueta, inst, segundos, **kw):
        motor = Motor(inst, SR, **kw)
        salidas = motor.procesar(musica(segundos), musica(segundos))
        cruce = correlacion_cruzada_maxima(salidas)
        cal = medicion.calibrar(microfono(salidas), salidas, SR)
        if cal is None:
            print(f"   {etiqueta:<32} {segundos:>4.0f}s  corr={cruce:.2f}  NO ALINEA")
            return
        marca = "  ← FALLA EN SILENCIO" if cal.confiable and error_de(cal) > 1.0 else ""
        print(
            f"   {etiqueta:<32} {segundos:>4.0f}s  corr={cruce:.2f}  "
            f"estab={max(cal.estabilidad_ms.values()):>5.2f}  confiable={str(cal.confiable):<5}  "
            f"error={error_de(cal):>6.2f} ms{marca}"
        )

    for s in (4, 10, 20):
        una("contenido, todo puesto", instalacion(), s)
    una("contenido, sin decorrelador", instalacion(), 10, decorrelar=False)
    una("contenido, sin ambiente", instalacion(), 10, extraer_ambiente=False)
    duros = Instalacion(
        parlantes=[
            Parlante("izq", "s1", pan=-1.0),
            Parlante("der", "s2", pan=+1.0),
            Parlante("atras", "s3", pan=0.0, ambiente=1.0),
        ],
        retardo_traseros_ms=12.0,
    )
    una("paneo total (canales disjuntos)", duros, 10)
    refs = {n: np.random.default_rng(i).standard_normal(SR * 10) * 0.3 for i, n in enumerate(REALES)}
    cal = medicion.calibrar(microfono(refs), refs, SR)
    print(
        f"   {'control: ruido independiente':<32} {10:>4.0f}s  corr={correlacion_cruzada_maxima(refs):.2f}  "
        f"estab={max(cal.estabilidad_ms.values()):>5.2f}  confiable={str(cal.confiable):<5}  "
        f"error={error_de(cal):>6.2f} ms"
    )


# -- 3. ¿se repite el error entre segmentos? -------------------------------------------


def prueba_repetibilidad() -> None:
    reiniciar_azar()
    print("\n3. El error del contenido, ¿se repite entre segmentos? (decide si confirmar sirve)\n")
    for segundos, n_seg in ((4.0, 16), (10.0, 10)):
        motor = Motor(instalacion(), SR)
        filas = []
        for _ in range(n_seg):
            salidas = motor.procesar(musica(segundos), musica(segundos))
            cal = medicion.calibrar(microfono(salidas), salidas, SR)
            filas.append(None if cal is None else (cal.confiable, error_de(cal), dict(cal.retardos_ms)))
        print(f"   --- segmentos de {segundos:.0f} s ---")
        for i, fila in enumerate(filas):
            if fila is None:
                print(f"     {i}: sin resultado")
                continue
            conf, err, val = fila
            print(
                f"     {i}: confiable={str(conf):<5} error={err:>6.2f} ms   "
                + "  ".join(f"{n}={val[n]:+7.2f}" for n in REALES)
            )
        buenos = [f for f in filas if f is not None and f[0]]
        if len(buenos) >= 2:
            pares = [max(abs(a[2][n] - b[2][n]) for n in REALES) for a, b in zip(buenos, buenos[1:], strict=False)]
            print(
                f"     → pasan estabilidad: {len(buenos)}/{n_seg}; de esos con error >1 ms: "
                f"{sum(1 for _, e, _ in buenos if e > 1.0)}"
            )
            print(f"     → discrepancia entre confiables consecutivos: {min(pares):.2f} a {max(pares):.2f} ms")


if __name__ == "__main__":
    prueba_retardo_negativo()
    prueba_contenido()
    prueba_repetibilidad()
