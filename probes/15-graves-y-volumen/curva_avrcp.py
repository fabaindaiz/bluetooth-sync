"""La curva volumen Bluetooth (AVRCP) → dB de cada Go 4, en el micrófono (experimentos/14 §A).

Por cada parlante, **uno a la vez** y directo a su sink (sin aurasync): el mismo ruido rosa a
amplitud fija (0,1; nunca más de 0,2), con el volumen del parlante en 20, 40, 60, 80 y 100 %, de
ida y de vuelta. Cada volumen se pide con `pactl set-sink-volume` y **se lee de vuelta** (como
`aurasync.bt_volume`); cada `pw-play` y la grabación se verifican en `pw-dump`. Se mide el nivel
en el micrófono por tercio y en total (100 Hz–10 kHz), y |H| por tercio contra el ruido.

**Cambia el estado del sistema:** el volumen de cada sink Bluetooth (WirePlumber lo recuerda).
Antes de tocarlo, lee el valor previo y escribe en `datos/14/cambios-de-sistema.txt` qué cambia y
el comando para revertirlo; al terminar lo restaura y lo lee de vuelta, también con Ctrl-C o
SIGTERM. Si algo falla al restaurar, lo dice y queda anotado.

Criterio (spec §8): la curva es monótona y se repite a ±0,5 dB entre **dos sesiones
independientes** (otra colocación del micrófono): `comparar.py`.

Uso (PC-Ryzen5, con la sesión de aurasync detenida):

    $PY probes/15-graves-y-volumen/curva_avrcp.py --sesion 1 --microfono-en "1 m al frente, 1 m alto" \\
        --bateria "Red=80,Black=75,Blue=90"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.append(str(AQUI.parent / "16-calidad"))
import analisis as A  # noqa: E402
import sistema as S  # noqa: E402

SR = S.SR


def plan_de_volumenes(porcentajes: list[float], ida_y_vuelta: bool) -> list[float]:  # noqa: FBT001
    subida = sorted(porcentajes)
    return subida + subida[-2::-1] if ida_y_vuelta else subida


def medir_parlante(
    sink: str, nombre: str, plan: list[float], ruido: np.ndarray, microfono: str, vol: S.VolumenParlante, base: Path
) -> dict:
    estereo = np.stack([ruido, ruido], axis=1)
    grabacion = S.Grabacion(microfono).iniciar()
    pasos, inicios, verificaciones = [], [], []
    try:
        time.sleep(2.0)  # el piso de la pieza
        for pct in plan:
            leido = vol.poner(sink, pct)
            time.sleep(0.3)
            r = S.Reproduccion(sink, estereo)
            r.iniciar()
            inicios.append(int((r.inicio - grabacion.inicio) * SR))
            verificaciones.append(r.verificado)
            print(f"    {pct:5.0f} % (leído {leido:.1f} %) ✓ {r.verificado['destinos'][0]}", flush=True)
            r.esperar()
            time.sleep(0.8)
            pasos.append({"pct": pct, "pct_leido": leido})
    finally:
        audio = grabacion.detener()
    wav = S.escribir_wav(base.parent / f"{base.name}-{nombre.split()[-1].lower()}.wav", audio)
    piso = A.niveles_por_tercio(audio[int(0.5 * SR) : int(1.8 * SR)], 16384)
    medidos = A.medir_pasos(audio, ruido, inicios)
    for paso, m in zip(pasos, medidos, strict=True):
        paso.update(m)
        paso["snr_tercios_db"] = m["tercios_db"] - piso
        paso["db"] = m["total_db"]
    medias, histeresis = A.promediar_ida_y_vuelta(pasos)
    curva = A.curva_relativa(medias)
    return {
        "sink": sink,
        "wav": wav.name,
        "grabacion": grabacion.describir(),
        "reproducciones": verificaciones,
        "piso_tercios_db": piso,
        "pasos": pasos,
        "curva_db": curva,
        "curva_pipewire_db": {p: A.db_pipewire(p) for p in curva},
        "histeresis_db": histeresis,
        "monotona": A.monotona(curva),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--parlantes", nargs="*", help="nombres, MAC o nodos (por defecto todos los bluez_output)")
    p.add_argument("--porcentajes", default="20,40,60,80,100")
    p.add_argument("--segundos", type=float, default=4.0, help="duración del ruido en cada volumen")
    p.add_argument("--amplitud", type=float, default=0.1, help="pico del ruido; nunca más de 0,2")
    p.add_argument("--solo-ida", action="store_true", help="sin la vuelta (la vuelta mide la histéresis)")
    p.add_argument("--microfono")
    S.agregar_anotaciones(p)
    args = p.parse_args(argv)
    amplitud = S.limitar_amplitud(args.amplitud)
    porcentajes = [float(x) for x in args.porcentajes.split(",")]
    if 100.0 not in porcentajes:
        print("✗ la curva se refiere al 100 %: incluilo en --porcentajes", file=sys.stderr)
        return 2

    try:
        S.requerir("pw-play", "pw-record", "pw-dump", "pactl")
        objetos = S.pw_dump()
        sinks = S.elegir_sinks(objetos, args.parlantes)
        ocupados = {d: S.quienes_alimentan(objetos, s) for s, d in sinks.items()}
        ocupados = {d: o for d, o in ocupados.items() if o}
        if ocupados:
            msg = f"estos parlantes ya reciben audio: {ocupados}. Detené la sesión de aurasync y la música."
            raise S.FaltaSistema(msg)
        microfono = S.resolver_microfono(args.microfono)
        vol = S.VolumenParlante()
        previos = {s: vol.leer(s) for s in sinks}
    except S.FaltaSistema as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    base = S.ruta_datos("14", f"curva-avrcp-s{args.sesion}")
    plan = plan_de_volumenes(porcentajes, not args.solo_ida)
    ruido = S.fundido(S.ruido_rosa(int(args.segundos * SR), semilla=0) * amplitud)
    registro: dict = {
        "tipo": "curva_avrcp",
        "entorno": S.entorno(),
        **S.anotaciones(args),
        "microfono": microfono,
        "amplitud": amplitud,
        "plan_pct": plan,
        "volumen_previo_pct": {sinks[s]: v for s, v in previos.items()},
        "parlantes": {},
    }
    cambios = S.DATOS / "14" / "cambios-de-sistema.txt"
    print(f"micrófono {microfono} · {len(sinks)} parlantes · plan {plan} %")
    with S.Restaurador(cambios) as restaurador:
        try:
            for sink, nombre in sinks.items():
                previo = previos[sink]
                restaurador.cambio(
                    f"volumen de {sink} ({nombre}) de {previo:.1f} % a {plan} % (curva_avrcp.py)",
                    f"pactl set-sink-volume {sink} {previo:.2f}%",
                    lambda s=sink, v=previo: vol.poner(s, v),
                )
                print(f"\n== {nombre}: volumen previo {previo:.1f} %")
                registro["parlantes"][nombre] = r = medir_parlante(sink, nombre, plan, ruido, microfono, vol, base)
                vol.poner(sink, previo)  # el siguiente parlante se mide con este como estaba
                for pct, d in r["curva_db"].items():
                    print(f"    {pct:5.0f} % → {d:+6.2f} dB (PipeWire diría {A.db_pipewire(pct):+6.2f})")
                print(f"    monótona: {r['monotona']['ok']} · histéresis {r['histeresis_db']:.2f} dB")
        except (S.RuteoIncorrecto, S.FaltaSistema, ValueError) as e:
            registro["error"] = str(e)
            print(f"✗ {e}", file=sys.stderr)
        except (KeyboardInterrupt, SystemExit) as e:
            # Ctrl-C o SIGTERM: se restaura (finally) y se guarda lo hecho, marcado.
            registro["error"] = f"interrumpido ({type(e).__name__})"
            print("\n✗ interrumpido: restauro y guardo lo hecho", file=sys.stderr)
        finally:
            registro["restauracion"] = restaurador.restaurar()
            registro["volumen_final_pct"] = {sinks[s]: vol.leer(s) for s in sinks}
            ruta = S.guardar_json(base.with_suffix(".json"), registro)
            print(f"\n{ruta}")
    return 1 if "error" in registro else 0


if __name__ == "__main__":
    sys.exit(main())
