"""Comprueba el estimador de retardo con retardos conocidos.

El test que más importa no es que acierte, sino el que documenta **de qué depende**: si las
referencias de dos parlantes están correlacionadas entre sí, la medición simultánea se
rompe. Esa es la razón por la que el sistema manda señales decorrelacionadas, y conviene
que quede escrita en un test y no solo en un comentario.
"""

import numpy as np
import pytest

from aurasync import estimulos, medicion
from aurasync.dsp import decorrelate

SR = 48000


def _retrasar(x: np.ndarray, muestras: float) -> np.ndarray:
    """Retardo fraccionario por rampa de fase, para poder probar precisión sub-muestra."""
    n = 1 << (len(x) + int(abs(muestras)) + 2).bit_length()
    esp = np.fft.rfft(x, n=n)
    frec = np.fft.rfftfreq(n)
    return np.fft.irfft(esp * np.exp(-2j * np.pi * frec * muestras), n=n)[: len(x) + int(muestras) + 1]


def test_recupera_un_retardo_entero():
    ref = estimulos.ruido_rosa(SR, SR, semilla=0)
    micro = np.zeros(2 * SR)
    micro[4800 : 4800 + len(ref)] += ref  # 100 ms
    est = medicion.gcc_phat(micro, ref, SR)
    assert est.retardo_ms == pytest.approx(100.0, abs=0.05)
    assert est.confiable


def test_la_precision_es_mejor_que_una_muestra():
    """Una muestra a 48 kHz son 0,021 ms. La interpolación parabólica baja de eso."""
    ref = estimulos.ruido_rosa(SR, SR, semilla=1)
    desplazado = _retrasar(ref, 350.5)  # 7,302 ms, justo entre dos muestras
    micro = np.zeros(2 * SR)
    micro[: len(desplazado)] += desplazado
    est = medicion.gcc_phat(micro, ref, SR)
    assert est.retardo_ms == pytest.approx(350.5 / SR * 1000, abs=0.01)


def test_la_confianza_baja_con_el_ruido_pero_no_sirve_de_umbral():
    """La confianza es diagnóstico, no criterio, y este test fija por qué.

    Baja con el ruido, sí. Pero su valor **sin señal** se superpone con su valor **con
    señal débil**, así que no existe un umbral que los separe. Medido: ruido puro contra una
    referencia ausente da 5,8 a 7,4, y mediciones reales buenas daban 6,4 a 16.
    """
    ref = estimulos.ruido_rosa(SR, SR, semilla=2)
    micro_limpio = np.zeros(2 * SR)
    micro_limpio[4800 : 4800 + len(ref)] += ref
    rng = np.random.default_rng(3)
    micro_sucio = micro_limpio + rng.normal(0, 20 * np.abs(micro_limpio).max(), len(micro_limpio))
    sin_senal = rng.normal(0, 1, 2 * SR)

    limpio = medicion.gcc_phat(micro_limpio, ref, SR).confianza
    sucio = medicion.gcc_phat(micro_sucio, ref, SR).confianza
    ausente = medicion.gcc_phat(sin_senal, ref, SR).confianza

    assert limpio > sucio, "con más ruido tiene que bajar"
    # Y la razón por la que no sirve de umbral: con señal débil y sin señal da parecido.
    assert abs(sucio - ausente) < 0.5 * max(sucio, ausente)


def test_el_nivel_si_detecta_un_parlante_que_no_suena():
    """Lo que la confianza no podía hacer, y por eso se usa el nivel en su lugar."""
    pistas = estimulos.calibracion(2, segundos=2.0, sr=SR, semilla=12)
    refs = dict(zip(["suena", "mudo"], pistas, strict=True))
    micro = np.zeros(int(SR * 2.5))
    micro[: len(pistas[0])] += pistas[0]  # solo el primero está en la grabación
    micro += np.random.default_rng(13).normal(0, 0.02, len(micro))

    medidos = medicion.niveles(micro, refs, dict.fromkeys(refs, 0.0), SR)
    assert medicion.parlantes_sin_sonar(medidos) == ["mudo"]


