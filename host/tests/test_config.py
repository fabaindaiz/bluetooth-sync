"""Comprueba el modelo de la instalación.

Lo que más importa acá es la aritmética acústica: si el retardo por distancia está mal, la
calibración corrige contra un número equivocado y el resultado empeora en vez de mejorar.
"""

import math

import pytest

from aurasync.config import Instalacion, Parlante


def _tres() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante("izq", "sink_a", x=-2.0, y=0.0),
            Parlante("der", "sink_b", x=2.0, y=0.0),
            Parlante("atras", "sink_c", x=0.0, y=-3.0),
        ]
    )


def test_la_distancia_y_el_retardo_acustico_coinciden():
    p = Parlante("uno", "sink", x=3.43, y=0.0)
    assert math.isclose(p.distancia_a(0, 0), 3.43)
    # 3,43 m a 343 m/s son exactamente 10 ms.
    assert math.isclose(p.retardo_acustico_ms(0, 0), 10.0, rel_tol=1e-9)


def test_treinta_y_cuatro_centimetros_son_un_milisegundo():
    """La regla que se usa en todo el proyecto para leer las mediciones."""
    cerca = Parlante("cerca", "a", x=1.00, y=0.0)
    lejos = Parlante("lejos", "b", x=1.343, y=0.0)
    diferencia = lejos.retardo_acustico_ms() - cerca.retardo_acustico_ms()
    assert math.isclose(diferencia, 1.0, abs_tol=0.01)


def test_alinear_por_geometria_iguala_los_tiempos_de_llegada():
    inst = _tres()
    inst.alinear_por_geometria()
    acusticos = inst.retardos_acusticos_ms()
    llegadas = [p.retardo_ms + acusticos[p.nombre] for p in inst.parlantes]
    assert max(llegadas) - min(llegadas) < 1e-9


def test_al_mas_lejano_no_se_le_agrega_retardo():
    """Agregar retardo al más lejano solo sumaría latencia a todo el sistema."""
    inst = _tres()
    inst.alinear_por_geometria()
    acusticos = inst.retardos_acusticos_ms()
    mas_lejano = max(acusticos, key=lambda n: acusticos[n])
    assert inst.por_nombre(mas_lejano).retardo_ms == pytest.approx(0.0)
    assert all(p.retardo_ms >= 0 for p in inst.parlantes)


def test_el_retardo_de_los_traseros_esta_en_el_rango_del_paper():
    """Avendaño y Jot: de 5 a 20 ms."""
    assert 5.0 <= Instalacion().retardo_traseros_ms <= 20.0


def test_no_deja_nombres_repetidos():
    with pytest.raises(ValueError, match="nombres"):
        Instalacion(parlantes=[Parlante("a", "s1"), Parlante("a", "s2")])


def test_no_deja_sinks_repetidos():
    """Dos parlantes apuntando al mismo sink sería un error silencioso: uno se quedaría
    sin sonar y la medición lo tomaría como parlante mudo."""
    with pytest.raises(ValueError, match="sinks"):
        Instalacion(parlantes=[Parlante("a", "s"), Parlante("b", "s")])


def test_por_nombre_explica_que_hay_cuando_falla():
    inst = _tres()
    with pytest.raises(KeyError, match="izq"):
        inst.por_nombre("no existe")


def test_ida_y_vuelta_a_disco(tmp_path):
    inst = _tres()
    inst.oyente_x, inst.oyente_y = 0.5, -0.5
    inst.alinear_por_geometria()
    ruta = tmp_path / "sub" / "instalacion.json"
    inst.guardar(ruta)

    leida = Instalacion.cargar(ruta)
    assert leida == inst
