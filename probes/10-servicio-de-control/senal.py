"""Genera la señal de prueba: estéreo, 16 bits, 48 kHz.

Dos tonos fijos y graves (220 Hz a la izquierda, 330 Hz a la derecha) más un ruido rosa
débil distinto en cada canal, que es lo que el extractor reconoce como ambiente.

**Por qué tonos y no música para buscar clics:** un tono grave casi no tiene energía por
encima de 3 kHz, así que cualquier discontinuidad —un salto de ganancia, de retardo, un
corte mal hecho— aparece como un pico de banda ancha que `clics.py` encuentra en la
grabación del micrófono. Con música, los transitorios propios taparían los clics. Para
juzgar el envolvimiento, en cambio, hace falta música: `fuente.sh` acepta un archivo.

Nivel: pico de 0,3 (-10 dBFS). Con el servicio a -20 dB sale a 0,03 por parlante.

Uso: python3 senal.py <salida.wav> [segundos]
"""

import sys
import wave

import numpy as np

SR = 48000


def generar(segundos: float, semilla: int = 0) -> np.ndarray:
    n = int(SR * segundos)
    t = np.arange(n) / SR
    rng = np.random.default_rng(semilla)

    def rosa() -> np.ndarray:
        espectro = np.fft.rfft(rng.standard_normal(n))
        f = np.fft.rfftfreq(n, 1 / SR)
        espectro[1:] /= np.sqrt(f[1:])
        espectro[0] = 0
        x = np.fft.irfft(espectro, n)
        return x / np.max(np.abs(x))

    izq = 0.22 * np.sin(2 * np.pi * 220 * t) + 0.06 * rosa()
    der = 0.22 * np.sin(2 * np.pi * 330 * t) + 0.06 * rosa()
    # Fundido de entrada y salida de 50 ms, para que la propia señal no empiece con un clic.
    rampa = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.05)
    return np.stack([izq * rampa, der * rampa], axis=1)


def main() -> None:
    destino = sys.argv[1]
    segundos = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
    x = generar(segundos)
    with wave.open(destino, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"  {destino}: {segundos:.0f} s, pico {np.max(np.abs(x)):.2f}")


if __name__ == "__main__":
    main()
