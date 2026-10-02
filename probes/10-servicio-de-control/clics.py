"""Busca clics en una grabación del micrófono y los cruza con los cambios pedidos al servicio.

Pensado para la señal de `senal.py` (tonos graves y ruido débil): por encima de 3 kHz casi
no hay energía, así que un clic aparece como un pico de alta frecuencia. Se marca la ventana
de 5 ms cuyo pico supera **en 10 dB** a la mediana de los 2 s que la rodean.

**Cuánto ve depende del ruido de la pieza**, y eso no se sabe de antemano. Por eso, en cada
grabación, el detector mide su propia sensibilidad: mete saltos sintéticos (6 dB de
ganancia, 2 ms de retardo) en tramos sin cambios y cuenta cuántos encuentra. Si no
encuentra ninguno, el resultado es NO CONCLUYENTE y lo dice.

El ruido de la pieza (una voz, un golpe) también da picos. Por eso cada pico se cruza con el
registro de la sesión: un pico a menos de 0,5 s de un cambio pedido es **sospechoso**; uno
lejos de todo cambio es ruido de la sala.

**Se vio fallar antes de usarse:** `--autoprueba` mete un salto de ganancia de 6 dB, un
salto de retardo de 2 ms y un corte de 80 + 80 ms como el del servicio en una señal
sintética con ruido de fondo. Tiene que encontrar los dos saltos y no el corte.

Uso:
  python3 clics.py <grabacion.wav> <registro.jsonl> <inicio_epoch>
  python3 clics.py --autoprueba
"""

import json
import sys
import wave

import numpy as np

SR = 48000
VENTANA = int(0.005 * SR)
UMBRAL_DB = 10.0
CERCA_S = 0.5
BORDE = 20
"""Ventanas que se ignoran en cada extremo: el filtro por FFT da la vuelta en los bordes."""


def pico_alto(x: np.ndarray, corte_hz: float = 3000.0) -> np.ndarray:
    """El pico de la señal por encima de `corte_hz` en cada ventana de 5 ms, en dB.

    El pico y no la energía: un salto de una muestra concentra su alta frecuencia en pocas
    muestras, y promediarlo en la ventana lo diluía hasta perderlo (se vio en la autoprueba).
    """
    espectro = np.fft.rfft(x)
    espectro[np.fft.rfftfreq(len(x), 1 / SR) < corte_hz] = 0
    alta = np.abs(np.fft.irfft(espectro, len(x)))
    n = len(alta) // VENTANA
    return 20 * np.log10(alta[: n * VENTANA].reshape(n, VENTANA).max(axis=1) + 1e-12)


def picos(x: np.ndarray) -> list[tuple[float, float]]:
    """(segundo, dB sobre la mediana de los 2 s alrededor) de cada ventana que supera el umbral."""
    db = pico_alto(x)
    radio = int(1.0 / (VENTANA / SR))
    encontrados = []
    i = BORDE
    while i < len(db) - BORDE:
        lo, hi = max(0, i - radio), min(len(db), i + radio)
        exceso = db[i] - np.median(db[lo:hi])
        if exceso >= UMBRAL_DB:
            encontrados.append((i * VENTANA / SR, float(exceso)))
            i += int(0.05 * SR / VENTANA)  # un clic es un evento, no diez ventanas seguidas
        else:
            i += 1
    return encontrados


def con_salto(x: np.ndarray, en: int, tipo: str) -> np.ndarray:
    """La misma grabación con un defecto sintético: ganancia de 6 dB o retardo de 2 ms."""
    y = x.copy()
    if tipo == "ganancia":
        y[en:] *= 0.5
    else:
        d = int(0.002 * SR)
        y[en:] = x[en - d : len(x) - d]
    return y


