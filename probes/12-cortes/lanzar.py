"""Thread or process for the loop measurement: how long `lanzar` takes and how much it stalls
the calling thread (the engine thread, in the service). Result in experimentos/10 §9.

    cd host && hatch run python ../probes/12-cortes/lanzar.py
"""

import time
import numpy as np
from aurasync import medicion, sincronia

def main():
    sr = 48000
    rng = np.random.default_rng(0)
    refs = {n: rng.standard_normal(sr * 10) for n in ("a", "b", "c")}
    mic = rng.standard_normal(sr * 11)
    for proc in (False, True):
        m = sincronia.MedicionEnSegundoPlano(medicion.calibrar, en_proceso=proc)
        for k in range(3):
            t = time.perf_counter(); m.lanzar(mic, refs, sr); launch = (time.perf_counter() - t) * 1000
            # how much the calling thread is slowed while it runs: a busy loop of 1 ms steps
            worst = 0.0; end = time.perf_counter() + 2.0; last = time.perf_counter()
            while time.perf_counter() < end:
                time.sleep(0.001); now = time.perf_counter(); worst = max(worst, (now - last) * 1000); last = now
            while m.ocupado: time.sleep(0.01)
            m.recoger()
            print(f"{'proceso' if proc else 'hilo   '} lanzar {launch:6.1f} ms · peor pausa del hilo llamador {worst:6.1f} ms")
        m.cerrar()

if __name__ == "__main__":
    main()
