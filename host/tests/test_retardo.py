"""Comprueba la línea de retardo variable.

Dos tests llevan el peso: que el retardo fraccionario sea exacto —si no, se pierde la
precisión que la calibración consiguió— y que **cambiar el retardo no produzca un clic**,
que es la razón de ser de todo el módulo.
"""

import numpy as np
import pytest

from aurasync.dsp.retardo import LineaDeRetardo

SR = 48000


def _pulso(n: int = 4096, en: int = 100) -> np.ndarray:
    x = np.zeros(n)
    x[en] = 1.0
    return x


def _seno(n: int, hz: float = 440.0) -> np.ndarray:
    return np.sin(2 * np.pi * hz * np.arange(n) / SR)


def test_un_retardo_entero_corre_el_pulso_exactamente():
    linea = LineaDeRetardo(SR, retardo_ms=10.0)
    salida = linea.procesar(_pulso())
    assert int(np.argmax(salida)) == 100 + int(SR * 0.010)


def test_sin_retardo_la_senal_pasa_igual():
    linea = LineaDeRetardo(SR, retardo_ms=0.0)
    x = _seno(2048)
    assert np.allclose(linea.procesar(x), x, atol=1e-12)


def test_el_retardo_fraccionario_es_exacto():
    """Media muestra a 48 kHz son 0,0104 ms, y la calibración mide mejor que eso.

    Se comprueba con un seno: retardar media muestra tiene que correr la fase lo que
    corresponde, no redondear a cero ni a una muestra.
    """
    hz = 1000.0
    media_muestra_ms = 0.5 / SR * 1000
    linea = LineaDeRetardo(SR, retardo_ms=media_muestra_ms)
    n = 4096
    salida = linea.procesar(_seno(n, hz))

    esperado = np.sin(2 * np.pi * hz * (np.arange(n) - 0.5) / SR)
    centro = slice(100, n - 100)
    error = np.abs(salida[centro] - esperado[centro]).max()
    assert error < 0.01, f"error de {error:.4f} en un retardo de media muestra"


def test_el_retardo_se_mueve_hacia_el_objetivo_pero_no_de_golpe():
    linea = LineaDeRetardo(SR, retardo_ms=0.0, velocidad_ms_s=1.0)
    linea.objetivo_ms = 10.0
    # Un bloque de un segundo: a 1 ms/s tiene que haber avanzado 1 ms, no 10.
    linea.procesar(np.zeros(SR))
    assert linea.actual_ms == pytest.approx(1.0, abs=0.01)
    assert not linea.en_objetivo