def sensibilidad(x: np.ndarray, prohibidos: list[float], intentos: int = 6) -> tuple[int, int]:
    """Cuántos saltos sintéticos encuentra el detector **en esta grabación**.

    Se insertan lejos de los cambios pedidos y de los picos que ya hay. Si no encuentra
    ninguno, la grabación tiene demasiado ruido para decir algo sobre clics.
    """
    rng = np.random.default_rng(0)
    ocupados = prohibidos + [s for s, _ in picos(x)]
    hallados = total = 0
    for _ in range(intentos * 20):
        if total >= intentos:
            break
        s = float(rng.uniform(1.0, len(x) / SR - 1.0))
        if any(abs(s - o) < 1.5 for o in ocupados):
            continue
        tipo = "ganancia" if total % 2 == 0 else "retardo"
        en = int(s * SR)
        tramo = slice(max(0, en - 3 * SR), min(len(x), en + 3 * SR))
        y = con_salto(x[tramo], en - tramo.start, tipo)
        total += 1
        hallados += any(abs(p - (en - tramo.start) / SR) < 0.02 for p, _ in picos(y))
    return hallados, total


def leer(ruta: str) -> np.ndarray:
    with wave.open(ruta) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    return x


def autoprueba() -> int:
    rng = np.random.default_rng(1)
    t = np.arange(SR * 12) / SR
    tono = 0.03 * np.sin(2 * np.pi * 220 * t + 0.7)
    ganancia = np.ones_like(t)
    ganancia[int(3.0013 * SR) :] = 0.5  # salto de 6 dB, fuera de un cruce por cero
    retardo = np.zeros_like(t, dtype=int)
    retardo[int(6 * SR) :] = int(0.002 * SR)  # salto de 2 ms a los 6 s
    indices = np.clip(np.arange(len(t)) - retardo, 0, None)
    x = (tono * ganancia)[indices]
    # El corte del servicio a los 9 s: coseno alzado de 80 ms hasta cero y de vuelta.
    largo = int(0.08 * SR)
    curva = 0.5 * (1 + np.cos(np.pi * np.arange(largo) / largo))
    inicio = int(9 * SR)
    x[inicio : inicio + largo] *= curva
    x[inicio + largo : inicio + 2 * largo] *= curva[::-1]
    x += 0.0003 * rng.standard_normal(len(x))  # el ruido de fondo, 40 dB bajo el tono
    tiempos = [round(s, 2) for s, _ in picos(x)]
    print(f"  picos encontrados: {tiempos}")
    salto_g = any(abs(s - 3.0) < 0.05 for s in tiempos)
    salto_r = any(abs(s - 6.0) < 0.05 for s in tiempos)
    corte = any(8.9 < s < 9.3 for s in tiempos)
    print(f"  salto de ganancia: {'✓' if salto_g else '✗'} · salto de retardo: {'✓' if salto_r else '✗'} · "
          f"corte sin falsa alarma: {'✓' if not corte else '✗'}")
    return 0 if (salto_g and salto_r and not corte and len(tiempos) == 2) else 1


def main() -> int:
    if sys.argv[1:] == ["--autoprueba"]:
        return autoprueba()
    grabacion, registro, inicio = sys.argv[1], sys.argv[2], float(sys.argv[3])
    x = leer(grabacion)
    cambios = []
    with open(registro) as f:
        for linea in f:
            e = json.loads(linea)
            if e.get("clase") == "api" and e.get("metodo") != "GET":
                cambios.append((e["t"] - inicio, f"{e['metodo']} {e['ruta']} {json.dumps(e.get('cuerpo'))}"))
    print(f"  {len(x) / SR:.0f} s grabados, {len(cambios)} cambios pedidos")
    sospechosos = 0
    for s, exceso in picos(x):
        cercano = min(cambios, key=lambda c: abs(c[0] - s), default=None)
        if cercano and abs(cercano[0] - s) <= CERCA_S:
            sospechosos += 1
            print(f"  ✗ {s:7.2f} s  +{exceso:4.1f} dB  a {s - cercano[0]:+.2f} s de: {cercano[1]}")
        else:
            print(f"  · {s:7.2f} s  +{exceso:4.1f} dB  lejos de todo cambio (ruido de la sala)")
    hallados, total = sensibilidad(x, [c[0] for c in cambios])
    print(f"  {sospechosos} pico(s) junto a un cambio")
    print(f"  sensibilidad en esta grabación: {hallados} de {total} saltos sintéticos encontrados")
    if hallados == 0:
        print("  ✗ NO CONCLUYENTE: con este ruido, el detector no vería ni un salto de 6 dB")
        return 2
    return 1 if sospechosos else 0


if __name__ == "__main__":
    sys.exit(main())
