"""¿El pasa-altos de `bass=protect` retrasa la protección de graves del Go 4? (experimentos/14 §B)

El Go 4 baja los graves por su cuenta pasado ~50 % de volumen (REPORTADO). Con **un** Go 4
sonando (los otros en silencio por la API), la misma señal pasa por aurasync con `bass=off` y
después con `bass=protect` (corte 90 Hz, orden 4, sin armónicos: `harmonics_db` = −24), y el
volumen Bluetooth del parlante se sube de 40 a 100 %. En el micrófono se mide, en cada volumen,
el nivel del tercio de 125 Hz y el de 63–100 Hz **relativos** al de 500 Hz–2 kHz: sin protección
son constantes al subir el volumen; la protección del firmware los hace caer. La caída "empieza"
en el primer volumen con 3 dB menos que a 40 %.

**Señal: ruido rosa** (por defecto). Es estacionario y tiene energía en cada tercio, así que el
nivel de 125 Hz se compara entre volúmenes y entre sesiones sin depender de qué pasaje sonó; con
música, el grave cambia de un compás a otro y una sesión no repetiría a la otra. Como el firmware
reacciona al nivel, la amplitud importa: el ruido llega al parlante con pico 0,2 (el tope de las
pruebas; el volumen del panel se ajusta para eso y se comprueba con el true peak que mide el
servicio). Si con `off` no aparece la caída, el resultado es **inconcluso**: hace falta más nivel,
y subir el tope se conversa antes. `--wav` mide con música (el mismo pasaje en cada paso).

Criterio (spec §8): con `protect`, la caída de 125 Hz aparece a un volumen mayor o no aparece, en
**dos sesiones** (`comparar.py`).

Cambia y restaura (también con Ctrl-C): el volumen Bluetooth del parlante (sistema: anotado en
`datos/14/cambios-de-sistema.txt` antes), el silencio de los otros, el volumen del panel, la
etapa `bass` y el lazo de recalibración (sesión de aurasync). `--ensayo` recorre la parte del
servicio sin sonar, grabar ni tocar el volumen Bluetooth (lo que se prueba en el Mac con
`aurasync service --simular`).

Uso (PC-Ryzen5, con la sesión de aurasync sonando y nada entrando a `aurasync`):

    $PY probes/15-graves-y-volumen/proteccion.py --parlante Red --sesion 1 --microfono-en "…"
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.append(str(AQUI.parent / "16-calidad"))
import analisis as A  # noqa: E402
import servicio as V  # noqa: E402
import sistema as S  # noqa: E402

SR = S.SR
BANDAS = {"125": (112, 140), "63_100": (56, 112)}


def buscar_parlante(estado: dict, pedido: str) -> dict:
    nombres = S.coincidencias(pedido, [(p["sink"], p["name"]) for p in estado["speakers"]])
    hallados = [p for p in estado["speakers"] if p["sink"] in nombres]
    if len(hallados) != 1:
        msg = f"{pedido!r} coincide con {[p['name'] for p in hallados] or 'ningún'} parlante del servicio"
        raise S.FaltaSistema(msg)
    return hallados[0]


def senal(args: argparse.Namespace, pan: float) -> tuple[np.ndarray, np.ndarray]:
    """(estéreo para `aurasync`, lo que le llega al parlante elegido: la referencia)."""
    n = int(args.segundos * SR)
    if args.wav:
        x, sr = S.leer_wav(args.wav)
        if sr != SR:
            msg = f"{args.wav} está a {sr} Hz; hace falta {SR}"
            raise S.FaltaSistema(msg)
        x = x if x.shape[1] >= 2 else np.repeat(x, 2, axis=1)  # noqa: PLR2004
        i0 = int(args.desde * SR)
        estereo = S.fundido(x[i0 : i0 + n, :2], 50)
    else:
        r = S.ruido_rosa(n, semilla=5) * 0.5
        estereo = S.fundido(np.stack([r, r], axis=1), 50)
    referencia = (1 - pan) / 2 * estereo[:, 0] + (1 + pan) / 2 * estereo[:, 1]
    return estereo, referencia


def analizar(audio: np.ndarray, referencia: np.ndarray, pasos: list[dict]) -> dict:
    medidos = A.medir_pasos(audio, referencia, [p["inicio_aprox"] for p in pasos], despues_s=3.0)
    for p, m in zip(pasos, medidos, strict=True):
        p.update(m)
        p["relativo_db"] = {b: A.nivel_relativo_graves(m["tercios_db"], banda=r) for b, r in BANDAS.items()}
    out = {}
    for alg in ("off", "protect"):
        mios = [p for p in pasos if p["bass"] == alg]
        out[alg] = {b: A.inicio_de_caida([p["pct"] for p in mios], [p["relativo_db"][b] for p in mios]) for b in BANDAS}
    out["veredicto"] = {b: A.comparar_proteccion(out["off"][b], out["protect"][b]) for b in BANDAS}
    return out


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0912, PLR0915
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--parlante", required=True, help="nombre, MAC o sink del Go 4 que suena")
    p.add_argument("--porcentajes", default="40,50,60,70,80,90,100")
    p.add_argument("--segundos", type=float, default=5.0)
    p.add_argument("--wav", type=Path, help="música en vez de ruido rosa (WAV a 48 kHz)")
    p.add_argument("--desde", type=float, default=30.0, help="con --wav: dónde empieza el pasaje")
    p.add_argument("--pico", type=float, default=0.2, help="pico que llega al parlante; nunca más de 0,2")
    p.add_argument("--corte", type=float, default=90.0)
    p.add_argument("--microfono")
    p.add_argument("--ensayo", action="store_true", help="solo la parte del servicio, sin sonar ni grabar")
    p.add_argument("--puerto", type=int)
    p.add_argument("--config", type=Path)
    S.agregar_anotaciones(p)
    args = p.parse_args(argv)
    pico = S.limitar_amplitud(args.pico)
    porcentajes = sorted(float(x) for x in args.porcentajes.split(","))
    base = S.ruta_datos("14", f"proteccion-s{args.sesion}")

    try:
        if not args.ensayo:
            S.requerir("pw-play", "pw-record", "pw-dump", "pactl")
        srv = V.Servicio.conectar(args.puerto, config=args.config, registro=base.with_suffix(".api.jsonl"))
        estado = srv.sesion_sonando()
        if estado["volume_avrcp"]["state"] != "off":
            msg = "el volumen está en modo 'avrcp' (el servicio maneja el volumen del parlante): pasalo a 'digital'"
            raise S.FaltaSistema(msg)
        objetivo = buscar_parlante(estado, args.parlante)
        if not args.ensayo:
            entrando = S.quienes_alimentan(S.pw_dump(), "aurasync")
            if entrando:
                msg = f"al sink aurasync ya le entra {entrando}: pausá la música"
                raise S.FaltaSistema(msg)
            microfono = S.resolver_microfono(args.microfono)
            vol = S.VolumenParlante()
            previo_pct = vol.leer(objetivo["sink"])
        else:
            microfono, vol, previo_pct = None, None, None
        estereo, referencia = senal(args, objetivo["pan"])
    except (S.FaltaSistema, V.ErrorServicio) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    volumen_db = max(-60.0, min(0.0, math.floor(20 * math.log10(pico / np.max(np.abs(referencia))) * 2) / 2))
    previo = {
        "volume_db": estado["global"]["volume_db"],
        "muted": {sp["name"]: sp["muted"] for sp in estado["speakers"]},
        "bass": srv.etapa("bass")["chosen"],
        "recalibrar": estado["recalibration"]["active"],
        "pct": previo_pct,
    }
    registro: dict = {
        "tipo": "proteccion",
        "entorno": S.entorno(),
        **S.anotaciones(args),
        "ensayo": args.ensayo,
        "parlante": objetivo["name"],
        "sink": objetivo["sink"],
        "senal": str(args.wav) if args.wav else "rosa",
        "pico_al_parlante": pico,
        "volumen_panel_db": volumen_db,
        "corte_hz": args.corte,
        "previo": previo,
        "microfono": microfono,
        "pasos": [],
    }
    print(f"{objetivo['name']} · panel a {volumen_db} dB para pico {pico} · volúmenes {porcentajes} %")
    audio = None
    with S.Restaurador(S.DATOS / "14" / "cambios-de-sistema.txt") as r:
        grabacion = None
        try:
            if previo["recalibrar"]:
                r.cambio(
                    "lazo apagado",
                    "recalibrate active=true",
                    lambda: srv.orden("recalibrate", active=True),
                    sistema=False,
                )
                srv.orden("recalibrate", active=False)
            r.cambio(
                "silencio",
                "muted como antes",
                lambda: [srv.orden("set", speaker=n, changes={"muted": m}) for n, m in previo["muted"].items()],
                sistema=False,
            )
            for nombre in previo["muted"]:
                srv.orden("set", speaker=nombre, changes={"muted": nombre != objetivo["name"]})
            r.cambio(
                "volumen del panel",
                f"volume_db {previo['volume_db']}",
                lambda: srv.orden("set", changes={"volume_db": previo["volume_db"]}),
                sistema=False,
            )
            srv.orden("set", changes={"volume_db": volumen_db})
            r.cambio("etapa bass", "como estaba", lambda: srv.restaurar_etapa("bass", previo["bass"]), sistema=False)
            if not args.ensayo:
                r.cambio(
                    f"volumen de {objetivo['sink']} ({objetivo['name']}) de {previo_pct:.1f} % a {porcentajes} % (proteccion.py)",
                    f"pactl set-sink-volume {objetivo['sink']} {previo_pct:.2f}%",
                    lambda: vol.poner(objetivo["sink"], previo_pct),
                )
                grabacion = S.Grabacion(microfono).iniciar()
            for alg in ("off", "protect"):
                params = {"cutoff_hz": args.corte, "order": 4, "harmonics_db": -24.0} if alg == "protect" else None
                srv.orden("chain_set", stage="bass", algorithm=alg, **({"params": params} if params else {}))
                print(f"\n== bass = {alg}")
                time.sleep(0.5)
                for pct in porcentajes:
                    paso: dict = {"bass": alg, "pct": pct}
                    if not args.ensayo:
                        paso["pct_leido"] = vol.poner(objetivo["sink"], pct)
                        time.sleep(0.3)
                        rep = S.Reproduccion("aurasync", estereo).iniciar()
                        paso["inicio_aprox"] = int((rep.inicio - grabacion.inicio) * SR)
                        paso["reproduccion"] = rep.verificado
                        rep.esperar()
                        time.sleep(1.5)
                    else:
                        time.sleep(0.2)
                    q = srv.estado().get("quality") or {}
                    paso["calidad"] = {
                        "tp_max": q.get("tp_max"),
                        "salida": (q.get("outputs") or {}).get(objetivo["name"]),
                    }
                    tope = 20 * math.log10(pico) + 1.0
                    if q.get("tp_max") is not None and q["tp_max"] > tope:
                        msg = f"el true peak de salida llegó a {q['tp_max']} dBTP, sobre el tope {tope:.1f}: corto"
                        raise S.FaltaSistema(msg)
                    print(f"    {pct:5.0f} %  tp_max {q.get('tp_max')} dBTP", flush=True)
                    registro["pasos"].append(paso)
        except (S.RuteoIncorrecto, S.FaltaSistema, V.ErrorServicio, ValueError) as e:
            registro["error"] = str(e)
            print(f"✗ {e}", file=sys.stderr)
        except (KeyboardInterrupt, SystemExit) as e:
            # Ctrl-C o SIGTERM: se restaura (finally) y se guarda lo hecho, marcado.
            registro["error"] = f"interrumpido ({type(e).__name__})"
            print("\n✗ interrumpido: restauro y guardo lo hecho", file=sys.stderr)
        finally:
            if grabacion is not None:
                audio = grabacion.detener()
                registro["grabacion"] = {
                    **grabacion.describir(),
                    "wav": S.escribir_wav(base.with_suffix(".wav"), audio).name,
                }
            registro["restauracion"] = r.restaurar()
            registro["chain_bass_final"] = srv.etapa("bass")["chosen"]
    if audio is not None and "error" not in registro:
        try:
            registro["resultado"] = analizar(audio, referencia, registro["pasos"])
            for b, v in registro["resultado"]["veredicto"].items():
                off, pro = registro["resultado"]["off"][b], registro["resultado"]["protect"][b]
                print(f"  {b} Hz: caída desde {off['inicio_pct']} % sin, {pro['inicio_pct']} % con → {v}")
        except ValueError as e:
            registro["error"] = f"análisis: {e}"
    print(S.guardar_json(base.with_suffix(".json"), registro))
    return 1 if "error" in registro else 0


if __name__ == "__main__":
    sys.exit(main())