def test_mide_varios_parlantes_de_una_sola_grabacion():
    """Es lo que permite calibrar todo junto en pocos segundos."""
    verdaderos = {"a": 0.0, "b": 7.3, "c": 23.1}
    pistas = estimulos.calibracion(3, segundos=2.0, sr=SR, semilla=4)
    refs = dict(zip(verdaderos, pistas, strict=True))

    micro = np.zeros(int(SR * 3))
    for nombre, ref in refs.items():
        d = int(SR * verdaderos[nombre] / 1000)
        micro[d : d + len(ref)] += ref

    estimaciones = medicion.retardos_simultaneos(micro, refs, SR)
    relativos = medicion.relativos_a(estimaciones, "a")
    for nombre, esperado in verdaderos.items():
        assert relativos[nombre] == pytest.approx(esperado, abs=0.05)


def test_con_referencias_correlacionadas_la_medicion_se_rompe():
    """El contra-test: documenta de qué depende todo lo anterior.

    Si a dos parlantes se les manda la **misma** señal, correlacionar contra una de ellas
    encuentra también al otro, y los retardos se confunden. Por eso el sistema decorrelaciona
    antes de repartir, y por eso la decorrelación no es solo estética.
    """
    base = estimulos.ruido_rosa(int(SR * 2), SR, semilla=5)
    refs = {"a": base, "b": base}  # idénticas: el caso que no hay que hacer
    micro = np.zeros(int(SR * 3))
    micro[: len(base)] += base
    d = int(SR * 0.0231)
    micro[d : d + len(base)] += base

    estimaciones = medicion.retardos_simultaneos(micro, refs, SR)
    relativos = medicion.relativos_a(estimaciones, "a")
    # Las dos referencias son iguales, así que no hay forma de distinguirlas: el retardo
    # relativo medido es 0 aunque los parlantes estén separados 23,1 ms.
    assert relativos["b"] == pytest.approx(0.0, abs=0.01)


def test_los_filtros_del_banco_si_permiten_separar():
    """El mismo caso que el anterior, pero decorrelando: ahora sí se distinguen."""
    base = estimulos.ruido_rosa(int(SR * 2), SR, semilla=5)
    filtros = decorrelate.banco_decorrelador(2, semilla=6)
    refs = {"a": decorrelate.aplicar(base, filtros[0]), "b": decorrelate.aplicar(base, filtros[1])}

    micro = np.zeros(int(SR * 3))
    micro[: len(refs["a"])] += refs["a"]
    d = int(SR * 0.0231)
    micro[d : d + len(refs["b"])] += refs["b"]

    relativos = medicion.relativos_a(medicion.retardos_simultaneos(micro, refs, SR), "a")
    assert relativos["b"] == pytest.approx(23.1, abs=0.1)


def test_estima_los_niveles_de_cada_parlante():
    """La otra mitad de la calibración: igualar volumen sin tocar una perilla.

    Lo que tiene que acertar son las **razones** entre parlantes, no el valor absoluto: la
    corrección que sale de acá es en dB relativos.
    """
    ganancias = {"a": 1.0, "b": 0.5, "c": 0.25}
    pistas = estimulos.calibracion(3, segundos=2.0, sr=SR, semilla=7)
    refs = dict(zip(ganancias, pistas, strict=True))
    micro = np.zeros(int(SR * 2.5))
    for nombre, ref in refs.items():
        micro[: len(ref)] += ganancias[nombre] * ref

    medidos = medicion.niveles(micro, refs, dict.fromkeys(refs, 0.0), SR)
    escala = medidos["a"] / ganancias["a"]
    for nombre, esperado in ganancias.items():
        assert medidos[nombre] / escala == pytest.approx(esperado, rel=0.15)


def test_los_niveles_aguantan_un_retardo_mal_estimado():
    """El estimador por mínimos cuadrados se caía con medio milisegundo de error.

    A 48 kHz, 0,5 ms son 24 muestras: suficiente para destruir la correlación de una señal
    de banda ancha. Este estimador busca el pico en una ventana, así que no depende de que
    el retardo venga exacto.
    """
    pistas = estimulos.calibracion(2, segundos=2.0, sr=SR, semilla=11)
    refs = dict(zip(["a", "b"], pistas, strict=True))
    micro = np.zeros(int(SR * 2.5))
    micro[: len(pistas[0])] += pistas[0]
    micro[: len(pistas[1])] += 0.5 * pistas[1]

    exactos = medicion.niveles(micro, refs, {"a": 0.0, "b": 0.0}, SR)
    con_error = medicion.niveles(micro, refs, {"a": 0.5, "b": -0.5}, SR)
    for nombre in refs:
        assert con_error[nombre] == pytest.approx(exactos[nombre], rel=0.2)


