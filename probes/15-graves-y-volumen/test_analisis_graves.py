"""Tests del análisis de `probes/15-graves-y-volumen/`, con señales sintéticas de respuesta conocida.

    cd host && "$(hatch env find hatch-test)/bin/python" -m pytest ../probes/15-graves-y-volumen -q

El módulo se carga por ruta con un nombre propio: `probes/16-calidad/` también tiene un
`analisis.py`.
"""

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pytest

AQUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("analisis_graves", AQUI / "analisis.py")
A = importlib.util.module_from_spec(_spec)
sys.modules["analisis_graves"] = A
_spec.loader.exec_module(A)

SR = 48000
RNG = np.random.default_rng(11)


def rosa(n, semilla):
    f = np.fft.rfftfreq(n, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(np.random.default_rng(semilla).standard_normal(n)) / np.sqrt(np.maximum(f, 20)), n)
    return 0.1 * x / np.max(np.abs(x))


def filtrar(x, ganancia_por_frecuencia):
    """Un filtro de fase cero definido en frecuencia (con relleno: sin dar la vuelta)."""
    n = len(x) + 8192
    f = np.fft.rfftfreq(n, 1 / SR)
    return np.fft.irfft(np.fft.rfft(x, n) * ganancia_por_frecuencia(f), n)[: len(x)]


def sala(semilla):
    """Una "colocación del micrófono": un FIR corto distinto (reflexiones) por semilla."""
    rng = np.random.default_rng(semilla)
    taps = np.zeros(2400)
    taps[0] = 1.0
    taps[rng.integers(100, 2400, 6)] = rng.uniform(-0.4, 0.4, 6)
    return taps


def grabar_barrido(referencia, ganancias_db, taps, ruido_db=-75.0, pausa_s=1.0):
    """Una grabación con la referencia sonando una vez por paso, a cada ganancia, con latencia
    que varía entre 0,1 y 0,4 s. Devuelve (grabación, inicios aproximados, inicios reales)."""
    partes, reales, aproximados, t = [np.zeros(SR)], [], [], SR
    for g in ganancias_db:
        latencia = int(RNG.uniform(0.1, 0.4) * SR)
        partes.append(np.zeros(latencia))
        reales.append(t + latencia)
        aproximados.append(t + int(0.25 * SR))
        sonido = np.convolve(referencia * 10 ** (g / 20), taps)[: len(referencia)]
        partes.append(sonido)
        partes.append(np.zeros(int(pausa_s * SR)))
        t += latencia + len(sonido) + int(pausa_s * SR)
    y = np.concatenate(partes)
    return y + 10 ** (ruido_db / 20) * RNG.standard_normal(len(y)), aproximados, reales


# -- niveles --------------------------------------------------------------------------------


def test_un_seno_cae_en_su_tercio_con_su_nivel():
    x = 0.1 * np.sin(2 * np.pi * 125 * np.arange(SR * 2) / SR)
    niveles = A.niveles_por_tercio(x)
    k = int(np.argmin(np.abs(A.TERCIOS - 125)))
    assert niveles[k] == pytest.approx(-23.01, abs=0.05)
    assert niveles[k + 4] < niveles[k] - 50


def test_la_transferencia_ve_un_filtro_conocido_aunque_haya_ruido():
    x = rosa(SR * 4, 1)
    y = filtrar(x, lambda f: np.where(f < 500, 0.5, 1.0)) + 0.003 * RNG.standard_normal(len(x))
    h, coherencia = A.transferencia_por_tercio(x, y)
    k250 = int(np.argmin(np.abs(A.TERCIOS - 250)))
    k2k = int(np.argmin(np.abs(A.TERCIOS - 2000)))
    assert h[k250] == pytest.approx(-6.02, abs=0.2)
    assert h[k2k] == pytest.approx(0.0, abs=0.2)
    assert coherencia[k2k] > 0.9


# -- curva AVRCP ----------------------------------------------------------------------------


GO4 = {20: -27.0, 40: -16.5, 60: -9.0, 80: -4.1, 100: 0.0}
"""Una curva inventada, no la de PipeWire (80 % → −4,1 dB, como experimentos/10 §5.4)."""


def _medir_curva(taps, curva=GO4):
    ref = rosa(SR * 3, 2)
    porcentajes = list(curva)
    y, aproximados, reales = grabar_barrido(ref, [curva[p] for p in porcentajes], taps)
    pasos = A.medir_pasos(y, ref, aproximados)
    assert [p["inicio"] for p in pasos] == reales
    return A.curva_relativa({p: s["total_db"] for p, s in zip(porcentajes, pasos, strict=True)})


def test_la_curva_medida_es_la_del_parlante_en_dos_colocaciones():
    una = _medir_curva(sala(1))
    otra = _medir_curva(sala(2))
    for p, esperado in GO4.items():
        assert una[p] == pytest.approx(esperado, abs=0.1)
    assert A.monotona(una)["ok"]
    r = A.repetibilidad(una, otra)
    assert r["ok"] and r["max_db"] < 0.1


