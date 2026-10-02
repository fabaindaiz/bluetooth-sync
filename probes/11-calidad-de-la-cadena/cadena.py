"""What each stage of the engine does to the sound, measured offline.

Feeds the real motor (the live installation) with stereo pink noise and compares each
speaker's output against the ideal: the plain L/R mix its pan asks for, at its gain. Reports,
per third-octave, the magnitude of the transfer (dB) and the coherence (1 = a clean linear
filter; lower = the stage smears or adds something that is not the input).

    cd host && hatch run python ../probes/11-calidad-de-la-cadena/cadena.py [installation.json]
"""

import sys
from pathlib import Path

import numpy as np

from aurasync import motor as M
from aurasync.config import Instalacion
from aurasync.dsp.response import THIRDS

SR = 48000
ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".config/aurasync/instalacion.json"
inst = Instalacion.cargar(ruta)
for p in inst.parlantes:
    p.ecualizacion_db = None
    p.ambiente = 0.1  # what is playing now

rng = np.random.default_rng(0)
SEG = 16384


def spectra(x, y):
    """Welch auto- and cross-spectra (Hann, 50 % overlap), in numpy."""
    w = np.hanning(SEG)
    sxx = syy = sxy = 0
    for i in range(0, len(x) - SEG, SEG // 2):
        a = np.fft.rfft(x[i : i + SEG] * w)
        b = np.fft.rfft(y[i : i + SEG] * w)
        sxx = sxx + np.abs(a) ** 2
        syy = syy + np.abs(b) ** 2
        sxy = sxy + b * np.conj(a)
    return np.fft.rfftfreq(SEG, 1 / SR), sxx, syy, sxy


def align(ideal, y):
    """Shift the output back by the engine's latency (found by cross-correlation)."""
    k = 1 << int(np.ceil(np.log2(2 * len(y))))
    c = np.fft.irfft(np.fft.rfft(y, k) * np.conj(np.fft.rfft(ideal, k)), k)
    lag = int(np.argmax(np.abs(c[: SR // 2])))
    return ideal[: len(y) - lag], y[lag:], lag


def pink(n):
    f = np.fft.rfftfreq(n, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) / np.sqrt(np.maximum(f, 20)), n)
    return 0.1 * x / x.std()


n = SR * 20
common = pink(n)
L = 0.7 * common + 0.3 * pink(n)  # correlated stereo, like music
R = 0.7 * common + 0.3 * pink(n)


def measure(label, **kw):
    m = M.Motor(inst, SR, volumen_db=0.0, ecualizar=True, **{k: v for k, v in kw.items() if k in ("extraer_ambiente", "decorrelar")})
    out = M.procesar_completo(m, L, R, 4096)
    print(f"\n== {label}")
    print("speaker            " + " ".join(f"{int(c):>6}" for c in THIRDS[::3]))
    for p in inst.parlantes:
        ideal = ((1 - p.pan) / 2 * L + (1 + p.pan) / 2 * R) * 10 ** (p.ganancia_db / 20)
        ideal, y, lag = align(ideal, out[p.nombre])
        f, pxx, pyy, pxy = spectra(ideal, y)
        coh = np.abs(pxy) ** 2 / (pxx * pyy + 1e-30)
        h = 20 * np.log10(np.abs(pxy) / (pxx + 1e-30) + 1e-12)
        mag = [np.mean(h[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))]) for c in THIRDS]
        co = [np.mean(coh[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))]) for c in THIRDS]
        print(f"{p.nombre[:16]:16} dB " + " ".join(f"{v:6.1f}" for v in mag[::3]))
        print(f"{'':16} coh" + " ".join(f"{v:6.2f}" for v in co[::3]))


measure("everything on (as the service runs)", extraer_ambiente=True, decorrelar=True)
measure("decorrelator off", extraer_ambiente=True, decorrelar=False)
measure("extractor off", extraer_ambiente=False, decorrelar=True)
measure("both off (pan, delay, gain, flat EQ, limiter)", extraer_ambiente=False, decorrelar=False)
print("\ndelays (ms):", {p.nombre: p.retardo_ms for p in inst.parlantes})