def test_las_ganancias_igualan_al_mas_flojo():
    correcciones = medicion.ganancias_para_igualar({"fuerte": 1.0, "flojo": 0.5})
    assert correcciones["flojo"] == pytest.approx(0.0, abs=1e-9)
    assert correcciones["fuerte"] == pytest.approx(-6.02, abs=0.05)
    assert all(v <= 1e-9 for v in correcciones.values()), "nunca se sube, solo se baja"


def test_un_parlante_mudo_no_pide_ganancia_infinita():
    correcciones = medicion.ganancias_para_igualar({"suena": 1.0, "mudo": 0.0})
    assert correcciones["mudo"] == 0.0
    assert np.isfinite(correcciones["suena"])


def test_las_correcciones_no_agregan_latencia_de_mas():
    """Al que llega último no se le suma nada."""
    correcciones = medicion.correcciones({"a": 0.0, "b": 7.3, "c": 23.1})
    assert correcciones["c"] == pytest.approx(0.0)
    assert correcciones["a"] == pytest.approx(23.1)
    assert all(v >= 0 for v in correcciones.values())


def test_calibrar_por_ventanas_da_mediana_y_dispersion():
    verdaderos = {"a": 0.0, "b": 7.3}
    pistas = estimulos.calibracion(2, segundos=10.0, sr=SR, semilla=8)
    refs = dict(zip(verdaderos, pistas, strict=True))
    micro = np.zeros(int(SR * 11))
    for nombre, ref in refs.items():
        d = int(SR * verdaderos[nombre] / 1000)
        micro[d : d + len(ref)] += ref

    medianas, dispersiones = medicion.calibrar_por_ventanas(micro, refs, SR, ventana_s=2.0)
    assert medianas["b"] - medianas["a"] == pytest.approx(7.3, abs=0.1)
    assert dispersiones["b"] < 0.5, "las ventanas tendrían que coincidir entre sí"


def test_con_ruido_puro_la_coincidencia_entre_ventanas_confunde():
    """Deja constancia de por qué la dispersión no alcanza como criterio.

    Contra ruido puro, las ventanas pueden coincidir entre sí en un valor sin sentido. Si
    alguna vez se vuelve a proponer la dispersión como criterio único, este test lo recuerda.
    """
    refs = {"a": estimulos.ruido_rosa(int(SR * 4), SR, semilla=9)}
    micro = np.random.default_rng(10).normal(0, 1, int(SR * 4))
    _, dispersiones = medicion.calibrar_por_ventanas(micro, refs, SR, ventana_s=2.0)
    assert medicion.calibracion_confiable(dispersiones), (
        "ruido puro pasa el criterio de dispersión: por eso no es el criterio final"
    )

    # Lo que sí lo detecta es el nivel, comparando contra otro parlante que sí suena.
    otra = estimulos.ruido_rosa(int(SR * 4), SR, semilla=14)
    refs2 = {"ausente": refs["a"], "presente": otra}
    micro2 = micro.copy()
    micro2[: len(otra)] += otra
    medidos = medicion.niveles(micro2, refs2, dict.fromkeys(refs2, 0.0), SR)
    assert medicion.parlantes_sin_sonar(medidos) == ["ausente"]


def test_con_un_solo_parlante_no_se_puede_decir_si_suena():
    """El detector es relativo. Con uno solo no hay con qué comparar, y lo admite."""
    solo = {"unico": 1.0}
    assert medicion.parlantes_sin_sonar(solo) == []


def test_relativos_a_explica_si_la_referencia_no_existe():
    with pytest.raises(KeyError, match="no está entre"):
        medicion.relativos_a({"a": medicion.Estimacion(0.0, 100.0)}, "z")


def test_las_pistas_de_calibracion_son_independientes():
    """No solo decorreladas: independientes de verdad.

    Es lo que evita el fallo silencioso con niveles desparejos. La correlación entre dos
    ruidos independientes tiene que ser mucho menor que la de dos versiones filtradas del
    mismo ruido.
    """
    independientes = estimulos.calibracion(2, segundos=2.0, sr=SR, semilla=0)
    base = estimulos.ruido_rosa(int(SR * 2), SR, semilla=0)
    decorreladas = estimulos.entradas_y_salidas(base, 2, semilla=0)

    def pico(a, b):
        na, nb = np.sqrt((a**2).sum()), np.sqrt((b**2).sum())
        return float(np.abs(np.correlate(a[::16], b[::16], mode="full")).max() / (na * nb) * 16)

    assert pico(*independientes) < pico(*decorreladas)