def test_respeta_la_velocidad_maxima():
    """Es lo que garantiza que el cambio de tono quede por debajo de lo audible."""
    velocidad = 0.5
    linea = LineaDeRetardo(SR, retardo_ms=0.0, velocidad_ms_s=velocidad)
    linea.objetivo_ms = 100.0
    for _ in range(4):
        antes = linea.actual_ms
        linea.procesar(np.zeros(SR // 2))  # medio segundo
        avance = linea.actual_ms - antes
        assert avance <= velocidad * 0.5 + 1e-6


def test_llega_al_objetivo_y_se_queda():
    linea = LineaDeRetardo(SR, retardo_ms=0.0, velocidad_ms_s=10.0)
    linea.objetivo_ms = 2.0
    linea.procesar(np.zeros(SR))
    assert linea.en_objetivo
    assert linea.actual_ms == pytest.approx(2.0)
    linea.procesar(np.zeros(SR))
    assert linea.actual_ms == pytest.approx(2.0)


def test_cambiar_el_retardo_no_produce_un_clic():
    """El test que justifica el módulo entero.

    Se compara con el salto instantáneo, que es lo que haría una línea de retardo simple.
    La medida es el mayor salto entre muestras consecutivas: con una señal continua, un
    corte en la forma de onda aparece como una discontinuidad grande.
    """
    hz = 220.0
    bloque = 2048
    salto_por_muestra_de_la_senal = np.abs(np.diff(_seno(bloque, hz))).max()

    # Con rampa: el retardo se mueve despacio mientras la señal sigue.
    suave = LineaDeRetardo(SR, retardo_ms=5.0, velocidad_ms_s=0.5)
    suave.objetivo_ms = 8.0
    fase = 0
    salidas = []
    for _ in range(8):
        x = np.sin(2 * np.pi * hz * (fase + np.arange(bloque)) / SR)
        salidas.append(suave.procesar(x))
        fase += bloque
    con_rampa = np.concatenate(salidas)

    # De golpe: el mismo cambio, aplicado sin rampa en medio del flujo.
    brusco = LineaDeRetardo(SR, retardo_ms=5.0, velocidad_ms_s=0.5)
    fase = 0
    salidas = []
    for i in range(8):
        if i == 4:
            brusco.saltar_a(8.0)
        x = np.sin(2 * np.pi * hz * (fase + np.arange(bloque)) / SR)
        salidas.append(brusco.procesar(x))
        fase += bloque
    de_golpe = np.concatenate(salidas)

    salto_rampa = np.abs(np.diff(con_rampa)).max()
    salto_brusco = np.abs(np.diff(de_golpe)).max()

    # Con rampa, el mayor salto no supera el de la propia señal: no hay discontinuidad.
    assert salto_rampa <= salto_por_muestra_de_la_senal * 1.1
    # De golpe sí: el corte es mucho mayor que cualquier salto natural de la señal.
    assert salto_brusco > salto_por_muestra_de_la_senal * 5
    assert salto_brusco > salto_rampa * 5


def test_saltar_a_no_espera_la_rampa():
    linea = LineaDeRetardo(SR, retardo_ms=0.0, velocidad_ms_s=0.01)
    linea.saltar_a(20.0)
    assert linea.actual_ms == pytest.approx(20.0)
    assert linea.en_objetivo


def test_el_objetivo_se_recorta_al_maximo():
    linea = LineaDeRetardo(SR, maximo_ms=50.0)
    linea.objetivo_ms = 1000.0
    assert linea.objetivo_ms == pytest.approx(50.0)
    linea.objetivo_ms = -5.0
    assert linea.objetivo_ms == pytest.approx(0.0)


def test_rechaza_un_retardo_inicial_imposible():
    with pytest.raises(ValueError, match="fuera de"):
        LineaDeRetardo(SR, retardo_ms=500.0, maximo_ms=100.0)


def test_procesar_por_bloques_equivale_a_procesar_de_una_vez():
    """Con el retardo quieto, el troceado no debe cambiar nada."""
    x = _seno(SR // 4)
    entera = LineaDeRetardo(SR, retardo_ms=7.3)
    troceada = LineaDeRetardo(SR, retardo_ms=7.3)
    esperado = entera.procesar(x)
    obtenido = np.concatenate([troceada.procesar(x[i : i + 777]) for i in range(0, len(x), 777)])
    assert np.allclose(esperado, obtenido, atol=1e-12)


def test_un_bloque_vacio_no_rompe():
    linea = LineaDeRetardo(SR, retardo_ms=5.0)
    assert len(linea.procesar(np.zeros(0))) == 0


def test_la_lectura_de_banda_limitada_no_le_quita_agudos_a_ningun_retardo():
    """La lineal le quitaba 3,5 dB a 12,7 kHz a medio camino entre muestras."""
    t = np.arange(SR // 2) / SR
    for retardo_ms in (0.0, 13.56, 11.59, 10.0 + 0.5 / SR * 1000):
        for f in (1000.0, 8000.0, 12700.0, 16000.0):
            linea = LineaDeRetardo(SR, retardo_ms=retardo_ms, sinc=True)
            y = linea.procesar(np.sin(2 * np.pi * f * t))
            amplitud = np.sqrt(2) * y[SR // 8 :].std()
            assert abs(20 * np.log10(amplitud)) < 0.05, (retardo_ms, f, amplitud)


def test_la_lectura_de_banda_limitada_es_exacta_en_retardos_enteros():
    linea = LineaDeRetardo(SR, retardo_ms=1.0, sinc=True)
    pulso = np.zeros(1000)
    pulso[10] = 1.0
    y = linea.procesar(pulso)
    assert int(np.argmax(y)) == 10 + 48 + linea.latencia_fija
    assert np.isclose(y.max(), 1.0)
    assert np.count_nonzero(np.abs(y) > 1e-9) == 1
