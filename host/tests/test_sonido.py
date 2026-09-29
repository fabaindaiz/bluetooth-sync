"""Comprueba la parte de la capa de audio que se puede probar sin PipeWire.

Lo que se prueba es el **parseo** de lo que devuelve `pw-dump` y la lectura de WAV. Lo que
reproduce y graba no se prueba acá: eso necesita parlantes, y su resultado está en
`docs/research/experimentos/`.
"""

import wave

import numpy as np
import pytest

from aurasync import sonido


def _nodo(nombre: str, descripcion: str = "", codec: str = "sbc", **extra) -> dict:
    props = {"node.name": nombre, "node.description": descripcion or nombre, **extra}
    if codec is not None:
        props["api.bluez5.codec"] = codec
    return {"info": {"props": props}}


def test_reconoce_los_parlantes_bluetooth():
    objetos = [
        _nodo("bluez_output.90_F2_60_DA_66_6D.1", "JBL Go 4 Black"),
        _nodo("alsa_output.pci-0000_30_00.6.analog-stereo", "placa de audio"),
        {"info": {}},
        {},
    ]
    salidas = sonido.leer_salidas(objetos)
    assert [s.descripcion for s in salidas] == ["JBL Go 4 Black"]


def test_descarta_los_nodos_internos_de_un_conjunto():
    """Un dispositivo LE Audio aparece con un nodo por stream isócrono más el visible.

    Sin este filtro, unos auriculares se contarían tres veces
    (`docs/research/experimentos/04-e8-unicast-le-audio-tune-770nc.md`).
    """
    objetos = [
        _nodo("bluez_output.88_92_CC_68_91_C0.1", "Tune (interno)", **{"api.bluez5.internal": True}),
        _nodo("bluez_output.88_92_CC_68_91_C0.3", "Tune (interno)", **{"api.bluez5.internal": True}),
        _nodo("bluez_output.88_92_CC_68_91_C0.257", "JBL Tune 770NC"),
    ]
    salidas = sonido.leer_salidas(objetos)
    assert [s.descripcion for s in salidas] == ["JBL Tune 770NC"]


def test_deduce_la_direccion_del_nombre_del_nodo():
    salida = sonido.leer_salidas([_nodo("bluez_output.90_F2_60_DA_66_6D.1")])[0]
    assert salida.direccion == "90:F2:60:DA:66:6D"


def test_detecta_codecs_mezclados():
    """Es lo que dispara 45 a 150 ms de desfase entre parlantes."""
    iguales = sonido.leer_salidas([_nodo("bluez_output.A.1"), _nodo("bluez_output.B.1")])
    distintos = sonido.leer_salidas([_nodo("bluez_output.A.1", codec="sbc"), _nodo("bluez_output.B.1", codec="aac")])
    assert not sonido.codecs_mezclados(iguales)
    assert sonido.codecs_mezclados(distintos)


def test_una_salida_sin_codec_no_rompe():
    salidas = sonido.leer_salidas([_nodo("bluez_output.A.1", codec=None)])
    assert salidas[0].codec == "?"


def test_el_reproductor_exige_al_menos_un_parlante():
    with pytest.raises(ValueError, match="ningún parlante"):
        sonido.Reproductor([])


def _escribir_wav(ruta, datos, canales, sr=48000, ancho=2):
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(canales)
        w.setsampwidth(ancho)
        w.setframerate(sr)
        w.writeframes(datos.astype("<i2").tobytes())


def test_lee_un_wav_estereo(tmp_path):
    ruta = tmp_path / "e.wav"
    inter = np.array([1000, -1000, 2000, -2000], dtype="<i2")
    _escribir_wav(ruta, inter, canales=2)
    izq, der, sr = sonido.leer_wav_estereo(ruta)
    assert sr == 48000
    assert np.allclose(izq, [1000 / 32768, 2000 / 32768])
    assert np.allclose(der, [-1000 / 32768, -2000 / 32768])


def test_lee_un_wav_mono_duplicando(tmp_path):
    ruta = tmp_path / "m.wav"
    _escribir_wav(ruta, np.array([100, 200, 300], dtype="<i2"), canales=1)
    izq, der, _ = sonido.leer_wav_estereo(ruta)
    assert np.allclose(izq, der)
    assert len(izq) == 3


def test_rechaza_un_wav_que_no_sea_de_16_bits(tmp_path):
    ruta = tmp_path / "x.wav"
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(1)
        w.setframerate(48000)
        w.writeframes(b"\x00\x01\x02\x03")
    with pytest.raises(ValueError, match="16 bits"):
        sonido.leer_wav_estereo(ruta)


def test_lee_un_wav_mono_como_mono(tmp_path):
    ruta = tmp_path / "g.wav"
    _escribir_wav(ruta, np.array([16384, -16384], dtype="<i2"), canales=1)
    assert np.allclose(sonido.leer_wav_mono(ruta), [0.5, -0.5])


def test_el_sink_virtual_sin_arrancar_no_devuelve_datos():
    """Antes de entrar al contexto no hay proceso, y `leer` tiene que decirlo sin romper."""
    sv = sonido.SinkVirtual()
    assert sv.leer(1024) is None
    sv.cerrar()  # cerrar sin haber abierto tampoco debe romper


def test_el_sink_virtual_arma_bien_su_nombre_y_descripcion():
    sv = sonido.SinkVirtual("mi_salida", "Mi salida")
    assert sv.nombre == "mi_salida"
    assert sv.descripcion == "Mi salida"