def test_con_niveles_desparejos_las_independientes_no_se_confunden():
    """El caso que rompía todo: un parlante fuerte contaminando la medición de uno flojo."""
    verdad = {"a": 0.0, "b": 23.1}
    ganancias = {"a": 1.0, "b": 0.15}
    pistas = estimulos.calibracion(2, segundos=4.0, sr=SR, semilla=3)
    refs = dict(zip(verdad, pistas, strict=True))

    micro = np.zeros(int(SR * 5))
    for nombre, ref in refs.items():
        d = int(SR * verdad[nombre] / 1000)
        micro[d : d + len(ref)] += ganancias[nombre] * ref

    relativos = medicion.relativos_a(medicion.retardos_simultaneos(micro, refs, SR), "a")
    assert relativos["b"] == pytest.approx(23.1, abs=0.1)


def test_el_residuo_distingue_una_estimacion_buena_de_una_mala():
    """El criterio de validez que reemplazó a la coincidencia entre ventanas."""
    verdad = {"a": 0.0, "b": 10.0}
    pistas = estimulos.calibracion(2, segundos=3.0, sr=SR, semilla=4)
    refs = dict(zip(verdad, pistas, strict=True))
    micro = np.zeros(int(SR * 4))
    for nombre, ref in refs.items():
        d = int(SR * verdad[nombre] / 1000)
        micro[d : d + len(ref)] += ref

    ganancias = dict.fromkeys(refs, 1.0)
    bueno = medicion.residuo_relativo(micro, refs, verdad, ganancias, SR)
    malo = medicion.residuo_relativo(micro, refs, {"a": 0.0, "b": 35.0}, ganancias, SR)
    assert bueno < 0.05, "el modelo correcto tendría que explicar casi toda la grabación"
    assert malo > 10 * bueno, "un retardo equivocado tiene que dejar mucho más residuo"


def test_la_alineacion_gruesa_rechaza_cuando_los_canales_no_concuerdan():
    """Un canal con un pico espurio no debe arrastrar a los demás."""
    pistas = estimulos.calibracion(2, segundos=2.0, sr=SR, semilla=5)
    refs = dict(zip(["a", "b"], pistas, strict=True))
    micro = np.zeros(int(SR * 3))
    d = int(SR * 0.3)
    micro[d : d + len(pistas[0])] += pistas[0]
    # El canal "b" no está en la grabación: su estimación va a ser cualquier cosa.
    assert medicion.alineacion_gruesa(micro, refs, SR) is None


def test_la_alineacion_gruesa_acepta_cuando_concuerdan():
    verdad_ms = 300.0
    pistas = estimulos.calibracion(2, segundos=2.0, sr=SR, semilla=6)
    refs = dict(zip(["a", "b"], pistas, strict=True))
    micro = np.zeros(int(SR * 3))
    d = int(SR * verdad_ms / 1000)
    for ref in pistas:
        micro[d : d + len(ref)] += ref
    grueso = medicion.alineacion_gruesa(micro, refs, SR)
    assert grueso is not None
    assert grueso == pytest.approx(verdad_ms, abs=1.0)


# -- retardos negativos ---------------------------------------------------------------
# `calibrar` mide contra referencias ya corridas por el desfase grueso, que es la mediana
# entre parlantes: el que llega antes que la mediana queda con residuo negativo. Cuando
# `gcc_phat` no podía representarlo, agarraba un pico espurio y erraba por decenas de ms.


def test_gcc_phat_encuentra_un_retardo_negativo_si_se_le_permite():
    rng = np.random.default_rng(3)
    ref = rng.standard_normal(SR)
    # El micrófono "adelantado": la referencia aparece 5 ms después de donde está la señal.
    micro = np.concatenate([ref[int(SR * 0.005) :], np.zeros(int(SR * 0.005))])
    e = medicion.gcc_phat(micro, ref, SR, retardo_maximo_ms=50.0, retardo_minimo_ms=-50.0)
    assert e.retardo_ms == pytest.approx(-5.0, abs=0.05)


def test_por_defecto_gcc_phat_sigue_sin_buscar_negativos():
    """Contra la referencia cruda el retardo no puede ser negativo, y el cero filtra picos."""
    rng = np.random.default_rng(4)
    ref = rng.standard_normal(SR)
    micro = np.concatenate([ref[int(SR * 0.005) :], np.zeros(int(SR * 0.005))])
    assert medicion.gcc_phat(micro, ref, SR, retardo_maximo_ms=50.0).retardo_ms >= 0.0


