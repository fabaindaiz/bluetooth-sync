"""El análisis de las pruebas de calidad con micrófono: funciones puras, probadas con señales
sintéticas de respuesta conocida (`test_analisis_calidad.py`).

- `niveles_por_banda`: potencia por fracción de octava (tercios, sextos), en dB respecto de
  escala completa: un seno de amplitud A da 20·log10(A) − 3,01 dB en su banda.
- `ubicar`: dónde empieza una referencia en una grabación (GCC-PHAT, con fracción de muestra).
- `decidir_suma`: si el Go 4 suma L+R o elige un canal (`suma_go4.py`).
- `comparar_pasadas`: directo contra motor, con la repetición A-B-A (`directo_vs_motor.py`).
- `respuesta_multiple`: la respuesta de cada parlante y su coherencia cuando suenan **todos a la
  vez** con ruidos independientes (`respuesta.py`). La coherencia común (γ² de una referencia con
  el micrófono) no sirve ahí: con tres parlantes iguales no pasa de ~1/3, porque los otros dos
  cuentan como ruido. Se resuelve el sistema de varias entradas por frecuencia (H = Gxx⁻¹·Gxy) y
  la coherencia de cada parlante es la que tendría solo, con el ruido que de verdad quedó sin
  explicar (Bendat y Piersol, *Random Data*, cap. 7: entradas múltiples).
"""

from __future__ import annotations

import math

import numpy as np

SR = 48000
ESCALA_ESTABLE_DB = 0.5
"""Lo que puede cambiar un resultado al cambiar el tamaño de segmento (un parámetro que no
debería importar, CLAUDE.md)."""


# -- bandas ------------------------------------------------------------------------------------


def centros(fraccion: int, f_min: float, f_max: float) -> np.ndarray:
    """Centros de 1/`fraccion` de octava (base 1 kHz) entre f_min y f_max, inclusive."""
    k_min = math.ceil(fraccion * math.log2(f_min / 1000) - 1e-9)
    k_max = math.floor(fraccion * math.log2(f_max / 1000) + 1e-9)
    return 1000 * 2 ** (np.arange(k_min, k_max + 1) / fraccion)


TERCIOS = centros(3, 50, 20000)
"""27 tercios, 50 Hz a 20 kHz, como `aurasync.dsp.response.THIRDS`."""


