#!/usr/bin/env python3
"""Los bits del limitador de pico real en Rust, y su diferencia con numpy (experimentos/20, §9).

Dos cosas, con la extensión que esté instalada:

1. **Identidad:** corre `TruePeakLimiter` con `engine=rust` sobre el corpus de `test_limiter.py`
   más dos ruidos (0,1 y 0,02 de escala), cada señal seguida de 4096 ceros, con tres diseños
   (anticipación / liberación / retención: 3/250/15, 1/50/0 y 5/1000/15 ms) y bloques de 4096,
   1000, 7 y 333 muestras en ciclo; junta la salida y las tres métricas después de cada bloque
   (1 698 318 valores) e imprime su SHA-256. Dos formas del código Rust dan los mismos bits si y
   solo si dan el mismo hash. Con `--guardar ruta.npy` guarda la salida; con `--contra ruta.npy`
   la compara valor a valor con una guardada.
2. **Golden:** la diferencia máxima entre Rust y numpy en el corpus de `test_limiter.py` (con los
   valores por defecto, bloques de 4096), por separado en la salida y en las métricas; con el
   corpus armado por numpy (como en los tests) y por Rust (sus filtros FIR lo cambian en ~1e-16).

    cd host && hatch test tests/test_limiter_rust.py   # deja compilada la extensión
    $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/identidad_limitador.py
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

HOST = Path(__file__).resolve().parents[2] / "host"
sys.path.insert(0, str(HOST / "src"))
sys.path.insert(0, str(HOST))

import aurasync_engine  # noqa: E402

from aurasync.dsp import backend, limiter  # noqa: E402
from tests import test_limiter  # noqa: E402

SR = 48000
DESIGNS = ((3.0, 250.0, 15.0), (1.0, 50.0, 0.0), (5.0, 1000.0, 15.0))
SIZES = (4096, 1000, 7, 333)


def identity_output() -> np.ndarray:
    backend.reset()
    backend.use(backend.RUST)
    signals = [
        *test_limiter.corpus(),
        0.1 * np.random.default_rng(1).standard_normal(SR),
        0.02 * np.random.default_rng(2).standard_normal(SR),
    ]
    x = np.concatenate([np.concatenate([s, np.zeros(4096)]) for s in signals])
    outs = []
    for lookahead, release, hold in DESIGNS:
        lim = limiter.TruePeakLimiter(SR, lookahead_ms=lookahead, release_ms=release, hold_ms=hold)
        at, k = 0, 0
        while at < len(x):
            n = SIZES[k % len(SIZES)]
            outs.append(lim.process(x[at : at + n]))
            outs.append(np.array([lim.gain, lim.max_reduction_db, lim.active_fraction]))
            at, k = at + n, k + 1
        assert lim._rust is not None  # noqa: SLF001 - Rust really ran
    assert backend.failure() is None
    return np.concatenate(outs)


def golden(corpus_engine: str) -> tuple[float, float]:
    """Rust against numpy on the corpus, made with `corpus_engine` (its pink signals go through
    `eq.StreamingFIR`, so the two engines make inputs that differ around 1e-16)."""
    def run(engine: str, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        backend.reset()
        backend.use(engine)
        lim = limiter.TruePeakLimiter(SR, ceiling_db=test_limiter.CEILING_DB)
        out, metrics = [], []
        for i in range(0, len(x), 4096):
            out.append(lim.process(x[i : i + 4096]))
            metrics.append((lim.gain, lim.max_reduction_db, lim.active_fraction))
        assert (lim._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        return np.concatenate(out), np.array(metrics)

    backend.reset()
    backend.use(corpus_engine)
    corpus = test_limiter.corpus()
    worst_out = worst_metrics = 0.0
    for signal in corpus:
        x = np.concatenate([signal, np.zeros(4096)])
        (a, am), (b, bm) = run(backend.NUMPY, x), run(backend.RUST, x)
        worst_out = max(worst_out, float(np.max(np.abs(a - b))))
        worst_metrics = max(worst_metrics, float(np.max(np.abs(am - bm))))
    return worst_out, worst_metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guardar", type=Path)
    parser.add_argument("--contra", type=Path)
    args = parser.parse_args()
    rustc = subprocess.run(["rustc", "--version"], capture_output=True, text=True, check=False).stdout.strip()
    print(f"equipo {platform.node()}, {platform.platform()}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, rustc: {rustc}")
    print(f"extensión: {aurasync_engine.__file__}")
    y = identity_output()
    print(f"identidad: {y.size} valores, sha256 {hashlib.sha256(y.tobytes()).hexdigest()}")
    if args.guardar is not None:
        np.save(args.guardar, y)
        print(f"guardada en {args.guardar}")
    if args.contra is not None:
        old = np.load(args.contra)
        same = old.shape == y.shape and np.array_equal(old, y)
        print(f"contra {args.contra.name}: mismos bits {same}, diferencia máxima {np.max(np.abs(old - y)):.3g}")
    for corpus_engine in (backend.NUMPY, backend.RUST):
        out, metrics = golden(corpus_engine)
        print(
            f"golden (Rust contra numpy, corpus de test_limiter.py armado con {corpus_engine}): "
            f"salida {out:.3g}, métricas {metrics:.3g}"
        )
    backend.reset()


if __name__ == "__main__":
    main()