def test_calibrar_acierta_con_el_parlante_que_llega_antes_que_la_mediana():
    """El caso que destapó el error: tres parlantes a 0, 3,4 y 7,1 ms.

    El desfase grueso da 3,4 —la mediana—, así que el de 0 ms queda con residuo negativo.
    Antes salía **47 ms** fuera de lugar, y con dispersión entre ventanas de 0,00: el
    estimador no se daba cuenta.
    """
    rng = np.random.default_rng(5)
    reales = {"a": 0.0, "b": 3.4, "c": 7.1}
    refs = {n: rng.standard_normal(SR * 3) * 0.3 for n in reales}
    micro = np.zeros(SR * 3 + SR // 5)
    for nombre, ref in refs.items():
        d = round(SR * reales[nombre] / 1000)
        micro[d : d + len(ref)] += ref

    cal = medicion.calibrar(micro, refs, SR)
    assert cal is not None
    assert cal.confiable
    ultimo = max(reales.values())
    for nombre, real in reales.items():
        assert cal.retardos_ms[nombre] == pytest.approx(ultimo - real, abs=0.05)


@pytest.mark.parametrize("semilla", range(6))
@pytest.mark.parametrize("segundos", [5.0, 10.0])
def test_los_niveles_salen_bien_con_cualquier_realizacion_del_ruido(semilla, segundos):
    """El nivel no puede depender de qué ruido rosa le tocó a cada parlante.

    Lo destapó el panel simulado (2026-10-01): con la semilla 0 y 5 s, ganancias reales de
    1 / 0,8 / 0,6 se medían como 1 / 0,53 / 0,90, porque la diafonía de graves entre ruidos
    independientes dominaba la ventana. Repetía igual entre corridas (la misma semilla da el
    mismo error), así que la estabilidad no lo podía ver.
    """
    ganancias = {"a": 1.0, "b": 0.8, "c": 0.6}
    pistas = estimulos.calibracion(3, segundos, semilla=semilla)
    refs = {n: 0.1 * p for n, p in zip(ganancias, pistas, strict=True)}
    llegadas = {"a": 144, "b": 360, "c": 576}
    micro = np.zeros(len(pistas[0]) + SR)
    for nombre, ref in refs.items():
        inicio = SR // 2 + llegadas[nombre]
        micro[inicio : inicio + len(ref)] += ganancias[nombre] * ref
    micro += 0.001 * np.random.default_rng(semilla).standard_normal(len(micro))
    medidos = medicion.niveles(micro, refs, {n: (SR // 2 + d) / SR * 1000 for n, d in llegadas.items()}, SR)
    for nombre, esperado in ganancias.items():
        error_db = 20 * np.log10((medidos[nombre] / medidos["a"]) / esperado)
        assert abs(error_db) < 0.5, f"{nombre}: {error_db:+.2f} dB"


@pytest.mark.parametrize("llegadas", [(0, 0, 0), (144, 360, 576), (576, 360, 144), (0, 48, 96)])
def test_calibrar_iguala_bien_aunque_los_parlantes_lleguen_a_destiempo(llegadas):
    """El parlante que llega antes que la mediana tiene el pico en un retraso negativo.

    `niveles` cortaba la correlación en el índice 0 y lo perdía: con llegadas de 3, 7,5 y
    12 ms, `calibrar` le pedía 0 dB al más fuerte y -14 dB al del medio (2026-10-01, lo
    destapó la calibración del panel simulado).
    """
    ganancias = {"a": 1.0, "b": 0.8, "c": 0.6}
    pistas = estimulos.calibracion(3, 5.0, semilla=0)
    refs = {n: 0.1 * p for n, p in zip(ganancias, pistas, strict=True)}
    micro = np.zeros(len(pistas[0]) + SR)
    for (nombre, ref), d in zip(refs.items(), llegadas, strict=True):
        micro[SR // 2 + d : SR // 2 + d + len(ref)] += ganancias[nombre] * ref
    micro += 0.001 * np.random.default_rng(1).standard_normal(len(micro))
    resultado = medicion.calibrar(micro, refs)
    esperadas = {n: 20 * np.log10(0.6 / g) for n, g in ganancias.items()}
    for nombre, esperada in esperadas.items():
        assert abs(resultado.ganancias_db[nombre] - esperada) < 0.5, (nombre, resultado.ganancias_db)
