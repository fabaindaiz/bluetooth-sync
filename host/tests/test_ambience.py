"""Comprueba la extracción de ambiente contra los tres casos que la definen.

El paper describe qué tendría que pasar en cada uno; estos tests lo verifican:

1. **L = R** (mono perfecto): coherencia 1, índice de ambiente 0 → no hay ambiente.
2. **L y R independientes**: coherencia 0, índice 1 → todo es ambiente.
3. **Fuente paneada del todo a un lado**: la coherencia también da bajo, pero **no** es
   ambiente. Es el criterio adicional del paper, y sin él la extracción se rompe con
   cualquier mezcla que tenga instrumentos a los costados.

Antes de los tres, se comprueba que el andamiaje de STFT reconstruye exacto: si eso
falla, los otros resultados no significan nada.
"""

import numpy as np
import pytest

from aurasync.dsp import ambience

SR = 48000
LARGO = SR  # un segundo


def _ruido(semilla: int, largo: int = LARGO) -> np.ndarray:
    return np.random.default_rng(semilla).standard_normal(largo)


def _energia(x: np.ndarray) -> float:
    return float(np.sqrt((x**2).mean()))


def test_la_reconstruccion_sin_modificar_es_exacta():
    """Análisis y síntesis tienen que cancelarse. Se mira la parte central, porque en los
    bordes la suma de ventanas todavía no llegó a régimen."""
    x = _ruido(0)
    y = ambience.reconstruir(x)
    centro = slice(ambience.N_FFT, LARGO - ambience.N_FFT)
    assert np.allclose(y[centro], x[centro], atol=1e-9)


def test_mono_perfecto_no_tiene_ambiente():
    """Con L = R la coherencia es 1, así que no hay nada que extraer."""
    x = _ruido(1)
    indice = ambience.indice_ambiente(x, x)
    assert float(np.median(indice)) < 0.05

    amb_izq, amb_der = ambience.extraer(x, x)
    centro = slice(ambience.N_FFT, LARGO - ambience.N_FFT)
    assert _energia(amb_izq[centro]) < 0.1 * _energia(x[centro])
    assert _energia(amb_der[centro]) < 0.1 * _energia(x[centro])


def test_canales_independientes_son_todo_ambiente():
    """Dos ruidos sin relación: coherencia 0, índice 1, pasa casi todo."""
    izq, der = _ruido(2), _ruido(3)
    indice = ambience.indice_ambiente(izq, der)
    assert float(np.median(indice)) > 0.5

    amb_izq, _ = ambience.extraer(izq, der)
    centro = slice(ambience.N_FFT, LARGO - ambience.N_FFT)
    assert _energia(amb_izq[centro]) > 0.5 * _energia(izq[centro])


def test_una_fuente_paneada_a_un_lado_no_es_ambiente():
    """El criterio adicional del paper.

    La coherencia de una fuente que solo está en un canal también es baja, pero es una
    fuente directa, no ambiente. Sin el criterio de energías comparables, esto se
    clasificaría mal y el "ambiente" traería el instrumento entero.
    """
    izq = _ruido(4)
    der = np.zeros(LARGO)
    indice = ambience.indice_ambiente(izq, der)
    assert float(np.median(indice)) < 0.05, "una fuente paneada se confundió con ambiente"


def test_sin_el_criterio_de_energia_la_fuente_paneada_si_se_confunde():
    """Deja constancia de por qué el criterio existe: al desactivarlo, el caso anterior
    falla. Si algún día se cambia el criterio, este test avisa que ya no hace falta."""
    izq = _ruido(4)
    der = np.zeros(LARGO)
    sin_criterio = ambience.Parametros(energia_minima=0.0)
    indice = ambience.indice_ambiente(izq, der, sin_criterio)
    assert float(np.median(indice)) > 0.5


def test_el_mapeo_respeta_su_rango():
    p = ambience.Parametros(mu0=0.0, mu1=1.0)
    valores = ambience.mapeo(np.linspace(0, 1, 101), p)
    assert valores.min() >= -1e-9
    assert valores.max() <= 1.0 + 1e-9
    # Monótona creciente: más ambiente, más ganancia.
    assert np.all(np.diff(valores) > 0)


def test_el_piso_del_mapeo_se_respeta():
    """`mu0` permite dejar pasar algo de lo coherente, en vez de borrarlo."""
    p = ambience.Parametros(mu0=0.3, mu1=1.0, sigma=8.0)
    valores = ambience.mapeo(np.linspace(0, 1, 101), p)
    assert valores.min() > 0.29


def test_la_pendiente_hace_la_curva_mas_abrupta():
    suave = ambience.mapeo(np.linspace(0, 1, 101), ambience.Parametros(sigma=1.0))
    abrupta = ambience.mapeo(np.linspace(0, 1, 101), ambience.Parametros(sigma=8.0))
    assert np.abs(np.diff(abrupta)).max() > np.abs(np.diff(suave)).max()


def test_rechaza_canales_de_distinto_largo():
    with pytest.raises(ValueError, match="largos distintos"):
        ambience.extraer(np.zeros(100), np.zeros(200))


def test_el_extractor_en_flujo_coincide_con_procesar_todo_de_una_vez():
    """La versión con estado tiene que dar lo mismo que la de una sola pasada.

    Es el test que justifica que exista: llamar a `extraer` por bloque no sirve, porque una
    STFT no se parte sin dejar los bordes sin reconstruir. Medido antes de escribir el
    extractor: bloques de 1024 muestras daban **642 %** de diferencia.
    """
    izq, der = _ruido(20), _ruido(21)
    de_una_vez = sum(ambience.extraer(izq, der)) / 2

    ext = ambience.Extractor()
    trozos = [ext.procesar(izq[i : i + 1024], der[i : i + 1024]) for i in range(0, LARGO, 1024)]
    en_flujo = np.concatenate(trozos)

    # El flujo sale retrasado la latencia del extractor; se compara alineando.
    lat = ext.latencia
    a = de_una_vez[: LARGO - lat]
    b = en_flujo[lat:]
    centro = slice(ambience.N_FFT, len(a) - ambience.N_FFT)
    error = np.sqrt(((a[centro] - b[centro]) ** 2).mean()) / (_energia(a[centro]) + 1e-12)
    assert error < 0.02, f"{error:.2%} de diferencia entre flujo y pasada única"


def test_el_extractor_declara_su_latencia_y_la_cumple():
    """Quien lo use tiene que retrasar los caminos paralelos lo mismo."""
    ext = ambience.Extractor()
    assert ext.latencia == ambience.N_FFT
    # Antes de acumular una trama entera no puede haber salida.
    primera = ext.procesar(_ruido(30, 512), _ruido(31, 512))
    assert np.allclose(primera, 0.0)


def test_el_extractor_se_reinicia():
    ext = ambience.Extractor()
    izq, der = _ruido(32, 8192), _ruido(33, 8192)
    a = ext.procesar(izq, der)
    ext.reiniciar()
    b = ext.procesar(izq, der)
    assert np.allclose(a, b)
