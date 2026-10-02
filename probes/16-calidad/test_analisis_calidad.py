"""Tests del análisis de `probes/16-calidad/`, con señales sintéticas de respuesta conocida.

    cd host && "$(hatch env find hatch-test)/bin/python" -m pytest ../probes/16-calidad -q

El módulo se carga por ruta con un nombre propio: `probes/15-…` también tiene un `analisis.py`.
"""

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

AQUI = Path(__file__).resolve().parent


def _cargar(nombre: str, archivo: str):
    spec = importlib.util.spec_from_file_location(nombre, AQUI / archivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


A = _cargar("analisis_calidad", "analisis.py")
S = _cargar("sistema_calidad", "sistema.py")
SR = 48000
RNG = np.random.default_rng(7)


def seno(f, amplitud, segundos, fase=0.0):
    t = np.arange(int(SR * segundos)) / SR
    return amplitud * np.sin(2 * np.pi * f * t + fase)


def rosa(n, semilla):
    return S.ruido_rosa(n, semilla) * 0.1


def retrasar(x, muestras):
    """Retardo con fracción, exacto (en frecuencia, con relleno para que no dé la vuelta)."""
    n = len(x) + 4096
    f = np.fft.rfftfreq(n)
    return np.fft.irfft(np.fft.rfft(x, n) * np.exp(-2j * np.pi * f * muestras), n)[: len(x)]


def remuestrear(x, paso, medio=32):
    """x leído en las posiciones n·paso, con sinc de Kaiser de 2·medio coeficientes."""
    pos = np.arange(len(x)) * paso
    out = np.zeros(len(x))
    for i in range(0, len(x), 48000):
        p = pos[i : i + 48000]
        base = np.floor(p).astype(int)
        k = base[:, None] + np.arange(-medio + 1, medio + 1)[None, :]
        t = p[:, None] - k
        ventana = np.kaiser(2 * medio + 1, 8.0)[np.clip(np.round(t + medio).astype(int), 0, 2 * medio)]
        validos = (k >= 0) & (k < len(x))
        out[i : i + 48000] = np.sum(np.where(validos, x[np.clip(k, 0, len(x) - 1)], 0) * np.sinc(t) * ventana, axis=1)
    return out


def filtro_fir(x, taps):
    return np.convolve(x, taps)[: len(x)]


# -- bandas ---------------------------------------------------------------------------------


def test_un_seno_cae_en_su_banda_con_su_nivel():
    x = seno(1000, 0.1, 2)
    niveles = A.niveles_por_banda(x)
    k = int(np.argmin(np.abs(A.TERCIOS - 1000)))
    assert niveles[k] == pytest.approx(20 * math.log10(0.1) - 3.0103, abs=0.05)
    # Las bandas lejanas quedan muy abajo (fuga de la ventana de Hann).
    assert niveles[k + 3] < niveles[k] - 60


def test_el_total_es_el_cuadratico_medio():
    x = rosa(SR * 4, 1)
    assert A.nivel_total(x) == pytest.approx(10 * math.log10(np.mean(x**2)), abs=0.1)


def test_los_centros_de_sexto_de_octava_cubren_la_banda():
    cs = A.centros(6, 100, 8000)
    assert cs[0] >= 100 and cs[-1] <= 8000
    assert np.allclose(cs[1:] / cs[:-1], 2 ** (1 / 6))
    assert len(cs) == 38


# -- ubicar ---------------------------------------------------------------------------------


def test_ubicar_encuentra_un_retardo_con_fraccion():
    ref = rosa(SR, 2)
    grabacion = np.concatenate([np.zeros(5000), ref, np.zeros(5000)])
    grabacion = retrasar(grabacion, 0.3) + 1e-4 * RNG.standard_normal(len(grabacion))
    donde, nitidez = A.ubicar(grabacion, ref)
    assert donde == pytest.approx(5000.3, abs=0.1)
    assert nitidez > 10


def test_ubicar_respeta_la_ventana_de_busqueda():
    ref = rosa(SR // 2, 3)
    grabacion = np.concatenate([np.zeros(1000), ref, np.zeros(20000), ref, np.zeros(1000)])
    segunda = 1000 + len(ref) + 20000
    donde, _ = A.ubicar(grabacion, ref, segunda - 500, segunda + 500)
    assert round(donde) == segunda


# -- suma_go4 -------------------------------------------------------------------------------


def _parlante(izq, der, modo):
    if modo == "suma":
        return 0.5 * (izq + der)
    if modo == "izquierdo":
        return izq
    raise ValueError(modo)


@pytest.mark.parametrize(("modo", "esperado"), [("suma", "suma"), ("izquierdo", "elige_L")])
def test_decidir_suma_con_un_parlante_sintetico(modo, esperado):
    tono = seno(1000, 0.1, 2)
    casos = {"L": (tono, 0 * tono), "R": (0 * tono, tono), "LR": (tono, tono), "LmR": (tono, -tono)}
    ruido = 1e-5 * RNG.standard_normal(len(tono))
    niveles = {k: A.nivel_tono(_parlante(i, d, modo) + ruido, 1000) for k, (i, d) in casos.items()}
    piso = A.nivel_tono(ruido, 1000)
    r = A.decidir_suma(niveles, piso)
    assert r["veredicto"] == esperado
    if modo == "suma":
        assert r["L_menos_LR_db"] == pytest.approx(-6.02, abs=0.1)


def test_decidir_suma_no_concluye_con_el_piso_alto():
    niveles = {"L": -30.0, "R": -30.0, "LR": -24.0, "LmR": -40.0}
    assert A.decidir_suma(niveles, piso_db=-50.0)["veredicto"] == "inconcluso"
    # Con el piso bien abajo, los mismos niveles no son suma (−16 dB no llega a −30).
    assert A.decidir_suma(niveles, piso_db=-90.0)["veredicto"] == "otro"


# -- directo contra motor --------------------------------------------------------------------


def test_comparar_pasadas_encuentra_un_realce_conocido():
    musica = rosa(SR * 8, 4)
    # El "motor" realza +1,5 dB en el sexto de 1 kHz (un FIR de pico construido en frecuencia).
    n = len(musica)
    f = np.fft.rfftfreq(n, 1 / SR)
    lo, hi = 1000 * 2 ** (-1 / 12), 1000 * 2 ** (1 / 12)
    ganancia = np.where((f >= lo) & (f < hi), 10 ** (1.5 / 20), 1.0)
    motor = np.fft.irfft(np.fft.rfft(musica) * ganancia, n)
    ruido = lambda: 1e-5 * RNG.standard_normal(n)  # noqa: E731
    r = A.comparar_pasadas(musica + ruido(), motor + ruido(), musica + ruido())
    k = int(np.argmin(np.abs(r["centros_hz"] - 1000)))
    assert r["delta_bandas_db"]["8192"][k] == pytest.approx(1.5, abs=0.25)
    assert abs(r["repeticion_lufs"]) < 0.01
    assert r["peor_delta_banda_db"] == pytest.approx(1.5, abs=0.25)
    veredicto = A.criterio_directo_motor(r)
    assert veredicto["medible"] and not veredicto["cumple"]


def test_comparar_pasadas_igual_cumple_y_una_ganancia_se_ve_en_lufs():
    musica = rosa(SR * 8, 5)
    r = A.comparar_pasadas(musica, musica * 10 ** (-0.3 / 20), musica)
    assert r["delta_lufs"] == pytest.approx(-0.3, abs=0.02)
    assert A.criterio_directo_motor(r)["cumple"]


def test_una_medicion_que_no_se_repite_no_es_medible():
    musica = rosa(SR * 8, 6)
    r = A.comparar_pasadas(musica, musica, musica * 10 ** (1.0 / 20))
    assert not A.criterio_directo_motor(r)["medible"]


# -- respuesta de varios parlantes a la vez -------------------------------------------------


def _sala_de_tres(snr_db=40.0, deriva_ppm=0.0, segundos=12):
    """Tres ruidos independientes por tres filtros conocidos, con retardos distintos (uno con
    fracción) y ruido de fondo."""
    n = SR * segundos
    refs = [rosa(n, 10 + i) for i in range(3)]
    taps = [
        np.array([1.0]),
        np.array([0.5]),
        np.array([0.25, 0.25]),  # un pasa-bajos suave: −6 dB a ~SR/4 respecto de continua
    ]
    retardos = [24000.0, 24480.0, 25000.4]
    y = np.zeros(n + 30000)
    for r, t, d in zip(refs, taps, retardos, strict=True):
        largo = len(y)
        sonido = filtro_fir(np.concatenate([r, np.zeros(largo - n)]), t)
        if deriva_ppm:
            # El reloj del micrófono corre `deriva_ppm` más rápido: remuestreo con sinc (una
            # interpolación lineal ensuciaría los agudos por su cuenta).
            sonido = remuestrear(sonido, 1 - deriva_ppm * 1e-6)
        y += retrasar(sonido, d)
    potencia = np.mean(y[24000 : 24000 + n] ** 2)
    y += np.sqrt(potencia * 10 ** (-snr_db / 10)) * RNG.standard_normal(len(y))
    return y, refs, taps, retardos


def _verdad_por_tercio(taps, cs):
    f = np.fft.rfftfreq(16384, 1 / SR)
    h = np.abs(np.fft.rfft(taps, 16384)) ** 2
    return A.db(A.promediar_por_banda(f, h, cs, 3))


def test_respuesta_multiple_recupera_cada_filtro_y_la_coherencia_comun_no_sirve():
    y, refs, taps, retardos = _sala_de_tres()
    r = A.respuesta_multiple(y, refs, retardos)
    tercios = A.respuesta_por_tercio(r)
    banda = (A.TERCIOS >= 100) & (A.TERCIOS <= 8000)
    for i, t in enumerate(taps):
        verdad = _verdad_por_tercio(t, A.TERCIOS)
        medido = tercios["parlantes"][i]["db"]
        assert np.max(np.abs(medido[banda] - verdad[banda])) < 0.3, i
        assert np.min(tercios["parlantes"][i]["coherencia"][banda]) > 0.95
    # La coherencia común del más débil queda muy por debajo de 0,9 aunque la medición es buena.
    comun = A.promediar_por_banda(r["f"], r["coherencia_comun"][2], A.TERCIOS, 3)
    assert np.nanmax(comun[banda]) < 0.5


def test_respuesta_multiple_baja_la_coherencia_con_ruido():
    y, refs, _, retardos = _sala_de_tres(snr_db=0.0)
    r = A.respuesta_por_tercio(A.respuesta_multiple(y, refs, retardos))
    banda = (A.TERCIOS >= 200) & (A.TERCIOS <= 4000)
    assert np.nanmax(r["parlantes"][2]["coherencia"][banda]) < 0.9


def test_la_deriva_se_estima_y_sin_corregirla_se_pierde_la_coherencia_en_agudos():
    y, refs, taps, retardos = _sala_de_tres(deriva_ppm=40.0, segundos=16)
    estimado = [A.desfase_y_deriva(y, ref) for ref in refs]
    # El FIR de dos coeficientes del tercero atrasa media muestra (fase lineal).
    for e, d, t in zip(estimado, retardos, taps, strict=True):
        assert e["desfase"] == pytest.approx(d + (len(t) - 1) / 2, abs=0.1)
        assert e["deriva"] == pytest.approx(40e-6, rel=0.01)
    alto = (A.TERCIOS >= 4000) & (A.TERCIOS <= 8000)
    con = A.respuesta_por_tercio(
        A.respuesta_multiple(y, refs, [e["desfase"] for e in estimado], [e["deriva"] for e in estimado])
    )
    sin = A.respuesta_por_tercio(A.respuesta_multiple(y, refs, [e["desfase"] for e in estimado]))
    assert np.min(con["parlantes"][0]["coherencia"][alto]) > 0.9
    assert np.min(sin["parlantes"][0]["coherencia"][alto]) < np.min(con["parlantes"][0]["coherencia"][alto]) - 0.1


def test_estabilidad_entre_segmentos_y_colocaciones():
    y, refs, _, retardos = _sala_de_tres()
    por_segmento = {
        s: A.respuesta_por_tercio(A.respuesta_multiple(y, refs, retardos, segmento=s)) for s in (8192, 16384, 32768)
    }
    e = A.estabilidad_entre_segmentos(por_segmento, 2)
    assert e["ok"] and e["tercios_validos"] >= 15
    base = por_segmento[16384]["parlantes"][0]
    otra = base["normalizada_db"].copy()
    k = int(np.argmin(np.abs(A.TERCIOS - 2000)))
    otra[k] += 4.0  # una colocación con un pozo de sala de 4 dB
    r = A.comparar_colocaciones([base["normalizada_db"], otra], [base["coherencia"]] * 2)
    assert not r["ok"] and r["peor_tercio_hz"] == pytest.approx(A.TERCIOS[k])
    assert A.comparar_colocaciones([base["normalizada_db"]] * 3, [base["coherencia"]] * 3)["ok"]


# -- WAV y pw-dump --------------------------------------------------------------------------


def test_wav_float_ida_y_vuelta(tmp_path):
    x = np.stack([seno(440, 0.5, 0.1), seno(660, 0.25, 0.1)], axis=1)
    y, sr = S.leer_wav(S.escribir_wav(tmp_path / "x.wav", x))
    assert sr == SR and y.shape == x.shape
    assert np.max(np.abs(y - x)) < 1e-6


def test_wav_pcm16_y_24(tmp_path):
    import wave

    x = (seno(440, 0.5, 0.05) * 32767).astype("<i2")
    with wave.open(str(tmp_path / "a.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR), w.writeframes(x.tobytes())
    y, _ = S.leer_wav(tmp_path / "a.wav")
    assert np.max(np.abs(y[:, 0] - x / 32768)) < 1e-9
    v = np.round(seno(440, 0.5, 0.05) * 8388607).astype(np.int32)
    crudo = np.stack([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF], axis=1).astype(np.uint8).tobytes()
    with wave.open(str(tmp_path / "b.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(3), w.setframerate(SR), w.writeframes(crudo)
    y, _ = S.leer_wav(tmp_path / "b.wav")
    assert np.max(np.abs(y[:, 0] - v / 8388608)) < 1e-9


def _pw_dump():
    """Un grafo mínimo: pw-play (pid 100) enlazado a un Go 4, otro stream (pid 200) al mismo
    sink, y pw-record (pid 300) grabando del fifine."""
    nodo = lambda i, **p: {"id": i, "type": "PipeWire:Interface:Node", "info": {"props": p}}  # noqa: E731
    cliente = lambda i, pid: {
        "id": i,
        "type": "PipeWire:Interface:Client",
        "info": {"props": {"application.process.id": pid}},
    }  # noqa: E731
    enlace = lambda i, a, b: {
        "id": i,
        "type": "PipeWire:Interface:Link",
        "info": {"props": {"link.output.node": a, "link.input.node": b}},
    }  # noqa: E731
    return [
        cliente(1, 100),
        cliente(2, 200),
        cliente(3, 300),
        nodo(10, **{"node.name": "bluez_output.90_F2_60_75_4A_83.1", "node.description": "JBL Go 4 Red"}),
        nodo(11, **{"node.name": "alsa_input.usb-fifine", "media.class": "Audio/Source"}),
        nodo(20, **{"client.id": 1, "application.name": "pw-play"}),
        nodo(21, **{"client.id": 2, "application.name": "Spotify"}),
        nodo(22, **{"client.id": 3, "application.name": "pw-record"}),
        enlace(30, 20, 10),
        enlace(31, 21, 10),
        enlace(32, 11, 22),
    ]


def test_pw_dump_dice_donde_quedo_cada_proceso():
    objetos = json.loads(json.dumps(_pw_dump()))
    assert S.destinos_de(objetos, 100) == ["bluez_output.90_F2_60_75_4A_83.1"]
    assert S.origenes_de(objetos, 300) == ["alsa_input.usb-fifine"]
    assert S.destinos_de(objetos, 999) == []
    assert S.quienes_alimentan(objetos, "bluez_output.90_F2_60_75_4A_83.1", salvo_pid=100) == ["Spotify (pid 200)"]
    assert S.elegir_sinks(objetos, ["90:F2:60:75:4A:83"]) == {"bluez_output.90_F2_60_75_4A_83.1": "JBL Go 4 Red"}
    assert S.elegir_sinks(objetos, ["red"]) == {"bluez_output.90_F2_60_75_4A_83.1": "JBL Go 4 Red"}
    candidatos = [
        ("bluez_output.90_F2_60_75_4A_83.1", "JBL Go 4 Red"),
        ("bluez_output.90_F2_60_E3_07_39.1", "JBL Go 4 Blue"),
    ]
    # "blue" está en todos los nodos bluez_output: cuenta solo el nombre.
    assert S.coincidencias("Blue", candidatos) == ["bluez_output.90_F2_60_E3_07_39.1"]
    assert S.coincidencias("90_F2_60_75_4A_83", candidatos) == ["bluez_output.90_F2_60_75_4A_83.1"]
    assert S.coincidencias("Go 4", candidatos) == [n for n, _ in candidatos]


def test_el_volumen_se_lee_de_vuelta():
    class Falso:
        def __init__(self, toma):
            self.toma, self.valor = toma, 50.0

        def set_percent(self, _sink, pct):
            if self.toma:
                self.valor = pct
            return True

        def get_percent(self, _sink):
            return self.valor

    v = S.VolumenParlante(Falso(toma=True))
    v.espera = 0
    assert v.poner("s", 80.0) == 80.0
    v = S.VolumenParlante(Falso(toma=False))
    v.espera = 0
    with pytest.raises(S.RuteoIncorrecto):
        v.poner("s", 80.0)


def test_el_restaurador_anota_antes_y_deshace_en_orden_inverso(tmp_path):
    archivo = tmp_path / "cambios.txt"
    hechos = []
    with pytest.raises(KeyboardInterrupt), S.Restaurador(archivo) as r:
        r.cambio("uno", "deshacer uno", lambda: hechos.append(1))
        assert "CAMBIO: uno" in archivo.read_text()
        r.cambio("dos", "deshacer dos", lambda: hechos.append(2))
        raise KeyboardInterrupt
    assert hechos == [2, 1]
    assert "revertido" in archivo.read_text()


# -- el análisis de suma_go4.py sobre una grabación sintética completa ------------------------


def _suma_go4():
    sys.modules["analisis"], sys.modules["sistema"] = A, S  # lo que el script importa por nombre
    return _cargar("suma_go4_calidad", "suma_go4.py")


@pytest.mark.parametrize(("modo", "esperado"), [("suma", "suma"), ("derecho", "elige_R"), ("nada", "otro")])
def test_suma_go4_de_punta_a_punta(modo, esperado):
    G = _suma_go4()
    x, plan = G.estimulo(1000.0, 0.1)
    izq, der = x[:, 0], x[:, 1]
    parlante = {"suma": 0.5 * (izq + der), "derecho": der, "nada": izq + 0.3 * der}[modo]
    sala = np.zeros(1200)
    sala[[0, 400, 1100]] = [1.0, 0.3, -0.2]
    latencia = int(0.37 * SR)
    grabacion = np.concatenate([np.zeros(latencia), np.convolve(parlante, sala), np.zeros(SR)])
    grabacion += 1e-5 * RNG.standard_normal(len(grabacion))
    r = G.analizar(grabacion, x, plan, 1000.0)
    assert r["decision"]["veredicto"] == esperado
    assert r["desfase_ms"] == pytest.approx(370, abs=0.1)
    # Lo que suena se repite entre vueltas; lo que no suena es piso de ruido y varía más.
    assert r["dispersion_entre_vueltas_db"]["LR"] < 0.05
    assert r["repetible"]


def test_suma_go4_si_las_vueltas_no_se_repiten_no_concluye():
    G = _suma_go4()
    x, plan = G.estimulo(1000.0, 0.1)
    parlante = 0.5 * (x[:, 0] + x[:, 1])
    segunda = next(p for p in plan if p["tramo"] == "LR#2")
    i = int(segunda["inicio_s"] * SR)
    parlante[i : i + int(segunda["dur_s"] * SR)] *= 10 ** (3 / 20)  # el parlante subió 3 dB a mitad
    grabacion = np.concatenate([np.zeros(SR // 4), parlante, np.zeros(SR)]) + 1e-5 * RNG.standard_normal(
        len(parlante) + SR + SR // 4
    )
    assert G.analizar(grabacion, x, plan, 1000.0)["decision"]["veredicto"] == "inconcluso"


def test_suma_go4_sin_parlante_no_inventa():
    G = _suma_go4()
    x, plan = G.estimulo(1000.0, 0.1)
    with pytest.raises(RuntimeError, match="marcador"):
        G.analizar(1e-4 * RNG.standard_normal(len(x) + SR), x, plan, 1000.0)
