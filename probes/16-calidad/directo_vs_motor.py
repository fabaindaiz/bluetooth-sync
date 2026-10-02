"""Directo contra motor, con la sonoridad igualada (experimentos/15 §B, roadmap i-7c8794-10ccb4).

El mismo pasaje de una canción, grabado en el punto de escucha por dos caminos:

- **a · directo:** la mezcla de cada parlante (su pan de L y R, su ganancia y su retardo de
  calibración, nada más) escrita por `pw-play` al sink combinado `aurasync_salida`, un canal por
  parlante (`AUX0…`). Es "la música sin el motor" con los mismos parlantes, la misma alineación
  y el mismo reloj.
- **b · motor:** la canción en estéreo al sink `aurasync`, por la cadena **por defecto**.

**La sonoridad se iguala en digital antes de sonar:** el motor se corre fuera de línea sobre el
pasaje con los valores vivos del servicio (instalación, cadena, volumen), y la ganancia del
directo es la diferencia de sonoridad integrada (`aurasync.dsp.loudness`, la suma de las
salidas con G = 1). Queda dentro de ±0,2 LU o el script no sigue.

Orden por canción **a, b, a**: las dos pasadas del directo dicen cuánto varía la medición sola
(si varía más que la mitad de la tolerancia, el Δ no se lee). El análisis se repite con dos
tamaños de segmento (8192 y 32768): un Δ verdadero no depende de eso (CLAUDE.md).

Criterio (spec §8): |ΔLUFS| ≤ 0,5 LU y ≤ 1 dB por sexto de octava de 100 Hz a 8 kHz, en 3
canciones × 2 sesiones (`comparar.py`).

Comprueba antes de tocar nada: herramientas, servicio sonando con salida combinada, cadena por
defecto, ningún parlante en silencio, volumen del panel ≤ −14 dB, nada entrando a `aurasync`.
Apaga el lazo de recalibración mientras mide (movería retardos entre pasadas) y lo vuelve a
encender al final, también con Ctrl-C. `--ensayo`: solo el servicio y el cálculo de las
ganancias, sin sonar ni grabar (lo que se prueba en el Mac con `aurasync service --simular`).

Uso (PC-Ryzen5):

    $PY probes/16-calidad/directo_vs_motor.py cancion1.wav cancion2.wav cancion3.wav \\
        --desde 30 --segundos 40 --sesion 1 --microfono-en "punto de escucha, 1,2 m"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import analisis as A  # noqa: E402
import servicio as V  # noqa: E402
import sistema as S  # noqa: E402

SR = S.SR
TOPE_VOLUMEN_DB = -14.0
"""Volumen del panel máximo de las pruebas (amplitud 0,2 con entrada a escala completa: lib.sh)."""
SINK_COMBINADO = "aurasync_salida"
SINK_ENTRADA = "aurasync"


# -- lo que suena por cada camino --------------------------------------------------------------


def instalacion_viva(estado: dict):
    from aurasync.config import Instalacion, Parlante  # noqa: PLC0415

    parlantes = [
        Parlante(
            p["name"],
            p["sink"],
            retardo_ms=p["delay_ms"],
            ganancia_db=p["gain_db"],
            pan=p["pan"],
            ambiente=p["ambience"],
            ecualizacion_db=p.get("eq_db"),
            tipo=p.get("kind"),
        )
        for p in estado["speakers"]
    ]
    return Instalacion(parlantes=parlantes, retardo_traseros_ms=estado["global"]["rear_delay_ms"])


def salida_del_motor(estado: dict, cadena: dict, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
    """Lo que el motor del servicio escribiría para este pasaje: (n, parlantes), en el orden de
    `estado["speakers"]`. Mismo constructor que `aurasync.service._default_motor`."""
    from aurasync import motor as M  # noqa: PLC0415
    from aurasync.chain import ChainValues  # noqa: PLC0415

    elegido = {s["id"]: s["chosen"] for s in cadena["stages"] if s.get("chosen")}
    m = M.Motor(
        instalacion_viva(estado),
        SR,
        extraer_ambiente=True,
        decorrelar=True,
        volumen_db=estado["global"]["volume_db"],
        ecualizar=True,
        chain=ChainValues.from_json(elegido),
        bloque=estado["config"]["block_size"],
    )
    salidas = M.procesar_completo(m, izq, der, estado["config"]["block_size"])
    return np.stack([salidas[p["name"]] for p in estado["speakers"]], axis=1)


def mezcla_directa(estado: dict, izq: np.ndarray, der: np.ndarray, alinear: bool) -> np.ndarray:  # noqa: FBT001
    """La mezcla de cada parlante sin el motor: pan, ganancia y (si `alinear`) su retardo."""
    columnas = []
    for p in estado["speakers"]:
        x = ((1 - p["pan"]) / 2 * izq + (1 + p["pan"]) / 2 * der) * 10 ** (p["gain_db"] / 20)
        if alinear:
            d = round(p["delay_ms"] * SR / 1000)
            x = np.concatenate([np.zeros(d), x[: len(x) - d]])
        columnas.append(x)
    return np.stack(columnas, axis=1)


def igualar(directo: np.ndarray, motor: np.ndarray) -> tuple[np.ndarray, dict]:
    from aurasync.dsp.loudness import integrated_lufs  # noqa: PLC0415

    unos = [1.0] * directo.shape[1]
    l_motor = integrated_lufs(motor, SR, unos)
    l_directo = integrated_lufs(directo, SR, unos)
    ganancia = l_motor - l_directo
    igualado = directo * 10 ** (ganancia / 20)
    l_igualado = integrated_lufs(igualado, SR, unos)
    return igualado, {
        "lufs_motor": l_motor,
        "lufs_directo": l_directo,
        "ganancia_directo_db": ganancia,
        "lufs_directo_igualado": l_igualado,
        "diferencia_lu": l_igualado - l_motor,
        "pico_directo": float(np.max(np.abs(igualado))),
        "pico_motor": float(np.max(np.abs(motor))),
    }


def pasaje(ruta: Path, desde_s: float, segundos: float) -> tuple[np.ndarray, np.ndarray]:
    x, sr = S.leer_wav(ruta)
    if sr != SR:
        msg = f"{ruta.name} está a {sr} Hz: convertilo a {SR} (ffmpeg -i in.wav -ar {SR} out.wav)"
        raise S.FaltaSistema(msg)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    i0, n = int(desde_s * SR), int(segundos * SR)
    if i0 + n > len(x):
        msg = f"{ruta.name} dura {len(x) / SR:.0f} s: no alcanza para {desde_s}+{segundos} s"
        raise S.FaltaSistema(msg)
    trozo = S.fundido(x[i0 : i0 + n, :2], 50)
    return trozo[:, 0], trozo[:, 1]


def mapa_aux(objetos: list, estado: dict) -> dict:
    """Qué canal AUX del sink combinado va a qué parlante, si `pw-dump` lo dice; si no, el orden
    de los parlantes del estado (el del servicio al crear el sink), marcado como no verificado."""
    nodos = {o["id"]: S._props(o) for o in objetos if str(o.get("type")).endswith("Node")}  # noqa: SLF001
    enlaces = [
        (int(p["link.output.node"]), int(p["link.input.node"]))
        for o in objetos
        if str(o.get("type")).endswith("Link")
        for p in [S._props(o)]
        if "link.output.node" in p
    ]  # noqa: SLF001
    por_sink = {}
    for a, b in enlaces:
        posicion = str(nodos.get(a, {}).get("combine.audio.position", ""))
        destino = nodos.get(b, {}).get("node.name")
        if "AUX" in posicion and destino:
            por_sink[destino] = posicion.strip("[] ")
    esperado = {p["sink"]: f"AUX{i}" for i, p in enumerate(estado["speakers"])}
    if por_sink:
        return {"verificado": por_sink == esperado, "visto": por_sink, "esperado": esperado}
    return {"verificado": None, "visto": None, "esperado": esperado}


# -- una pasada ----------------------------------------------------------------------------------


def pasada(
    destino: str, datos: np.ndarray, canales: str, microfono: str, cola_s: float, srv: V.Servicio | None
) -> dict:
    grabacion = S.Grabacion(microfono).iniciar()
    calidad = []
    try:
        time.sleep(0.5)
        r = S.Reproduccion(destino, datos, canales).iniciar()
        fin = time.monotonic() + len(datos) / SR + cola_s
        while time.monotonic() < fin:
            if srv is not None:
                q = srv.estado().get("quality") or {}
                calidad.append(
                    {
                        "t": time.time(),
                        "sum_s": (q.get("sum") or {}).get("s"),
                        "input_s": (q.get("input") or {}).get("s"),
                    }
                )
            time.sleep(0.5)
        r.esperar()
    finally:
        audio = grabacion.detener()
    return {"audio": audio, "grabacion": grabacion.describir(), "reproduccion": r.verificado, "calidad": calidad}


def media_potencia(valores: list[float | None]) -> float | None:
    v = np.array([x for x in valores if x is not None], dtype=float)
    return float(10 * np.log10(np.mean(10 ** (v / 10)))) if len(v) else None


# -- todo -------------------------------------------------------------------------------------


def comprobar(srv: V.Servicio, ensayo: bool, cadena_actual: bool) -> tuple[dict, dict]:  # noqa: FBT001
    estado = srv.sesion_sonando()
    cadena = srv.orden("chain")
    problemas = []
    if estado["config"].get("output_mode") != "combinado":
        problemas.append(f"la salida es '{estado['config'].get('output_mode')}': hace falta la combinada")
    distintas = V.cadena_por_defecto(cadena)
    if distintas and not cadena_actual:
        problemas.append(f"la cadena no es la de por defecto en {distintas} (o pasá --cadena-actual y queda anotado)")
    if estado["global"]["volume_db"] > TOPE_VOLUMEN_DB:
        problemas.append(
            f"el volumen del panel está en {estado['global']['volume_db']} dB: bajalo a {TOPE_VOLUMEN_DB} o menos"
        )
    if any(p.get("muted") for p in estado["speakers"]):
        problemas.append("hay un parlante en silencio")
    if (estado.get("source") or {}).get("kind") != "system":
        problemas.append(f"la fuente del servicio es '{estado['source']['kind']}': ponela en 'system' (nada sonando)")
    if not ensayo:
        objetos = S.pw_dump()
        if not S.nodo_existe(objetos, SINK_COMBINADO):
            problemas.append(f"no existe el sink {SINK_COMBINADO}")
        entrando = S.quienes_alimentan(objetos, SINK_ENTRADA)
        if entrando:
            problemas.append(f"al sink {SINK_ENTRADA} ya le entra {entrando}: pausá la música")
    if problemas:
        raise S.FaltaSistema("; ".join(problemas))
    return estado, cadena


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0912, PLR0915
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("wavs", nargs="+", type=Path, help="las canciones (WAV a 48 kHz)")
    p.add_argument("--desde", type=float, default=30.0, help="segundo de la canción donde empieza el pasaje")
    p.add_argument("--segundos", type=float, default=40.0)
    p.add_argument("--microfono")
    p.add_argument("--sin-alinear", action="store_true", help="el directo sin los retardos de calibración")
    p.add_argument("--cadena-actual", action="store_true", help="medir aunque la cadena no sea la de por defecto")
    p.add_argument("--ensayo", action="store_true", help="sin sonar ni grabar: servicio y ganancias")
    p.add_argument("--puerto", type=int)
    p.add_argument("--config", type=Path, help="la carpeta con service.json")
    S.agregar_anotaciones(p)
    args = p.parse_args(argv)

    base = S.ruta_datos("15", f"directo-vs-motor-s{args.sesion}")
    try:
        if not args.ensayo:
            S.requerir("pw-play", "pw-record", "pw-dump", "pactl")
        srv = V.Servicio.conectar(args.puerto, config=args.config, registro=base.with_suffix(".api.jsonl"))
        estado, cadena = comprobar(srv, args.ensayo, args.cadena_actual)
        microfono = None if args.ensayo else S.resolver_microfono(args.microfono)
        pasajes = {w: pasaje(w, args.desde, args.segundos) for w in args.wavs}
    except (S.FaltaSistema, V.ErrorServicio) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    registro: dict = {
        "tipo": "directo_vs_motor",
        "entorno": S.entorno(),
        **S.anotaciones(args),
        "ensayo": args.ensayo,
        "alinear": not args.sin_alinear,
        "pasaje": {"desde_s": args.desde, "segundos": args.segundos},
        "estado": {
            k: estado.get(k) for k in ("global", "speakers", "chain_summary", "chain_latency_ms", "config", "service")
        },
        "cadena_elegida": {s["id"]: s["chosen"] for s in cadena["stages"] if s.get("chosen")},
        "microfono": microfono,
        "canciones": [],
    }
    if not args.ensayo:
        objetos = S.pw_dump()
        registro["mapa_aux"] = mapa_aux(objetos, estado)
        vol = S.VolumenParlante()
        registro["volumen_avrcp_pct"] = {p["name"]: vol.leer(p["sink"]) for p in estado["speakers"]}
    canales = ",".join(f"AUX{i}" for i in range(len(estado["speakers"])))
    cola_motor = 1.5 + (estado.get("chain_latency_ms") or 0) / 1000

    with S.Restaurador(None) as restaurador:
        if estado["recalibration"]["active"]:
            restaurador.cambio(
                "lazo de recalibración apagado",
                "recalibrate active=true",
                lambda: srv.orden("recalibrate", active=True),
                sistema=False,
            )
            srv.orden("recalibrate", active=False)
        try:
            if not args.ensayo:
                print("  · 3 s de silencio para el piso de la pieza…")
                g = S.Grabacion(microfono).iniciar()
                time.sleep(3.0)
                piso_audio = g.detener()
                piso = A.niveles_por_banda(piso_audio[SR // 2 :], A.centros(6, 100, 8000), 6, 8192)
                registro["piso_db"] = piso
            for wav, (izq, der) in pasajes.items():
                print(f"\n== {wav.name}")
                motor = salida_del_motor(estado, cadena, izq, der)
                directo, ganancia = igualar(mezcla_directa(estado, izq, der, not args.sin_alinear), motor)
                print(
                    f"  sonoridad: motor {ganancia['lufs_motor']:.2f} LUFS, directo {ganancia['lufs_directo']:.2f} → "
                    f"ganancia {ganancia['ganancia_directo_db']:+.2f} dB (queda {ganancia['diferencia_lu']:+.3f} LU)"
                )
                cancion = {"wav": wav.name, "igualacion": ganancia}
                registro["canciones"].append(cancion)
                if abs(ganancia["diferencia_lu"]) > 0.2 or ganancia["pico_directo"] > 0.9:  # noqa: PLR2004
                    cancion["error"] = "no se pudo igualar dentro de ±0,2 LU sin recortar"
                    print(f"  ✗ {cancion['error']}", file=sys.stderr)
                    continue
                if args.ensayo:
                    continue
                referencia = izq + der
                estereo = np.stack([izq, der], axis=1)
                pasadas = {}
                for nombre, destino, datos, mapa, cola, con_srv in (
                    ("a1", SINK_COMBINADO, directo, canales, 1.0, False),
                    ("b", SINK_ENTRADA, estereo, "FL,FR", cola_motor, True),
                    ("a2", SINK_COMBINADO, directo, canales, 1.0, False),
                ):
                    print(f"  ▶ {nombre} ({'directo' if nombre != 'b' else 'motor'})…", flush=True)
                    r = pasada(destino, datos, mapa, microfono, cola, srv if con_srv else None)
                    wav_out = S.escribir_wav(base.parent / f"{base.name}-{wav.stem}-{nombre}.wav", r["audio"])
                    donde, nitidez = A.ubicar(r["audio"], referencia)
                    pasadas[nombre] = A.recortar(r["audio"], donde, len(referencia), SR // 2)
                    cancion[nombre] = {
                        "wav": wav_out.name,
                        "grabacion": r["grabacion"],
                        "reproduccion": r["reproduccion"],
                        "inicio_en_grabacion_s": donde / SR,
                        "nitidez": nitidez,
                        "calidad_vivo": r["calidad"],
                    }
                    if r["grabacion"]["ganancia_cambio"]:
                        cancion["aviso"] = "la ganancia del micrófono cambió: no comparable"
                vivo = media_potencia([c["sum_s"] for c in cancion["b"]["calidad_vivo"]])
                cancion["sonoridad_vivo_vs_fuera_de_linea_lu"] = None if vivo is None else vivo - ganancia["lufs_motor"]
                res = A.comparar_pasadas(pasadas["a1"], pasadas["b"], pasadas["a2"], piso=registro["piso_db"])
                cancion["resultado"] = res
                cancion["criterio"] = A.criterio_directo_motor(res)
                print(
                    f"  ΔLUFS {res['delta_lufs']:+.2f} LU (a2−a1 {res['repeticion_lufs']:+.2f}) · peor sexto "
                    f"{res['peor_delta_banda_db']:.2f} dB (a2−a1 {res['peor_repeticion_banda_db']:.2f}) · "
                    f"estabilidad {res['estabilidad_db']:.2f} dB → {cancion['criterio']}"
                )
        except (S.RuteoIncorrecto, S.FaltaSistema, V.ErrorServicio, ValueError) as e:
            registro["error"] = str(e)
            print(f"✗ {e}", file=sys.stderr)
        except (KeyboardInterrupt, SystemExit) as e:
            # Ctrl-C o SIGTERM: se restaura (finally) y se guarda lo hecho, marcado.
            registro["error"] = f"interrumpido ({type(e).__name__})"
            print("\n✗ interrumpido: restauro y guardo lo hecho", file=sys.stderr)
        finally:
            registro["restauracion"] = restaurador.restaurar()
            ruta = S.guardar_json(base.with_suffix(".json"), registro)
            print(f"\n{ruta}")
    return 1 if "error" in registro else 0


if __name__ == "__main__":
    sys.exit(main())