def test_una_curva_plana_arriba_no_es_monotona_y_una_corrida_distinta_no_es_repetible():
    plana = {**GO4, 100: -4.0}
    m = A.monotona(A.curva_relativa(plana))
    assert not m["ok"] and m["pasos_que_no_suben"][0]["de"] == 80
    otra = {**GO4, 60: -8.2}
    assert not A.repetibilidad(A.curva_relativa(GO4), A.curva_relativa(otra))["ok"]


def test_ida_y_vuelta_da_la_media_y_la_histeresis():
    pasos = [{"pct": 20, "db": -27.0}, {"pct": 40, "db": -16.0}, {"pct": 40, "db": -16.6}, {"pct": 20, "db": -27.2}]
    medias, histeresis = A.promediar_ida_y_vuelta(pasos)
    assert medias == {20: pytest.approx(-27.1), 40: pytest.approx(-16.3)}
    assert histeresis == pytest.approx(0.6)


def test_la_curva_de_pipewire():
    assert A.db_pipewire(80) == pytest.approx(-5.81, abs=0.01)
    assert A.db_pipewire(100) == 0.0


# -- protección de graves -------------------------------------------------------------------


def firmware_go4(x, volumen_pct):
    """Un Go 4 inventado: la protección baja la banda de 100–160 Hz según la energía bajo 120 Hz
    que le llega después del volumen, desde un umbral. Lo que se sabe del real es REPORTADO."""
    v = 10 ** (A.db_pipewire(volumen_pct) / 20)
    bajo = A.nivel_en_banda(x * v, 20, 120)
    recorte = max(0.0, 1.5 * (bajo - (-34.0)))
    return filtrar(x * v, lambda f: np.where((f >= 100) & (f < 160), 10 ** (-recorte / 20), 1.0))


def pasa_altos(x, corte=90):
    return filtrar(x, lambda f: (f / corte) ** 4 / (1 + (f / corte) ** 4))


def _barrido_proteccion(senal, volumenes):
    relativos = []
    for v in volumenes:
        y = firmware_go4(senal, v) + 1e-5 * RNG.standard_normal(len(senal))
        relativos.append(A.nivel_relativo_graves(A.niveles_por_tercio(y)))
    return A.inicio_de_caida(volumenes, relativos)


def test_el_pasa_altos_retrasa_una_proteccion_conocida():
    x = rosa(SR * 3, 3) * 4
    volumenes = [40, 50, 60, 70, 80, 90, 100]
    sin = _barrido_proteccion(x, volumenes)
    con = _barrido_proteccion(pasa_altos(x), volumenes)
    assert sin["inicio_pct"] is not None
    assert con["inicio_pct"] is None or con["inicio_pct"] > sin["inicio_pct"]
    assert A.comparar_proteccion(sin, con) in {"se_retrasa", "desaparece"}


def test_sin_proteccion_el_nivel_relativo_no_depende_del_volumen():
    x = rosa(SR * 3, 4) * 0.1  # tan bajo que el firmware inventado no actúa
    r = _barrido_proteccion(x, [40, 70, 100])
    assert r["inicio_pct"] is None and r["caida_maxima_db"] < 0.2


def test_comparar_proteccion_casos():
    caida = lambda p: {"inicio_pct": p}  # noqa: E731
    assert A.comparar_proteccion(caida(None), caida(None)) == "inconcluso"
    assert A.comparar_proteccion(caida(70), caida(None)) == "desaparece"
    assert A.comparar_proteccion(caida(70), caida(90)) == "se_retrasa"
    assert A.comparar_proteccion(caida(70), caida(70)) == "igual"
    assert A.comparar_proteccion(caida(70), caida(60)) == "se_adelanta"


# -- A/B ------------------------------------------------------------------------------------


def test_binomial_como_la_tabla_de_research_11():
    assert A.p_binomial(20, 30) == pytest.approx(0.0494, abs=1e-4)
    assert A.p_binomial(19, 30) > 0.05
    assert A.p_binomial(0, 30) == pytest.approx(1.0)
    assert [A.aciertos_minimos(n) for n in (16, 30, 40)] == [12, 20, 26]
    assert A.aciertos_minimos(4) is None  # ni 4 de 4 llega a p ≤ 0,05


def test_preferencia_prueba_de_signos():
    r = A.preferencia(["protect"] * 22 + ["off"] * 8, "protect")
    assert (r["veces"], r["de"]) == (22, 30)
    assert r["p_dos_colas"] == pytest.approx(2 * A.p_binomial(22, 30))
    assert A.preferencia(["off"] * 15 + ["protect"] * 15, "protect")["p_dos_colas"] == pytest.approx(1.0)
    assert math.isnan(A.preferencia([], "protect")["p_dos_colas"])
