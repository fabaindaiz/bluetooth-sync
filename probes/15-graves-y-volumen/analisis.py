"""El análisis de las pruebas de graves y volumen: funciones puras, probadas con señales
sintéticas de respuesta conocida (`test_analisis_graves.py`).

- `niveles_por_tercio`, `transferencia_por_tercio`: el nivel en el micrófono por tercio de
  octava (dB re escala completa) y |H| de la referencia al micrófono (el cruzado rechaza el ruido
  de la pieza, que el nivel solo no rechaza).
- `curva_relativa`, `monotona`, `repetibilidad`: la curva volumen AVRCP → dB (`curva_avrcp.py`).
- `nivel_relativo_graves`, `inicio_de_caida`, `comparar_proteccion`: cuándo el Go 4 empieza a
  bajar los graves al subir el volumen, con y sin el pasa-altos (`proteccion.py`).
- `p_binomial`, `aciertos_minimos`, `preferencia`: el A/B ciego (`ab_graves.py`).
"""

from __future__ import annotations

import math

import numpy as np

SR = 48000
TERCIOS = 1000 * 2 ** (np.arange(-13, 14) / 3)
"""27 tercios, 50 Hz a 20 kHz (como `aurasync.dsp.response.THIRDS`)."""
REPETIBLE_DB = 0.5


def _db(p: np.ndarray | float) -> np.ndarray | float:
    return 10 * np.log10(np.maximum(p, 1e-30))


def _bandas(f: np.ndarray, valores: np.ndarray, cs: np.ndarray, sumar: bool) -> np.ndarray:  # noqa: FBT001
    out = np.full(len(cs), np.nan)
    for k, c in enumerate(cs):
        m = (f >= c * 2 ** (-1 / 6)) & (f < c * 2 ** (1 / 6))
        if m.any():
            out[k] = np.sum(valores[m]) if sumar else np.mean(valores[m])
    return out