def potencia_por_bin(x: np.ndarray, segmento: int, sr: int = SR) -> tuple[np.ndarray, np.ndarray]:
    """(frecuencias, potencia) de Welch (Hann, 50 % de solape), escalada para que la suma de
    todos los bins sea el valor cuadrático medio de `x`."""
    x = np.asarray(x, dtype=float)
    if len(x) < segmento:
        msg = f"hacen falta al menos {segmento} muestras; hay {len(x)}"
        raise ValueError(msg)
    w = np.hanning(segmento)
    acumulado = np.zeros(segmento // 2 + 1)
    cuantos = 0
    for i in range(0, len(x) - segmento + 1, segmento // 2):
        acumulado += np.abs(np.fft.rfft(x[i : i + segmento] * w)) ** 2
        cuantos += 1
    p = acumulado / cuantos / (segmento * np.sum(w**2))
    p[1:-1] *= 2  # un solo lado
    return np.fft.rfftfreq(segmento, 1 / sr), p


def _bordes(c: float, fraccion: int) -> tuple[float, float]:
    return c * 2 ** (-1 / (2 * fraccion)), c * 2 ** (1 / (2 * fraccion))


def sumar_por_banda(f: np.ndarray, valores: np.ndarray, cs: np.ndarray, fraccion: int) -> np.ndarray:
    """Suma de `valores` (potencias) por banda; NaN si la banda no tiene ningún bin."""
    out = np.full(len(cs), np.nan)
    for k, c in enumerate(cs):
        lo, hi = _bordes(c, fraccion)
        m = (f >= lo) & (f < hi)
        if m.any():
            out[k] = np.sum(valores[m])
    return out


def promediar_por_banda(f: np.ndarray, valores: np.ndarray, cs: np.ndarray, fraccion: int) -> np.ndarray:
    out = np.full(len(cs), np.nan)
    for k, c in enumerate(cs):
        lo, hi = _bordes(c, fraccion)
        m = (f >= lo) & (f < hi)
        if m.any():
            out[k] = np.mean(valores[m])
    return out


def db(p: np.ndarray | float) -> np.ndarray | float:
    return 10 * np.log10(np.maximum(p, 1e-30))


def niveles_por_banda(
    x: np.ndarray, cs: np.ndarray = TERCIOS, fraccion: int = 3, segmento: int = 16384, sr: int = SR
) -> np.ndarray:
    f, p = potencia_por_bin(x, segmento, sr)
    return db(sumar_por_banda(f, p, cs, fraccion))


def nivel_total(x: np.ndarray, f_min: float = 0.0, f_max: float = SR / 2, segmento: int = 16384, sr: int = SR) -> float:
    f, p = potencia_por_bin(x, segmento, sr)
    return float(db(np.sum(p[(f >= f_min) & (f <= f_max)])))


def nivel_tono(x: np.ndarray, frecuencia: float, segmento: int = 16384, sr: int = SR) -> float:
    """El nivel en el tercio de octava del tono (dB re escala completa)."""
    return float(niveles_por_banda(x, np.array([frecuencia]), 3, segmento, sr)[0])


# -- alinear ------------------------------------------------------------------------------------


def ubicar(
    grabacion: np.ndarray, referencia: np.ndarray, desde: int = 0, hasta: int | None = None
) -> tuple[float, float]:
    """Dónde empieza `referencia` dentro de `grabacion`, buscando el inicio entre `desde` y
    `hasta` (muestras). Devuelve (posición con fracción de muestra, nitidez del pico: el pico
    sobre la mediana del valor absoluto, > ~10 es un hallazgo claro).

    GCC-PHAT: blanquea el espectro, así un tono o la reverberación no ensanchan el pico."""
    hasta = len(grabacion) - len(referencia) if hasta is None else min(hasta, len(grabacion) - 1)
    desde = max(0, desde)
    tramo = grabacion[desde : hasta + len(referencia)]
    if len(tramo) < len(referencia):
        msg = "la grabación no alcanza a cubrir la referencia en esa ventana"
        raise ValueError(msg)
    n = 1 << int(math.ceil(math.log2(len(tramo) + len(referencia))))
    cruzado = np.fft.rfft(tramo, n) * np.conj(np.fft.rfft(referencia, n))
    c = np.fft.irfft(cruzado / (np.abs(cruzado) + 1e-12 * np.max(np.abs(cruzado))), n)
    validos = c[: hasta - desde + 1]
    k = int(np.argmax(validos))
    nitidez = float(validos[k] / (np.median(np.abs(validos)) + 1e-30))
    # La fracción: el pico de PHAT es una sinc, y una parábola por tres puntos lo sesga hasta
    # 0,2 muestras. Se interpola la correlación con sinc (es de banda limitada) en una grilla
    # fina alrededor del pico.
    vecinos = np.arange(max(0, k - 32), min(len(c), k + 33))
    finos = k + np.linspace(-1, 1, 401)
    valores = np.sinc(finos[:, None] - vecinos[None, :]) @ c[vecinos]
    return desde + float(finos[int(np.argmax(valores))]), nitidez


def recortar(grabacion: np.ndarray, inicio: float, largo: int, margen: int = 0) -> np.ndarray:
    """El tramo de `largo` muestras desde `inicio` (redondeado), sin `margen` en cada punta."""
    i = round(inicio) + margen
    tramo = grabacion[i : i + largo - 2 * margen]
    if len(tramo) < largo - 2 * margen:
        msg = "la grabación se terminó antes del tramo"
        raise ValueError(msg)
    return tramo


# -- suma_go4 -----------------------------------------------------------------------------------


def decidir_suma(niveles: dict[str, float], piso_db: float, umbral_db: float = -30.0) -> dict:
    """¿El parlante suma L+R, elige un canal, u otra cosa?

    `niveles`: dB del tono en el micrófono con `L` solo, `R` solo, `LR` (L = R) y `LmR`
    (L = −R). Si suma, `LmR` se cancela adentro del parlante: queda al menos 30 dB bajo `LR`
    (y `L` y `R` quedan ~6 dB bajo `LR`). Si elige un canal, `LmR` suena como `LR` y el otro
    canal no suena. Para poder ver −30 dB, el piso de ruido tiene que estar 5 dB más abajo.
    """
    lr, lmr, izq, der = niveles["LR"], niveles["LmR"], niveles["L"], niveles["R"]
    cancelacion = lmr - lr
    margen = lr - piso_db
    datos = {
        "cancelacion_db": round(cancelacion, 2),
        "L_menos_LR_db": round(izq - lr, 2),
        "R_menos_LR_db": round(der - lr, 2),
        "margen_sobre_piso_db": round(margen, 2),
        "umbral_db": umbral_db,
    }
    if cancelacion < umbral_db:
        return {"veredicto": "suma", "motivo": f"L=−R queda {cancelacion:.1f} dB bajo L=R", **datos}
    if margen < -umbral_db + 5:
        motivo = f"el piso de ruido está solo {margen:.1f} dB bajo L=R: no se puede ver {umbral_db:.0f} dB"
        return {"veredicto": "inconcluso", "motivo": motivo, **datos}
    if abs(cancelacion) <= 3:  # noqa: PLR2004
        if der - izq < -20:  # noqa: PLR2004
            return {"veredicto": "elige_L", "motivo": "L=−R suena como L=R y R solo casi no suena", **datos}
        if izq - der < -20:  # noqa: PLR2004
            return {"veredicto": "elige_R", "motivo": "L=−R suena como L=R y L solo casi no suena", **datos}
    return {
        "veredicto": "otro",
        "motivo": f"L=−R queda {cancelacion:.1f} dB respecto de L=R: ni suma ni elige",
        **datos,
    }


# -- directo contra motor -----------------------------------------------------------------------


def lufs(x: np.ndarray, sr: int = SR) -> float:
    """Sonoridad integrada (BS.1770, compuerta incluida) de una señal mono, G = 1."""
    from aurasync.dsp.loudness import integrated_lufs  # noqa: PLC0415

    return float(integrated_lufs(np.asarray(x, dtype=float), sr))


def comparar_pasadas(
    a1: np.ndarray,
    b: np.ndarray,
    a2: np.ndarray,
    segmentos: tuple[int, ...] = (8192, 32768),
    f_min: float = 100.0,
    f_max: float = 8000.0,
    piso: np.ndarray | None = None,
    sr: int = SR,
) -> dict:
    """Directo (a1, a2) contra motor (b), ya recortados al mismo pasaje.

    - `delta_lufs`: L(b) − media de L(a1), L(a2).
    - `delta_bandas`: por sexto de octava, b − media(a1, a2) en dB, para cada tamaño de segmento.
    - `repeticion_*`: a2 − a1, lo que varía la medición sola: si es grande, el Δ no se puede leer.
    - `estabilidad_db`: la mayor diferencia del Δ entre tamaños de segmento.
    - Si se da `piso` (sextos del ruido de la pieza), las bandas con menos de 10 dB de señal sobre
      el piso se excluyen y se listan.
    """
    cs = centros(6, f_min, f_max)
    la1, lb, la2 = lufs(a1, sr), lufs(b, sr), lufs(a2, sr)
    por_segmento, repeticion = {}, {}
    validas = np.ones(len(cs), dtype=bool)
    for seg in segmentos:
        n1 = niveles_por_banda(a1, cs, 6, seg, sr)
        nb = niveles_por_banda(b, cs, 6, seg, sr)
        n2 = niveles_por_banda(a2, cs, 6, seg, sr)
        por_segmento[seg] = nb - (n1 + n2) / 2
        repeticion[seg] = n2 - n1
        validas &= np.isfinite(por_segmento[seg])
        if piso is not None:
            validas &= np.minimum(np.minimum(n1, n2), nb) - piso >= 10  # noqa: PLR2004
    principal = por_segmento[segmentos[0]]
    estabilidad = max(
        (float(np.max(np.abs(por_segmento[s] - principal)[validas])) for s in segmentos[1:] if validas.any()),
        default=0.0,
    )
    peor = float(np.max(np.abs(principal[validas]))) if validas.any() else math.nan
    repeticion_bandas = float(np.max(np.abs(repeticion[segmentos[0]][validas]))) if validas.any() else math.nan
    return {
        "centros_hz": cs,
        "lufs": {"a1": la1, "b": lb, "a2": la2},
        "delta_lufs": lb - (la1 + la2) / 2,
        "repeticion_lufs": la2 - la1,
        "delta_bandas_db": {str(s): v for s, v in por_segmento.items()},
        "repeticion_bandas_db": {str(s): v for s, v in repeticion.items()},
        "bandas_validas": validas,
        "bandas_excluidas_hz": cs[~validas],
        "peor_delta_banda_db": peor,
        "peor_repeticion_banda_db": repeticion_bandas,
        "estabilidad_db": estabilidad,
    }


def criterio_directo_motor(r: dict, lu: float = 0.5, banda_db: float = 1.0) -> dict:
    """El criterio de la spec (§8): |ΔLUFS| ≤ 0,5 LU y ≤ 1 dB por sexto de 100 Hz a 8 kHz; vale
    solo si la medición se repite (a2 − a1 dentro de la mitad de cada tolerancia) y no depende
    del tamaño de segmento (≤ 0,5 dB)."""
    medible = (
        abs(r["repeticion_lufs"]) <= lu / 2
        and r["peor_repeticion_banda_db"] <= banda_db / 2
        and r["estabilidad_db"] <= ESCALA_ESTABLE_DB
    )
    cumple = abs(r["delta_lufs"]) <= lu and r["peor_delta_banda_db"] <= banda_db
    return {"medible": bool(medible), "cumple": bool(cumple and medible)}


# -- respuesta de varios parlantes a la vez ----------------------------------------------------


def desfase_y_deriva(
    grabacion: np.ndarray, referencia: np.ndarray, sr: int = SR, trozo_s: float = 1.0, busqueda_s: float | None = None
) -> dict:
    """Dónde está `referencia` en la grabación, trozo a trozo, y la recta que mejor ajusta.

    La deriva de reloj (~22 ppm, experimentos/10 §5.3) corre 0,2 ms en 10 s: a 8 kHz eso es
    más de una vuelta de fase, y sin corregirla la coherencia se derrumba en agudos. Se descartan
    los trozos que se alejan más de 0,5 ms de la recta (research/11 §1.4). Trozos de 1 s: con
    2 s, la propia deriva dentro del trozo ensancha el pico y sesga la pendiente (MEDIDO en el
    test: 4 % de error a 40 ppm con 2 s, 0,05 % con 1 s)."""
    trozo = int(trozo_s * sr)
    hasta = None if busqueda_s is None else int(busqueda_s * sr)
    inicio_global, _ = ubicar(grabacion, referencia[: min(len(referencia), 4 * trozo)], 0, hasta)
    posiciones, desfases = [], []
    for k in range(0, len(referencia) - trozo + 1, trozo):
        esperado = round(inicio_global) + k
        try:
            donde, nitidez = ubicar(grabacion, referencia[k : k + trozo], esperado - 240, esperado + 240)
        except ValueError:
            break
        if nitidez > 5:  # noqa: PLR2004
            # El pico es el desfase medio del trozo: corresponde a su centro, no a su inicio
            # (con 40 ppm, medio trozo de 2 s son casi 2 muestras de error en la ordenada).
            posiciones.append(k + trozo / 2)
            desfases.append(donde - k)
    if len(posiciones) < 2:  # noqa: PLR2004
        return {"desfase": inicio_global, "deriva": 0.0, "trozos": len(posiciones), "descartados": 0}
    p, d = np.array(posiciones, dtype=float), np.array(desfases)
    pendiente, ordenada = np.polyfit(p, d, 1)
    fuera = np.abs(d - (pendiente * p + ordenada)) > 0.5e-3 * sr
    if (~fuera).sum() >= 2:  # noqa: PLR2004
        pendiente, ordenada = np.polyfit(p[~fuera], d[~fuera], 1)
    return {"desfase": float(ordenada), "deriva": float(pendiente), "trozos": len(p), "descartados": int(fuera.sum())}


def respuesta_multiple(
    grabacion: np.ndarray,
    referencias: list[np.ndarray],
    desfases: list[float],
    derivas: list[float] | None = None,
    segmento: int = 16384,
    sr: int = SR,
) -> dict:
    """La respuesta de cada parlante en el micrófono cuando suenan todos a la vez.

    Modelo por frecuencia: Y = Σ Hᵢ·Xᵢ + N. Con los espectros cruzados promediados por Welch,
    conj(H) = Gxx⁻¹·Gxy (resuelve también la fuga entre referencias que el promedio finito no
    anula). El ruido que queda, Gnn = Gyy − Σᵢⱼ Hᵢ·Gxx[i,j]·conj(Hⱼ), es lo que ninguna
    referencia explica (sala lejana, ruido, no linealidad). La coherencia de cada parlante,
    γᵢ² = |Hᵢ|²·Gxxᵢᵢ / (|Hᵢ|²·Gxxᵢᵢ + Gnn), es la que tendría si sonara solo con ese ruido.

    `desfases[i]` es dónde empieza la referencia i en la grabación (con fracción) y `derivas[i]`
    cuánto se corre por muestra: cada segmento toma su trozo de referencia con el desfase de ese
    momento, y la fracción se aplica como fase.
    """
    n_ref = len(referencias)
    derivas = derivas or [0.0] * n_ref
    w = np.hanning(segmento)
    f = np.fft.rfftfreq(segmento, 1 / sr)
    gxx = np.zeros((len(f), n_ref, n_ref), dtype=complex)
    gxy = np.zeros((len(f), n_ref), dtype=complex)
    gyy = np.zeros(len(f))
    usados = 0
    largo = min(len(r) for r in referencias)
    for k in range(0, largo - segmento + 1, segmento // 2):
        espectros = []
        posicion_y = None
        for i, ref in enumerate(referencias):
            d = desfases[i] + derivas[i] * k
            entero = math.floor(d)
            if i == 0:
                posicion_y = entero + k
            # Todas las referencias se leen contra el mismo tramo de micrófono: el desplazamiento
            # de cada una respecto de la primera entra como corrimiento de su ventana más la fase.
            corrimiento = d - (posicion_y - k)
            inicio_ref = k - math.floor(corrimiento)
            fraccion = corrimiento - math.floor(corrimiento)
            if inicio_ref < 0 or inicio_ref + segmento > len(ref):
                espectros = None
                break
            x = np.fft.rfft(ref[inicio_ref : inicio_ref + segmento] * w)
            espectros.append(x * np.exp(-2j * np.pi * f * fraccion / sr))
        if espectros is None or posicion_y is None or posicion_y < 0 or posicion_y + segmento > len(grabacion):
            continue
        y = np.fft.rfft(grabacion[posicion_y : posicion_y + segmento] * w)
        x = np.stack(espectros, axis=1)  # (frecuencia, referencia)
        gxx += x[:, :, None] * np.conj(x[:, None, :])
        gxy += x * np.conj(y)[:, None]
        gyy += np.abs(y) ** 2
        usados += 1
    if usados < 2:  # noqa: PLR2004
        msg = "la grabación no alcanza para dos segmentos con todas las referencias"
        raise ValueError(msg)
    regularizacion = 1e-12 * np.max(np.abs(gxx)) * np.eye(n_ref)
    h = np.conj(np.linalg.solve(gxx + regularizacion, gxy[:, :, None])[:, :, 0])
    explicado = np.real(np.einsum("fi,fij,fj->f", h, gxx, np.conj(h)))
    gnn = np.maximum(gyy - explicado, 1e-30 * np.max(gyy))
    propia = np.abs(h) ** 2 * np.real(np.einsum("fii->fi", gxx))
    coherencia = propia / (propia + gnn[:, None])
    comun = np.abs(gxy) ** 2 / (np.real(np.einsum("fii->fi", gxx)) * gyy[:, None] + 1e-30)
    return {
        "f": f,
        "h": h.T,
        "coherencia": coherencia.T,
        "coherencia_comun": comun.T,
        "coherencia_multiple": np.clip(explicado / (gyy + 1e-30), 0, 1),
        "segmentos": usados,
    }


def respuesta_por_tercio(r: dict, cs: np.ndarray = TERCIOS) -> dict:
    """Por tercio: |H|² promediado (dB), la misma curva normalizada a la mediana de 400 Hz–2,5 kHz
    (como `aurasync.dsp.response`, para comparar colocaciones a distinta distancia) y γ² medio."""
    f = r["f"]
    out = []
    for h, g in zip(r["h"], r["coherencia"], strict=True):
        banda = db(promediar_por_banda(f, np.abs(h) ** 2, cs, 3))
        medio = (cs > 400) & (cs < 2500)  # noqa: PLR2004
        out.append(
            {
                "db": banda,
                "normalizada_db": banda - np.nanmedian(banda[medio]),
                "coherencia": promediar_por_banda(f, g, cs, 3),
            }
        )
    return {"centros_hz": cs, "parlantes": out}


def estabilidad_entre_segmentos(
    por_segmento: dict[int, dict], indice: int, f_min: float = 100.0, f_max: float = 8000.0, gamma2: float = 0.9
) -> dict:
    """La mayor diferencia de la curva normalizada del parlante `indice` entre tamaños de
    segmento, en los tercios de f_min a f_max con γ² ≥ `gamma2` en todos."""
    tamanos = sorted(por_segmento)
    cs = por_segmento[tamanos[0]]["centros_hz"]
    banda = (cs >= f_min) & (cs <= f_max)
    validas = banda.copy()
    for t in tamanos:
        validas &= np.nan_to_num(por_segmento[t]["parlantes"][indice]["coherencia"]) >= gamma2
    curvas = np.array([por_segmento[t]["parlantes"][indice]["normalizada_db"] for t in tamanos])
    if not validas.any():
        return {"max_db": math.nan, "tercios_validos": 0, "ok": False}
    rango = np.max(curvas[:, validas], axis=0) - np.min(curvas[:, validas], axis=0)
    maximo = float(np.max(rango))
    return {"max_db": maximo, "tercios_validos": int(validas.sum()), "ok": maximo <= ESCALA_ESTABLE_DB}


def comparar_colocaciones(
    curvas: list[np.ndarray],
    coherencias: list[np.ndarray],
    cs: np.ndarray = TERCIOS,
    f_min: float = 100.0,
    f_max: float = 8000.0,
    gamma2: float = 0.9,
    tolerancia_db: float = 1.5,
) -> dict:
    """Las curvas normalizadas de un parlante en varias colocaciones del micrófono: en los tercios
    de f_min a f_max con γ² ≥ `gamma2` en todas, la mayor distancia a la media tiene que ser
    ≤ `tolerancia_db` (±1,5 dB)."""
    c = np.array(curvas)
    validas = (cs >= f_min) & (cs <= f_max)
    for g in coherencias:
        validas &= np.nan_to_num(g) >= gamma2
    if not validas.any():
        return {"max_db": math.nan, "tercios_validos": 0, "ok": False, "peor_tercio_hz": None}
    distancia = np.abs(c[:, validas] - np.mean(c[:, validas], axis=0))
    peor = int(np.argmax(np.max(distancia, axis=0)))
    maximo = float(np.max(distancia))
    return {
        "max_db": maximo,
        "tercios_validos": int(validas.sum()),
        "tercios_excluidos_hz": [float(x) for x in cs[((cs >= f_min) & (cs <= f_max)) & ~validas]],
        "peor_tercio_hz": float(cs[validas][peor]),
        "ok": maximo <= tolerancia_db,
    }
