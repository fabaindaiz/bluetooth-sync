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


# -- el anillo del micrófono continuo ---------------------------------------------------
# Se prueba el anillo, no la captura: la captura necesita PipeWire y un micrófono, y su
# resultado va a `docs/research/experimentos/`. Se entra por `agregar`, que es la costura
# que permite probar la lógica sin proceso.


def test_el_anillo_del_microfono_devuelve_lo_ultimo_en_orden():
    m = sonido.MicrofonoContinuo("mic", sr=10, segundos=1.0)  # 10 muestras
    m.agregar(np.arange(1.0, 8.0))  # 7 muestras: no completó la vuelta
    assert not m.lleno
    assert m.ultimos(0.5) is not None
    assert np.allclose(m.ultimos(0.5), [3, 4, 5, 6, 7])


def test_el_anillo_del_microfono_sigue_en_orden_despues_de_dar_la_vuelta():
    m = sonido.MicrofonoContinuo("mic", sr=10, segundos=1.0)
    m.agregar(np.arange(1.0, 9.0))  # 8
    m.agregar(np.arange(9.0, 16.0))  # 7 más: pasa el límite y sobreescribe
    assert m.lleno
    assert np.allclose(m.ultimos(1.0), np.arange(6.0, 16.0))


def test_el_anillo_del_microfono_no_devuelve_mas_de_lo_que_tiene():
    m = sonido.MicrofonoContinuo("mic", sr=10, segundos=1.0)
    m.agregar(np.arange(1.0, 4.0))
    assert m.ultimos(1.0) is None  # todavía no hay un segundo
    assert m.ultimos(2.0) is None  # y nunca va a haber dos: el anillo mide uno


def test_un_bloque_mas_grande_que_el_anillo_conserva_la_cola():
    m = sonido.MicrofonoContinuo("mic", sr=10, segundos=1.0)
    m.agregar(np.arange(1.0, 26.0))  # 25 muestras en un anillo de 10
    assert np.allclose(m.ultimos(1.0), np.arange(16.0, 26.0))


def test_bombear_sin_proceso_no_rompe():
    m = sonido.MicrofonoContinuo("mic")
    assert m.bombear() == 0
    m.cerrar()


# -- las entradas de audio, que `doctor` muestra ---------------------------------------


def _fuente(nombre: str, descripcion: str = "") -> dict:
    return {
        "info": {
            "props": {"node.name": nombre, "node.description": descripcion or nombre, "media.class": "Audio/Source"}
        }
    }


def test_reconoce_las_fuentes_de_audio():
    objetos = [
        _fuente("alsa_input.usb-3142_fifine_Microphone-00.analog-stereo", "fifine Microphone"),
        _nodo("bluez_output.90_F2_60_DA_66_6D.1", "JBL Go 4"),
        {"info": {"props": {"node.name": "x", "media.class": "Audio/Sink"}}},
    ]
    assert [e.descripcion for e in sonido.leer_entradas(objetos)] == ["fifine Microphone"]


def test_distingue_un_monitor_de_un_microfono():
    """Un monitor devuelve la señal sin pasar por el aire: no mide nada de la sala."""
    entradas = sonido.leer_entradas(
        [_fuente("alsa_output.pci-0000_30_00.6.analog-stereo.monitor"), _fuente("alsa_input.usb-3142_fifine")]
    )
    # `leer_entradas` ordena por nombre de nodo, así que el input queda primero.
    assert {e.nodo: e.es_monitor for e in entradas} == {
        "alsa_input.usb-3142_fifine": False,
        "alsa_output.pci-0000_30_00.6.analog-stereo.monitor": True,
    }
