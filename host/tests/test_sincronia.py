"""Comprueba el lazo de recalibración: qué acepta, qué descarta y cómo converge.

El controlador existe porque **el sensor se equivoca a veces**, así que casi todos estos
tests son mediciones falsas a propósito, comprobando que no se escriben. El caso feliz es
uno solo; el resto son las trampas.
"""

import time

import numpy as np
import pytest

from aurasync import sincronia
from aurasync.config import Instalacion, Parlante
from aurasync.medicion import Calibracion
from aurasync.motor import Motor
from aurasync.sincronia import Controlador


def _instalacion(**retardos) -> Instalacion:
    return Instalacion(
        parlantes=[Parlante(n, f"sink_{n}", retardo_ms=r) for n, r in (retardos or {"a": 0.0, "b": 0.0}).items()],
        retardo_traseros_ms=0.0,
    )


def _cal(retardos, *, estabilidad=0.1, ganancias=None) -> Calibracion:
    """Una calibración sintética. Por defecto, estable y sin corrección de nivel."""
    return Calibracion(
        retardos_ms=dict(retardos),
        ganancias_db=dict(ganancias or dict.fromkeys(retardos, 0.0)),
        estabilidad_ms=dict.fromkeys(retardos, estabilidad),
        desfase_grueso_ms=0.0,
    )


