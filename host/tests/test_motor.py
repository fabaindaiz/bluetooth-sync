"""Comprueba la cadena de proceso completa.

El test que más importa es el de **continuidad entre bloques**: un motor que procesa un
flujo tiene estado, y si ese estado está mal el resultado suena con clics en cada borde de
bloque. Eso no se nota en un test que procesa todo de una vez, y sí se oye.
"""

import numpy as np
import pytest

from aurasync import motor
from aurasync.config import Instalacion, Parlante

SR = 48000


def _instalacion(n: int = 3, **extra) -> Instalacion:
    roles = [
        {"pan": -0.7, "ambiente": 0.0},
        {"pan": 0.7, "ambiente": 0.0},
        {"pan": 0.0, "ambiente": 1.0},
    ]
    return Instalacion(parlantes=[Parlante(f"p{i}", f"sink{i}", **roles[i % len(roles)], **extra) for i in range(n)])


def _estereo(n: int = SR, semilla: int = 0):
    rng = np.random.default_rng(semilla)
    return rng.standard_normal(n), rng.standard_normal(n)


def test_devuelve_una_senal_por_parlante_del_largo_correcto():
    m = motor.Motor(_instalacion(), SR)
    izq, der = _estereo(4096)
    salida = m.procesar(izq, der)
    assert set(salida) == {"p0", "p1", "p2"}
    assert all(len(v) == 4096 for v in salida.values())


def test_procesar_por_bloques_da_lo_mismo_que_de_una_vez():
    """Si esto falla, el flujo va a sonar con un clic en cada borde de bloque."""
    izq, der = _estereo(SR)

    entero = motor.Motor(_instalacion(), SR)
    esperado = entero.procesar(izq, der)

    troceado = motor.Motor(_instalacion(), SR)
    obtenido = motor.procesar_completo(troceado, izq, der, bloque=1024)

    for nombre in esperado:
        # La convolución y el retardo son exactos; la extracción de ambiente introduce una
        # diferencia chica en los bordes de bloque, porque una STFT no se parte sin costo.
        a, b = esperado[nombre], obtenido[nombre]
        assert len(a) == len(b)
        error = np.sqrt(((a - b) ** 2).mean()) / (np.sqrt((a**2).mean()) + 1e-12)
        assert error < 0.05, f"{nombre}: {error:.3%} de diferencia entre bloques y entero"


def test_sin_ambiente_los_bloques_son_exactos():
    """Sin la STFT de por medio, la cadena es exacta muestra a muestra."""
    izq, der = _estereo(SR // 2)
    entero = motor.Motor(_instalacion(), SR, extraer_ambiente=False)
    troceado = motor.Motor(_instalacion(), SR, extraer_ambiente=False)
    esperado = entero.procesar(izq, der)
    obtenido = motor.procesar_completo(troceado, izq, der, bloque=777)
    for nombre in esperado:
        assert np.allclose(esperado[nombre], obtenido[nombre], atol=1e-12)


def test_el_pan_reparte_los_canales():
    """Un parlante con pan -1 solo recibe el izquierdo."""
    inst = Instalacion(parlantes=[Parlante("izq", "s0", pan=-1.0), Parlante("der", "s1", pan=1.0)])
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False)
    izq = np.full(1000, 0.5)
    der = np.zeros(1000)
    salida = m.procesar(izq, der)
    desde = 2 * m.latencia_retardo  # lo que tarda en llenarse la lectura de banda limitada
    assert np.allclose(salida["izq"][desde:], 0.5)
    assert np.allclose(salida["der"], 0.0)


def test_el_retardo_de_haas_se_aplica_en_proporcion_al_ambiente():
    """Un parlante sin ambiente no se aleja; uno con ambiente pleno se retrasa entero."""
    inst = Instalacion(
        parlantes=[Parlante("frente", "s0", ambiente=0.0), Parlante("atras", "s1", ambiente=1.0)],
        retardo_traseros_ms=12.0,
    )
    m = motor.Motor(inst, SR)
    efectivos = m.retardos_efectivos_ms()
    assert efectivos["frente"] == pytest.approx(0.0)
    assert efectivos["atras"] == pytest.approx(12.0)


def test_el_retardo_corre_la_senal_lo_que_dice():
    inst = Instalacion(
        parlantes=[Parlante("a", "s0"), Parlante("b", "s1", retardo_ms=10.0)],
        retardo_traseros_ms=0.0,
    )
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False)
    pulso = np.zeros(SR)
    pulso[100] = 1.0
    salida = m.procesar(pulso, pulso)
    fija = m.latencia_retardo
    assert int(np.argmax(salida["a"])) == 100 + fija
    assert int(np.argmax(salida["b"])) == 100 + int(SR * 0.010) + fija


def test_la_ganancia_se_aplica_en_decibeles():
    inst = Instalacion(parlantes=[Parlante("a", "s0"), Parlante("b", "s1", ganancia_db=-6.0206)])
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False)
    x = np.full(1000, 0.5)
    salida = m.procesar(x, x)
    assert np.allclose(salida["b"] / salida["a"], 0.5, atol=1e-3)


def test_las_salidas_estan_decorrelacionadas_entre_si():
    """Es la propiedad que produce el envolvimiento, y la que permite medir después."""
    izq, der = _estereo(SR)
    m = motor.Motor(_instalacion(), SR, extraer_ambiente=False)
    salida = m.procesar(izq, der)
    corr = np.corrcoef(salida["p0"], salida["p1"])[0, 1]
    assert abs(corr) < 0.6, f"p0 y p1 siguen muy correlacionados: {corr:.2f}"


