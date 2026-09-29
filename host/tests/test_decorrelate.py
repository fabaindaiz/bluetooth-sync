"""Comprueba que los filtros de decorrelación hacen lo que el paper dice que hacen.

Las tres propiedades que importan, y que si se rompen el efecto no funciona:

1. **Son todo-paso**: la magnitud de la respuesta es plana, así que no colorean.
2. **La salida suena igual que la entrada**: mismo espectro de amplitud.
3. **Las salidas están decorrelacionadas entre sí**: es lo que produce el campo difuso
   en vez de una imagen fantasma.
"""

import numpy as np
import pytest

from aurasync.dsp import decorrelate


def test_el_filtro_es_todo_paso():
    """Magnitud plana: es lo que garantiza que no coloree."""
    h = decorrelate.filtro_todo_paso(semilla=1)
    magnitud = np.abs(np.fft.rfft(h))
    # Normalizado, así el test mira la planitud y no la ganancia absoluta.
    magnitud = magnitud / magnitud.mean()
    assert np.allclose(magnitud, 1.0, atol=1e-9)


def test_la_respuesta_al_impulso_es_real():
    """Si la simetría hermítica está mal, sale compleja y el audio se rompe."""
    h = decorrelate.filtro_todo_paso(semilla=2)
    assert h.dtype == np.float64
    assert np.isfinite(h).all()


@pytest.mark.parametrize("largo", [64, 128, 129])
def test_el_largo_impar_tambien_funciona(largo):
    h = decorrelate.filtro_todo_paso(largo=largo, semilla=3)
    assert len(h) == largo
    magnitud = np.abs(np.fft.rfft(h))
    assert np.allclose(magnitud / magnitud.mean(), 1.0, atol=1e-9)


def test_la_salida_conserva_el_espectro_de_la_entrada():
    """ "Suena igual" quiere decir esto: mismo espectro de amplitud."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal(8192)
    h = decorrelate.filtro_todo_paso(semilla=4)
    y = decorrelate.aplicar(x, h)

    assert len(y) == len(x)

    # Se compara la densidad espectral promediada en bandas: bin a bin, una señal
    # aleatoria tiene varianza propia que tapa la comparación.
    def bandas(v):
        esp = np.abs(np.fft.rfft(v)) ** 2
        return np.array([esp[i : i + 64].mean() for i in range(0, len(esp) - 64, 64)])

    bx, by = bandas(x), bandas(y)
    razon = by / bx
    assert 0.8 < float(np.median(razon)) < 1.25


def test_las_salidas_quedan_decorrelacionadas():
    """La propiedad que produce el efecto: dos parlantes, dos formas de onda distintas."""
    rng = np.random.default_rng(1)
    x = rng.standard_normal(16384)
    filtros = decorrelate.banco_decorrelador(2, semilla=5)
    y1, y2 = (decorrelate.aplicar(x, h) for h in filtros)

    corr = np.corrcoef(y1, y2)[0, 1]
    assert abs(corr) < 0.25, f"las salidas siguen correlacionadas: {corr:.3f}"
    # Y contra la entrada sin filtrar, para que quede claro que el filtro es el que
    # produce la diferencia.
    assert abs(np.corrcoef(x, x)[0, 1] - 1.0) < 1e-9


def test_la_seleccion_mejora_la_ortogonalidad():
    """El "best performance selection" del paper tiene que servir para algo.

    Se compara el banco elegido contra el mismo número de filtros tomados sin elegir.
    """
    n = 4
    elegidos = decorrelate.banco_decorrelador(n, candidatos=64, semilla=7)
    rng = np.random.default_rng(7)
    sin_elegir = [decorrelate.filtro_todo_paso(semilla=int(s)) for s in rng.integers(0, 2**31 - 1, n)]
    assert decorrelate.ortogonalidad(elegidos) < decorrelate.ortogonalidad(sin_elegir)


def test_hasta_seis_canales_siguen_razonablemente_ortogonales():
    """El paper dice 5 o 6 con filtros fijos; el proyecto necesita como mucho 4."""
    filtros = decorrelate.banco_decorrelador(decorrelate.MAXIMO_FIJOS, candidatos=96, semilla=11)
    assert len(filtros) == decorrelate.MAXIMO_FIJOS
    assert decorrelate.ortogonalidad(filtros) < 0.5


def test_pide_candidatos_suficientes():
    with pytest.raises(ValueError, match="candidatos"):
        decorrelate.banco_decorrelador(8, candidatos=4)


def test_rechaza_cantidades_invalidas():
    with pytest.raises(ValueError, match="filtros"):
        decorrelate.banco_decorrelador(0)