class _Reloj:
    """Un reloj que avanza cuando se le dice, para probar la deriva sin esperar."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


# -- lo que se acepta ----------------------------------------------------------------


def test_aplica_una_medicion_buena():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0)
    ajuste = c.proponer(_cal({"a": 4.0, "b": 0.0}))
    assert ajuste.aceptado
    assert inst.por_nombre("a").retardo_ms == pytest.approx(4.0)
    assert inst.por_nombre("b").retardo_ms == pytest.approx(0.0)


def test_la_correccion_se_suma_a_la_que_ya_estaba():
    """El micrófono mide el residuo, no el desfase desnudo.

    Si el controlador tratara la medición como un reemplazo, en cada vuelta desharía su
    propio trabajo y el lazo nunca convergería.
    """
    inst = _instalacion(a=3.0, b=0.0)
    c = Controlador(inst, factor=1.0)
    c.proponer(_cal({"a": 2.0, "b": 0.0}))  # todavía le faltan 2 ms a "a"
    assert inst.por_nombre("a").retardo_ms == pytest.approx(5.0)


def test_el_lazo_converge_y_despues_se_queda_quieto():
    """Con factor 0,5 el error se divide a la mitad en cada vuelta hasta la zona muerta."""
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=0.5, zona_muerta_ms=0.5)
    error = 5.0
    for _ in range(10):
        c.proponer(_cal({"a": error, "b": 0.0}))
        error = 5.0 - inst.por_nombre("a").retardo_ms
    assert abs(error) <= 0.5
    # Y una vez dentro de la zona muerta, deja de escribir.
    quieto = inst.por_nombre("a").retardo_ms
    c.proponer(_cal({"a": error, "b": 0.0}))
    assert inst.por_nombre("a").retardo_ms == pytest.approx(quieto)


def test_no_acumula_latencia_de_mas():
    """Después de cada ajuste, el parlante más adelantado queda en cero."""
    inst = _instalacion(a=10.0, b=10.0)
    c = Controlador(inst, factor=1.0)
    c.proponer(_cal({"a": 2.0, "b": 0.0}))
    assert min(p.retardo_ms for p in inst.parlantes) == pytest.approx(0.0)
    assert inst.por_nombre("a").retardo_ms == pytest.approx(2.0)


# -- lo que se descarta --------------------------------------------------------------


def test_descarta_una_medicion_inestable():
    from aurasync.medicion import ESTABILIDAD_MAXIMA_MS

    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst)
    ajuste = c.proponer(_cal({"a": 4.0, "b": 0.0}, estabilidad=ESTABILIDAD_MAXIMA_MS + 1))
    assert not ajuste.aceptado
    assert "no es estable" in ajuste.motivo
    assert inst.por_nombre("a").retardo_ms == 0.0


def test_descarta_toda_la_medicion_aunque_falle_un_solo_parlante():
    """Los retardos son relativos y después se renormalizan: un canal malo corre a todos."""
    inst = _instalacion(a=0.0, b=0.0, c=0.0)
    c = Controlador(inst)
    cal = Calibracion(
        retardos_ms={"a": 3.0, "b": 0.0, "c": 1.0},
        ganancias_db=dict.fromkeys("abc", 0.0),
        estabilidad_ms={"a": 0.1, "b": 0.1, "c": 99.0},
        desfase_grueso_ms=0.0,
    )
    assert not c.proponer(cal).aceptado
    assert all(p.retardo_ms == 0.0 for p in inst.parlantes)


def test_una_calibracion_que_no_se_pudo_alinear_no_rompe_nada():
    inst = _instalacion()
    c = Controlador(inst)
    ajuste = c.proponer(None)
    assert not ajuste.aceptado
    assert not ajuste.hubo_cambios


def test_no_persigue_ruido_por_debajo_de_la_zona_muerta():
    """0,3 ms es el propio MAD del instrumento: moverse por eso es perseguir ruido."""
    inst = _instalacion(a=1.0, b=0.0)
    c = Controlador(inst, zona_muerta_ms=0.5)
    ajuste = c.proponer(_cal({"a": 0.3, "b": 0.0}))
    assert ajuste.aceptado
    assert not ajuste.hubo_cambios
    assert inst.por_nombre("a").retardo_ms == pytest.approx(1.0)


# -- el salto grande, que es el caso ambiguo -----------------------------------------


def test_un_salto_grande_no_se_aplica_la_primera_vez():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, salto_maximo_ms=15.0)
    ajuste = c.proponer(_cal({"a": 40.0, "b": 0.0}))
    assert not ajuste.aceptado
    assert "confirmación" in ajuste.motivo
    assert inst.por_nombre("a").retardo_ms == 0.0


def test_un_salto_grande_que_se_repite_termina_aplicandose():
    """Un resync real de A2DP se ve igual en la medición siguiente; un error de pico, no."""
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, salto_maximo_ms=15.0, factor=1.0, confirmaciones=2)
    c.proponer(_cal({"a": 40.0, "b": 0.0}))
    ajuste = c.proponer(_cal({"a": 40.0, "b": 0.0}))
    assert ajuste.aceptado
    assert "confirmado" in ajuste.motivo
    assert inst.por_nombre("a").retardo_ms == pytest.approx(40.0)


def test_dos_saltos_grandes_distintos_no_se_confirman_entre_si():
    """Es el patrón del error de pico: grande, pero cada vez en otro lado."""
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, salto_maximo_ms=15.0, confirmaciones=2)
    c.proponer(_cal({"a": 40.0, "b": 0.0}))
    ajuste = c.proponer(_cal({"a": -90.0, "b": 0.0}))
    assert not ajuste.aceptado
    assert inst.por_nombre("a").retardo_ms == 0.0


def test_una_medicion_normal_borra_el_salto_pendiente():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, salto_maximo_ms=15.0, factor=1.0, confirmaciones=2)
    c.proponer(_cal({"a": 40.0, "b": 0.0}))  # queda pendiente
    c.proponer(_cal({"a": 2.0, "b": 0.0}))  # normal: se aplica y limpia el pendiente
    ajuste = c.proponer(_cal({"a": 38.0, "b": 0.0}))  # vuelve a 40 en total: otra vez pendiente
    assert not ajuste.aceptado


# -- niveles --------------------------------------------------------------------------


def test_corrige_el_nivel_con_su_propia_zona_muerta():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, zona_muerta_db=1.0)
    c.proponer(_cal({"a": 0.0, "b": 0.0}, ganancias={"a": -4.0, "b": -0.5}))
    assert inst.por_nombre("a").ganancia_db == pytest.approx(-4.0)
    assert inst.por_nombre("b").ganancia_db == pytest.approx(0.0)


def test_recorta_una_correccion_de_nivel_absurda_en_vez_de_confirmarla():
    """Saturar un parlante se oye enseguida, así que el nivel se acota y no se espera."""
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, salto_maximo_db=6.0)
    c.proponer(_cal({"a": 0.0, "b": 0.0}, ganancias={"a": 30.0, "b": 0.0}))
    assert inst.por_nombre("a").ganancia_db == pytest.approx(6.0)


def test_una_ganancia_no_finita_se_ignora():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0)
    c.proponer(_cal({"a": 0.0, "b": 0.0}, ganancias={"a": float("nan"), "b": 0.0}))
    assert inst.por_nombre("a").ganancia_db == 0.0


# -- integración con el motor ---------------------------------------------------------


def test_el_motor_recibe_los_objetivos_nuevos_sin_reiniciarse():
    inst = _instalacion(a=0.0, b=0.0)
    m = Motor(inst, 48000, extraer_ambiente=False, decorrelar=False, velocidad_retardo_ms_s=100.0)
    c = Controlador(inst, m, factor=1.0)
    c.proponer(_cal({"a": 3.0, "b": 0.0}))
    # El objetivo cambió, pero el retardo todavía no llegó: se mueve con rampa.
    assert m.retardos_actuales_ms()["a"] == pytest.approx(0.0)
    m.procesar(np.zeros(48000), np.zeros(48000))
    assert m.retardos_actuales_ms()["a"] == pytest.approx(3.0)


# -- la deriva, que el lazo mide sin proponérselo --------------------------------------


def test_no_estima_deriva_sin_puntos_suficientes():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst)
    assert c.deriva_ms_h() is None
    c.proponer(_cal({"a": 2.0, "b": 0.0}))
    assert c.deriva_ms_h() is None


def test_estima_la_deriva_a_partir_de_lo_que_tuvo_que_corregir():
    """Un parlante que se atrasa 1 ms cada 10 minutos son 6 ms/h."""
    reloj = _Reloj()
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, reloj=reloj)
    for i in range(5):
        reloj.t = i * 600.0
        c.proponer(_cal({"a": 1.0, "b": 0.0}))
    deriva = c.deriva_ms_h()
    assert deriva["a"] == pytest.approx(6.0, rel=0.05)
    assert deriva["b"] == pytest.approx(0.0, abs=1e-9)


def test_el_historial_guarda_lo_aplicado_con_su_marca_de_tiempo():
    reloj = _Reloj()
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, reloj=reloj)
    reloj.t = 17.0
    c.proponer(_cal({"a": 2.0, "b": 0.0}))
    assert c.historial == [(17.0, {"a": 2.0, "b": 0.0})]


# -- confirmar_todo: el modo para cuando la referencia es el propio contenido ---------
# Con referencias correlacionadas el estimador falla **en silencio**: pasa el filtro de
# estabilidad y devuelve un número equivocado. Lo único que distingue ese error de un
# desfase real es que no se repite.


def test_con_confirmar_todo_un_cambio_chico_tampoco_pasa_a_la_primera():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, confirmar_todo=True)
    ajuste = c.proponer(_cal({"a": 3.0, "b": 0.0}))
    assert not ajuste.aceptado
    assert "confirmación" in ajuste.motivo
    assert inst.por_nombre("a").retardo_ms == 0.0


def test_con_confirmar_todo_lo_que_se_repite_se_aplica():
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, confirmar_todo=True)
    c.proponer(_cal({"a": 3.0, "b": 0.0}))
    assert c.proponer(_cal({"a": 3.0, "b": 0.0})).aceptado
    assert inst.por_nombre("a").retardo_ms == pytest.approx(3.0)


def test_con_confirmar_todo_un_error_que_no_se_repite_no_se_aplica():
    """El patrón medido en simulación: 8,05 ms en un segmento y 0,00 en el siguiente."""
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, factor=1.0, confirmar_todo=True)
    c.proponer(_cal({"a": 8.05, "b": 0.0}))
    c.proponer(_cal({"a": 0.0, "b": 0.0}))
    c.proponer(_cal({"a": 8.05, "b": 0.0}))
    assert inst.por_nombre("a").retardo_ms == 0.0


def test_la_zona_muerta_gana_sobre_confirmar_todo():
    """Estar alineado no es un cambio: no tiene nada que confirmar."""
    inst = _instalacion(a=0.0, b=0.0)
    c = Controlador(inst, confirmar_todo=True, zona_muerta_ms=0.5)
    ajuste = c.proponer(_cal({"a": 0.2, "b": 0.0}))
    assert ajuste.aceptado
    assert not ajuste.hubo_cambios


# -- la ventana de emisión: la referencia del lazo ------------------------------------


def _ventana(segundos=1.0, sr=10):
    return sincronia.VentanaDeEmision(["a", "b"], sr=sr, segundos=segundos)


def test_la_ventana_devuelve_lo_emitido_en_orden():
    v = _ventana()  # 10 muestras por parlante
    for i in range(0, 12, 2):
        v.agregar({"a": np.array([i, i + 1], dtype=float), "b": -np.array([i, i + 1], dtype=float)})
    refs = v.referencias(segundos=0.5, margen_s=0.0)
    assert refs is not None
    assert np.allclose(refs["a"], [7, 8, 9, 10, 11])
    assert np.allclose(refs["b"], [-7, -8, -9, -10, -11])


def test_la_ventana_deja_fuera_el_margen_que_todavia_no_se_oyo():
    """Lo último escrito está en el buffer de pw-play: no tiene par en la grabación."""
    v = _ventana()
    v.agregar({"a": np.arange(12.0), "b": np.arange(12.0)})
    refs = v.referencias(segundos=0.5, margen_s=0.2)  # 5 muestras, salteando las últimas 2
    assert np.allclose(refs["a"], [5, 6, 7, 8, 9])


def test_la_ventana_no_devuelve_nada_hasta_tener_suficiente():
    v = _ventana()
    assert v.referencias(segundos=0.5, margen_s=0.0) is None
    v.agregar({"a": np.arange(3.0), "b": np.arange(3.0)})
    assert v.referencias(segundos=0.5, margen_s=0.0) is None
    v.agregar({"a": np.arange(3.0), "b": np.arange(3.0)})
    assert v.referencias(segundos=0.5, margen_s=0.0) is not None


def test_la_ventana_nunca_devuelve_mas_de_lo_que_mide():
    v = _ventana()
    v.agregar({"a": np.arange(40.0), "b": np.arange(40.0)})
    assert v.referencias(segundos=2.0, margen_s=0.0) is None


def test_la_ventana_rechaza_bloques_de_largos_distintos():
    v = _ventana()
    with pytest.raises(ValueError, match="largos distintos"):
        v.agregar({"a": np.zeros(4), "b": np.zeros(5)})


def test_la_ventana_rechaza_un_juego_de_parlantes_distinto():
    v = _ventana()
    with pytest.raises(ValueError, match="se esperaban"):
        v.agregar({"a": np.zeros(4), "c": np.zeros(4)})


def test_la_ventana_ignora_un_bloque_vacio():
    v = _ventana()
    v.agregar({"a": np.zeros(0), "b": np.zeros(0)})
    assert not v.lleno


def test_la_ventana_exige_al_menos_un_parlante():
    with pytest.raises(ValueError, match="ningún parlante"):
        sincronia.VentanaDeEmision([])


def test_limpiar_vuelve_la_ventana_a_cero():
    v = _ventana()
    v.agregar({"a": np.arange(20.0), "b": np.arange(20.0)})
    assert v.lleno
    v.limpiar()
    assert not v.lleno
    assert v.referencias(segundos=0.5, margen_s=0.0) is None


def test_hay_senal_distingue_el_silencio_de_la_musica():
    """Medir contra silencio daría un número igual de bien formado y sin sentido."""
    v = _ventana()
    rng = np.random.default_rng(0)
    assert not v.hay_senal({"a": np.zeros(100), "b": np.zeros(100)})
    assert not v.hay_senal({"a": rng.standard_normal(100) * 0.3, "b": np.zeros(100)})
    assert v.hay_senal({n: rng.standard_normal(100) * 0.3 for n in ("a", "b")})


# -- la medición en segundo plano -----------------------------------------------------
# Existe porque `calibrar` cuesta 1,00 s de CPU y en el hilo de audio eso vacía el buffer
# de los parlantes, que es peor que no medir.


def test_el_medidor_no_bloquea_y_entrega_despues():
    import threading

    listo = threading.Event()
    m = sincronia.MedicionEnSegundoPlano(lambda x: (listo.wait(2.0), x * 2)[1])
    assert m.lanzar(21)
    assert m.ocupado
    assert m.recoger() == (False, None)  # todavía no: no bloqueó
    listo.set()
    for _ in range(200):
        hay, valor = m.recoger()
        if hay:
            assert valor == 42
            break
        time.sleep(0.01)
    else:
        pytest.fail("la medición nunca entregó resultado")
    m.cerrar()


def test_el_medidor_no_encola_dos():
    import threading

    seguir = threading.Event()
    m = sincronia.MedicionEnSegundoPlano(lambda: seguir.wait(2.0))
    assert m.lanzar()
    assert not m.lanzar()  # la segunda se saltea en vez de encolarse
    assert m.lanzadas == 1
    seguir.set()
    m.cerrar()


def test_un_resultado_none_se_distingue_de_no_haber_terminado():
    """`calibrar` devuelve `None` cuando no pudo alinear: es un resultado, no una espera."""
    m = sincronia.MedicionEnSegundoPlano(lambda: None)
    m.lanzar()
    for _ in range(200):
        hay, valor = m.recoger()
        if hay:
            assert valor is None
            break
        time.sleep(0.01)
    else:
        pytest.fail("la medición nunca entregó resultado")
    m.cerrar()


def test_una_medicion_que_revienta_no_corta_la_reproduccion():
    def explota():
        msg = "el micrófono se desconectó"
        raise RuntimeError(msg)

    m = sincronia.MedicionEnSegundoPlano(explota)
    m.lanzar()
    for _ in range(200):
        hay, valor = m.recoger()
        if hay:
            assert isinstance(valor, RuntimeError)
            break
        time.sleep(0.01)
    else:
        pytest.fail("la medición nunca entregó resultado")
    m.cerrar()


# -- el lazo entero, sin audio ---------------------------------------------------------


def test_el_lazo_completo_converge_contra_un_microfono_simulado():
    """Motor → ventana de emisión → micrófono simulado → calibrar → controlador.

    Es la prueba que habría cazado el error de `gcc_phat`: acá el desfase verdadero se
    conoce, así que se puede comprobar que el lazo lo corrige y no que solo "hace algo".
    Con referencias de ruido independiente, que es el caso preciso; el contenido real se
    evalúa en `docs/research/experimentos/08-…`.
    """
    from aurasync import medicion

    sr = 48000
    reales = {"a": 0.0, "b": 3.4, "c": 7.1}  # lo que el lazo tiene que descubrir
    inst = Instalacion(
        parlantes=[Parlante(n, f"sink_{n}") for n in reales],
        retardo_traseros_ms=0.0,
    )
    c = Controlador(inst, factor=1.0, confirmar_todo=True, zona_muerta_ms=0.5)
    rng = np.random.default_rng(11)

    def medir() -> object:
        """Un ciclo: emite ruido, lo capta el micrófono con los desfases reales y calibra."""
        refs = {n: rng.standard_normal(int(sr * 2.0)) * 0.3 for n in reales}
        mic = np.zeros(int(sr * 2.3))
        for nombre, ref in refs.items():
            # Lo que llega al micrófono es el desfase real **menos** lo ya corregido.
            retardo = reales[nombre] + inst.por_nombre(nombre).retardo_ms
            d = round(sr * (retardo - min(reales.values())) / 1000)
            mic[d : d + len(ref)] += ref
        return medicion.calibrar(mic, refs, sr)

    for _ in range(6):
        c.proponer(medir())

    # Alineado quiere decir que el tiempo total de cada parlante es el mismo.
    totales = [reales[n] + inst.por_nombre(n).retardo_ms for n in reales]
    assert max(totales) - min(totales) <= 0.5, f"quedaron desalineados: {totales}"


def test_la_ventana_del_microfono_deja_la_referencia_dentro_del_rango_grueso():
    """`alineacion_gruesa` busca hasta 1500 ms: la referencia tiene que empezar antes.

    Con la ventana de medición al final del anillo menos el margen, y la tajada del
    micrófono `MARGEN_DEL_MICROFONO_S` más larga, la referencia arranca a
    `MARGEN_DEL_MICROFONO_S - MARGEN_S` segundos más la latencia de reproducción.
    """
    v = sincronia.VentanaDeEmision
    sobrante_s = v.MARGEN_DEL_MICROFONO_S - v.MARGEN_S
    assert sobrante_s > 0, "la tajada del micrófono tiene que cubrir el margen"
    latencia_tolerada_ms = 1500 - sobrante_s * 1000
    assert latencia_tolerada_ms >= 1000, (
        f"solo tolera {latencia_tolerada_ms:.0f} ms de latencia de reproducción antes de "
        "salirse del rango de la alineación gruesa"
    )


def test_las_dos_ventanas_se_corresponden_con_la_latencia_de_reproduccion():
    """Simula el lazo de `aurasync run` completo, sin PipeWire y sin parlantes.

    Es la prueba de la aritmética de ventanas: la del contenido emitido y la del micrófono
    tienen que quedar alineadas aunque el sonido se oiga cientos de milisegundos después de
    escribirse. Equivocarse acá no da un error: da mediciones que no alinean nunca, y eso en
    una prueba con parlantes se ve como "el lazo no hace nada".
    """
    from aurasync import medicion, sonido

    sr, bloque = 48000, 4096
    medir, margen_mic = 3.0, sincronia.VentanaDeEmision.MARGEN_DEL_MICROFONO_S
    latencia_s = 0.25  # pw-play más el buffer de A2DP
    reales_ms = {"a": 0.0, "b": 3.4, "c": 7.1}

    emision = sincronia.VentanaDeEmision(list(reales_ms), sr, segundos=medir + 2.0)
    micro = sonido.MicrofonoContinuo("falso", sr, segundos=medir + 4.0)

    rng = np.random.default_rng(23)
    total = int(sr * (medir + 3.0))
    emitido = {n: rng.standard_normal(total) * 0.3 for n in reales_ms}
    # Lo que llegaría al micrófono: la suma, con la latencia común y el desfase de cada uno.
    captado = np.zeros(total)
    for nombre, x in emitido.items():
        d = round(sr * (latencia_s + reales_ms[nombre] / 1000))
        captado[d:] += x[: total - d]

    for i in range(0, total, bloque):
        emision.agregar({n: x[i : i + bloque] for n, x in emitido.items()})
        micro.agregar(captado[i : i + bloque])

    referencias = emision.referencias(medir)
    grabado = micro.ultimos(medir + margen_mic)
    assert referencias is not None
    assert grabado is not None
    assert emision.hay_senal(referencias)

    cal = medicion.calibrar(grabado, referencias, sr)
    assert cal is not None, "no alineó: la ventana del micrófono no contiene la referencia"
    assert cal.confiable
    ultimo = max(reales_ms.values())
    for nombre, real in reales_ms.items():
        assert cal.retardos_ms[nombre] == pytest.approx(ultimo - real, abs=0.1)


def test_la_medicion_en_otro_proceso_devuelve_lo_mismo():
    from aurasync import medicion

    rng = np.random.default_rng(5)
    sr = 48000
    refs = {n: rng.standard_normal(int(sr * 2.0)) * 0.3 for n in ("a", "b")}
    mic = np.zeros(int(sr * 2.3))
    mic[480 : 480 + len(refs["a"])] += refs["a"]
    mic[960 : 960 + len(refs["b"])] += 0.8 * refs["b"]
    m = sincronia.MedicionEnSegundoPlano(medicion.calibrar, en_proceso=True)
    try:
        assert m.lanzar(mic, refs, sr)
        fin = time.monotonic() + 60
        listo, resultado = False, None
        while not listo and time.monotonic() < fin:
            time.sleep(0.05)
            listo, resultado = m.recoger()
        assert listo
        assert not isinstance(resultado, Exception), resultado
        assert resultado == medicion.calibrar(mic, refs, sr)
    finally:
        m.cerrar()
