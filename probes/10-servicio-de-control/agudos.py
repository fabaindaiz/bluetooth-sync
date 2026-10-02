"""¿El corte de agudos es del parlante o del códec bajo congestión?

Manda ruido rosa (amplitud 0,1) a un solo parlante y graba el micrófono, en dos condiciones:
1. los otros dos parlantes sin transmitir (enlace libre: el bitpool no tiene por qué bajar);
2. los otros dos transmitiendo silencio (tres enlaces A2DP activos, como en uso real).
Compara la respuesta en tercios de octava del mismo parlante en las dos. Requiere la sesión
del servicio detenida (los streams de la sesión serían tráfico). Uso: python3 agudos.py
"""

import subprocess, sys, time, wave
from pathlib import Path
import numpy as np

SR = 48000
MIC = "alsa_input.usb-3142_fifine_Microphone-00.analog-stereo"
SINKS = {n: f"bluez_output.{m}.1" for n, m in (("Red", "90_F2_60_75_4A_83"), ("Black", "90_F2_60_DA_66_6D"), ("Blue", "90_F2_60_E3_07_39"))}
TERCIOS = 1000 * 2 ** (np.arange(-13, 15) / 3)
PROPS = "{ node.dont-move = true node.dont-reconnect = true node.dont-fallback = true }"


def rosa(n, semilla):
    rng = np.random.default_rng(semilla); X = np.fft.rfft(rng.standard_normal(n)); f = np.fft.rfftfreq(n, 1 / SR)
    X[1:] /= np.sqrt(f[1:]); X[0] = 0; x = np.fft.irfft(X, n); return 0.1 * x / np.max(np.abs(x))


def play(sink, x):
    p = subprocess.Popen(["pw-play", "--target", sink, "--rate", str(SR), "--channels", "1", "--format", "s16", "-P", PROPS, "--raw", "-"], stdin=subprocess.PIPE)
    return p, (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def medir(objetivo, otros_activos, dur=8.0, carpeta=Path(".")):
    ruido = rosa(int(SR * dur), 5)
    wav = carpeta / f"agudos-{objetivo}-{'con' if otros_activos else 'sin'}-otros.wav"
    rec = subprocess.Popen(["pw-record", "--target", MIC, "--rate", str(SR), "--channels", "1", "--format", "s16", str(wav)])
    procs = []
    if otros_activos:
        for n, s in SINKS.items():
            if n != objetivo:
                procs.append(play(s, np.zeros(int(SR * (dur + 3)))))
    time.sleep(1.0)
    procs.append(play(SINKS[objetivo], np.concatenate([ruido, np.zeros(SR)])))
    for p, data in procs:
        p.stdin.write(data)
    for p, _ in procs:
        p.stdin.close()
    for p, _ in procs:
        p.wait(timeout=30)
    time.sleep(1.0); rec.send_signal(2); rec.wait(timeout=10)
    with wave.open(str(wav)) as w:
        mic = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32768
    # Alinear: el pico de la correlación cruzada.
    n = 1 << int(np.ceil(np.log2(len(mic) + len(ruido))))
    c = np.fft.irfft(np.fft.rfft(mic, n) * np.conj(np.fft.rfft(ruido, n)), n)
    lag = int(np.argmax(np.abs(c[: len(mic)])))
    m = mic[lag: lag + len(ruido)]; r = ruido[: len(m)]
    nseg = 16384; win = np.hanning(nseg); pxy = 0; pxx = 0
    for i in range(0, len(r) - nseg, nseg // 2):
        X = np.fft.rfft(r[i:i+nseg] * win); Y = np.fft.rfft(m[i:i+nseg] * win); pxy = pxy + Y * np.conj(X); pxx = pxx + np.abs(X) ** 2
    H = np.abs(pxy) / (pxx + 1e-20); f = np.fft.rfftfreq(nseg, 1 / SR)
    b = np.array([10 * np.log10(np.mean(H[(f >= c0 / 2**(1/6)) & (f < c0 * 2**(1/6))] ** 2) + 1e-20) for c0 in TERCIOS])
    return b - np.median(b[(TERCIOS > 400) & (TERCIOS < 2500)])


def main():
    carpeta = Path(__file__).resolve().parents[2] / "docs/research/experimentos/datos/10"
    objetivo = sys.argv[1] if len(sys.argv) > 1 else "Black"
    sin = medir(objetivo, False, carpeta=carpeta); time.sleep(1)
    con = medir(objetivo, True, carpeta=carpeta)
    print("Hz           " + "".join(f"{c:>6.0f}" for c in TERCIOS[::2]))
    print(f"{objetivo} solo     " + "".join(f"{v:6.1f}" for v in sin[::2]))
    print(f"{objetivo} + otros  " + "".join(f"{v:6.1f}" for v in con[::2]))
    for nombre, b in (("solo", sin), ("+ otros", con)):
        ok = b > -10
        print(f"  banda −10 dB {nombre}: {TERCIOS[ok].min():.0f}–{TERCIOS[ok].max():.0f} Hz")


if __name__ == "__main__":
    main()