def test_sin_decorrelar_las_salidas_del_mismo_rol_son_identicas():
    """El contraste que muestra qué aporta la decorrelación."""
    inst = Instalacion(parlantes=[Parlante("a", "s0"), Parlante("b", "s1")])
    izq, der = _estereo(8192)
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False)
    salida = m.procesar(izq, der)
    assert np.allclose(salida["a"], salida["b"])


def test_reiniciar_borra_el_estado():
    """Al empezar una reproducción nueva, la cola del anterior no debe filtrarse."""
    izq, der = _estereo(4096)
    m = motor.Motor(_instalacion(), SR)
    primera = m.procesar(izq, der)
    m.reiniciar()
    segunda = m.procesar(izq, der)
    for nombre in primera:
        assert np.allclose(primera[nombre], segunda[nombre])


def test_avisa_si_hay_mas_parlantes_que_filtros_decorrelados():
    """El límite del paper: con filtros fijos salen 5 o 6 señales, no más."""
    muchos = Instalacion(parlantes=[Parlante(f"p{i}", f"s{i}") for i in range(9)])
    with pytest.raises(ValueError, match="decorrelacionadas"):
        motor.Motor(muchos, SR)
    # Sin decorrelar sí se puede, porque entonces no hay filtros que repartir.
    motor.Motor(muchos, SR, decorrelar=False)


def test_rechaza_una_instalacion_vacia():
    with pytest.raises(ValueError, match="no tiene parlantes"):
        motor.Motor(Instalacion(), SR)


def test_rechaza_canales_de_distinto_largo():
    m = motor.Motor(_instalacion(), SR)
    with pytest.raises(ValueError, match="largos distintos"):
        m.procesar(np.zeros(100), np.zeros(200))


# -- actualización de parámetros mientras suena -------------------------------------
# Es lo que necesita el lazo de recalibración: corregir el retardo de un parlante sin
# parar, porque cada arranque de A2DP trae un desfase distinto.


def _un_parlante(**extra) -> Instalacion:
    return Instalacion(parlantes=[Parlante("solo", "s0", **extra)], retardo_traseros_ms=0.0)


def test_actualizar_mueve_el_retardo_gradualmente():
    inst = _un_parlante()
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False, velocidad_retardo_ms_s=1.0)
    assert m.retardos_actuales_ms()["solo"] == pytest.approx(0.0)

    inst.parlantes[0].retardo_ms = 10.0
    m.actualizar()
    m.procesar(np.zeros(SR), np.zeros(SR))  # un segundo
    # A 1 ms/s, en un segundo avanza 1 ms: ni se queda quieto ni salta a los 10.
    assert m.retardos_actuales_ms()["solo"] == pytest.approx(1.0, abs=0.01)


def test_el_retardo_llega_al_objetivo_si_se_le_da_tiempo():
    inst = _un_parlante()
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False, velocidad_retardo_ms_s=5.0)
    inst.parlantes[0].retardo_ms = 4.0
    m.actualizar()
    for _ in range(2):
        m.procesar(np.zeros(SR), np.zeros(SR))
    assert m.retardos_actuales_ms()["solo"] == pytest.approx(4.0, abs=1e-6)


def test_cambiar_el_retardo_en_caliente_no_produce_un_clic():
    """El mismo criterio que en la línea de retardo, pero por la cadena completa."""
    hz, bloque = 220.0, 2048
    inst = _un_parlante(retardo_ms=5.0)
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False, velocidad_retardo_ms_s=0.5)

    salidas, fase = [], 0
    for i in range(8):
        if i == 4:
            inst.parlantes[0].retardo_ms = 9.0
            m.actualizar()
        x = np.sin(2 * np.pi * hz * (fase + np.arange(bloque)) / SR)
        salidas.append(m.procesar(x, x)["solo"])
        fase += bloque
    y = np.concatenate(salidas)

    natural = np.abs(np.diff(np.sin(2 * np.pi * hz * np.arange(bloque) / SR))).max()
    assert np.abs(np.diff(y)).max() <= natural * 1.1


def test_la_ganancia_cambia_con_rampa_y_no_de_golpe():
    inst = _un_parlante()
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False, velocidad_ganancia_db_s=6.0)
    x = np.full(SR // 10, 0.5)  # 100 ms, bajo el techo del limitador
    m.procesar(x, x)

    inst.parlantes[0].ganancia_db = -20.0
    salida = m.procesar(x, x)["solo"]
    # En 100 ms, a 6 dB/s, solo puede bajar 0,6 dB: ni se queda ni llega a -20.
    assert salida[-1] == pytest.approx(0.5 * 10 ** (-0.6 / 20), rel=0.02)
    # Y dentro del bloque el cambio es continuo, no un escalón.
    assert np.abs(np.diff(salida)).max() < 0.01


def test_el_limitador_no_deja_pasar_de_escala_completa():
    """La ecualización solo realza: lo que se pasa lo baja el limitador, no lo recorta pw-play."""
    from aurasync.dsp import limiter

    inst = Instalacion(parlantes=[Parlante("a", "s0", ganancia_db=6.0)])
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False)
    x = np.sin(np.arange(4800) / 7)
    assert np.abs(m.procesar(x, x)["a"]).max() <= limiter.CEILING + 1e-9


def test_la_ganancia_llega_a_su_objetivo():
    inst = _un_parlante()
    m = motor.Motor(inst, SR, extraer_ambiente=False, decorrelar=False, velocidad_ganancia_db_s=60.0)
    inst.parlantes[0].ganancia_db = -6.0206
    x = np.ones(SR // 2)
    for _ in range(3):
        salida = m.procesar(x, x)["solo"]
    assert salida[-1] == pytest.approx(0.5, rel=0.01)