def _welch(
    x: np.ndarray, y: np.ndarray | None, segmento: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    w = np.hanning(segmento)
    pxx = np.zeros(segmento // 2 + 1)
    pyy = np.zeros_like(pxx) if y is not None else None
    pxy = np.zeros_like(pxx, dtype=complex) if y is not None else None
    n = 0
    largo = len(x) if y is None else min(len(x), len(y))
    if largo < segmento:
        msg = f"hacen falta al menos {segmento} muestras; hay {largo}"
        raise ValueError(msg)
    for i in range(0, largo - segmento + 1, segmento // 2):
        a = np.fft.rfft(x[i : i + segmento] * w)
        pxx += np.abs(a) ** 2
        if y is not None:
            b = np.fft.rfft(y[i : i + segmento] * w)
            pyy += np.abs(b) ** 2
            pxy += b * np.conj(a)
        n += 1
    escala = n * segmento * np.sum(w**2)
    unilateral = np.full(len(pxx), 2.0)
    unilateral[[0, -1]] = 1.0
    f = np.fft.rfftfreq(segmento, 1 / SR)
    return (
        f,
        pxx * unilateral / escala,
        None if pyy is None else pyy * unilateral / escala,
        None if pxy is None else pxy * unilateral / escala,
    )


def niveles_por_tercio(x: np.ndarray, segmento: int = 16384, cs: np.ndarray = TERCIOS) -> np.ndarray:
    """Potencia por tercio, escalada para que un seno de amplitud A dé 20·log10(A) − 3,01 dB."""
    f, p, _, _ = _welch(np.asarray(x, dtype=float), None, segmento)
    return _db(_bandas(f, p, cs, sumar=True))


def nivel_en_banda(x: np.ndarray, f_min: float, f_max: float, segmento: int = 16384) -> float:
    f, p, _, _ = _welch(np.asarray(x, dtype=float), None, segmento)
    return float(_db(np.sum(p[(f >= f_min) & (f < f_max)])))


def transferencia_por_tercio(
    referencia: np.ndarray, grabacion: np.ndarray, segmento: int = 16384, cs: np.ndarray = TERCIOS
) -> tuple[np.ndarray, np.ndarray]:
    """(|H| en dB, coherencia γ²) por tercio, con la referencia ya alineada con la grabación."""
    f, pxx, pyy, pxy = _welch(np.asarray(referencia, dtype=float), np.asarray(grabacion, dtype=float), segmento)
    assert pyy is not None
    assert pxy is not None
    h2 = np.abs(pxy) ** 2 / (pxx**2 + 1e-30)
    coherencia = np.abs(pxy) ** 2 / (pxx * pyy + 1e-30)
    return _db(_bandas(f, h2 * pxx, cs, sumar=True)) - _db(_bandas(f, pxx, cs, sumar=True)), _bandas(
        f, coherencia, cs, sumar=False
    )


def ubicar(grabacion: np.ndarray, referencia: np.ndarray, desde: int, hasta: int) -> tuple[int, float]:
    """Dónde empieza `referencia` en `grabacion`, buscando entre `desde` y `hasta` (muestras):
    (posición, nitidez = pico / mediana; > ~10 es un hallazgo claro). GCC-PHAT, sin fracción
    (alcanza para recortar un tramo y medir su nivel)."""
    desde = max(0, desde)
    hasta = min(hasta, len(grabacion) - len(referencia))
    if hasta < desde:
        msg = "la grabación no cubre la ventana de búsqueda"
        raise ValueError(msg)
    tramo = grabacion[desde : hasta + len(referencia)]
    n = 1 << int(math.ceil(math.log2(len(tramo) + len(referencia))))
    cruzado = np.fft.rfft(tramo, n) * np.conj(np.fft.rfft(referencia, n))
    c = np.fft.irfft(cruzado / (np.abs(cruzado) + 1e-12 * np.max(np.abs(cruzado))), n)[: hasta - desde + 1]
    k = int(np.argmax(c))
    return desde + k, float(c[k] / (np.median(np.abs(c)) + 1e-30))


# -- curva AVRCP --------------------------------------------------------------------------------


def db_pipewire(porcentaje: float) -> float:
    """La curva cúbica de PipeWire: lo que el volumen "debería" dar (80 % = −5,81 dB)."""
    return 60 * math.log10(porcentaje / 100)


def curva_relativa(niveles: dict[float, float], referencia: float = 100.0) -> dict[float, float]:
    """Cada nivel menos el del porcentaje de referencia."""
    base = niveles[referencia]
    return {p: v - base for p, v in sorted(niveles.items())}


def monotona(curva: dict[float, float], paso_minimo_db: float = REPETIBLE_DB) -> dict:
    """Sube en cada paso de porcentaje al menos `paso_minimo_db` (más que lo que se repite la
    medición: si no, un "sube" no se distingue de "queda igual")."""
    puntos = sorted(curva.items())
    malos = [
        {"de": a, "a": b, "sube_db": round(vb - va, 2)}
        for (a, va), (b, vb) in zip(puntos, puntos[1:], strict=False)
        if vb - va < paso_minimo_db
    ]
    return {"ok": not malos, "pasos_que_no_suben": malos}


def promediar_ida_y_vuelta(pasos: list[dict]) -> tuple[dict[float, float], float]:
    """`pasos` = [{pct, db}] con porcentajes repetidos (ida y vuelta): la media por porcentaje
    (en dB) y la mayor diferencia entre repeticiones del mismo porcentaje (histéresis)."""
    por: dict[float, list[float]] = {}
    for p in pasos:
        por.setdefault(p["pct"], []).append(p["db"])
    histeresis = max((max(v) - min(v) for v in por.values()), default=0.0)
    return {k: float(np.mean(v)) for k, v in sorted(por.items())}, float(histeresis)


def repetibilidad(a: dict[float, float], b: dict[float, float]) -> dict:
    comunes = sorted(set(a) & set(b))
    diferencias = {p: b[p] - a[p] for p in comunes}
    peor = max((abs(d) for d in diferencias.values()), default=math.nan)
    return {"diferencias_db": diferencias, "max_db": peor, "ok": bool(comunes) and peor <= REPETIBLE_DB}


# -- protección de graves -----------------------------------------------------------------------


def nivel_relativo_graves(
    niveles: np.ndarray, cs: np.ndarray = TERCIOS, banda: tuple[float, float] = (112, 140)
) -> float:
    """El nivel de los tercios de `banda` (por defecto el de 125 Hz) menos el de 500 Hz–2 kHz.

    Relativo, para que el volumen general no cuente: sin protección es constante al subir el
    volumen, y la protección del firmware lo hace bajar."""
    potencia = 10 ** (np.asarray(niveles) / 10)
    graves = np.nansum(potencia[(cs >= banda[0]) & (cs < banda[1])])
    medios = np.nansum(potencia[(cs >= 450) & (cs < 2240)])  # noqa: PLR2004 - tercios de 500 a 2000
    return float(_db(graves) - _db(medios))


def inicio_de_caida(volumenes: list[float], relativos: list[float], umbral_db: float = 3.0) -> dict:
    """El primer volumen en que el nivel relativo cayó `umbral_db` o más respecto del volumen
    más bajo medido (o None si no cae)."""
    orden = np.argsort(volumenes)
    v = np.asarray(volumenes, dtype=float)[orden]
    r = np.asarray(relativos, dtype=float)[orden]
    caida = r[0] - r
    donde = np.nonzero(caida >= umbral_db)[0]
    return {
        "inicio_pct": float(v[donde[0]]) if len(donde) else None,
        "caida_maxima_db": float(np.max(caida)),
        "caida_db": {float(a): float(b) for a, b in zip(v, caida, strict=True)},
        "umbral_db": umbral_db,
    }


def comparar_proteccion(sin: dict, con: dict) -> str:
    """`sin` y `con` son `inicio_de_caida` con `bass=off` y `bass=protect`.

    - `inconcluso`: sin protección propia no hubo caída (el nivel no alcanzó a disparar el
      firmware): no hay nada que retrasar.
    - `desaparece` / `se_retrasa` / `igual` / `se_adelanta`."""
    a, b = sin["inicio_pct"], con["inicio_pct"]
    if a is None:
        return "inconcluso"
    if b is None:
        return "desaparece"
    if b > a:
        return "se_retrasa"
    return "igual" if b == a else "se_adelanta"


# -- A/B ----------------------------------------------------------------------------------------


def p_binomial(aciertos: int, ensayos: int, p0: float = 0.5) -> float:
    """P(X ≥ aciertos) con X ~ Binomial(ensayos, p0): la probabilidad de acertar al menos eso
    adivinando (una cola, exacta)."""
    return float(sum(math.comb(ensayos, k) * p0**k * (1 - p0) ** (ensayos - k) for k in range(aciertos, ensayos + 1)))


def aciertos_minimos(ensayos: int, alfa: float = 0.05) -> int | None:
    """Los aciertos que hacen falta para p ≤ alfa (research/11 §1.3: 12 de 16, 20 de 30, 26 de
    40); None si ni acertando todos se llega (menos de 5 ensayos)."""
    return next((k for k in range(ensayos + 1) if p_binomial(k, ensayos) <= alfa), None)


def preferencia(elecciones: list[str], opcion: str) -> dict:
    """Comparación pareada: cuántas veces se prefirió `opcion`, y p de dos colas (prueba de
    signos) contra "da lo mismo"."""
    n = len(elecciones)
    k = sum(1 for e in elecciones if e == opcion)
    extremo = max(k, n - k)
    p = min(1.0, 2 * p_binomial(extremo, n)) if n else math.nan
    return {"opcion": opcion, "veces": k, "de": n, "p_dos_colas": p}


# -- una grabación con varios pasos ------------------------------------------------------------


def medir_pasos(
    grabacion: np.ndarray,
    referencia: np.ndarray,
    inicios: list[int],
    antes_s: float = 0.3,
    despues_s: float = 2.5,
    margen_s: float = 0.25,
    segmento: int = 16384,
) -> list[dict]:
    """Cada paso reproduce la misma `referencia` empezando cerca de `inicios[i]` (muestras de la
    grabación, aproximado: la hora a la que se lanzó `pw-play` más lo que tarda en sonar). Se la
    ubica de verdad entre `antes_s` antes y `despues_s` después, se recorta sin `margen_s` en las
    puntas, y se miden el nivel por tercio, el total de 100 Hz a 10 kHz y |H| por tercio."""
    out = []
    for inicio in inicios:
        donde, nitidez = ubicar(grabacion, referencia, inicio - int(antes_s * SR), inicio + int(despues_s * SR))
        m = int(margen_s * SR)
        tramo = grabacion[donde + m : donde + len(referencia) - m]
        ref = referencia[m : len(referencia) - m]
        h, coherencia = transferencia_por_tercio(ref, tramo, segmento)
        out.append(
            {
                "inicio": int(donde),
                "retraso_s": (donde - inicio) / SR,
                "nitidez": nitidez,
                "tercios_db": niveles_por_tercio(tramo, segmento),
                "total_db": nivel_en_banda(tramo, 100, 10000, segmento),
                "h_db": h,
                "coherencia": coherencia,
            }
        )
    return out
