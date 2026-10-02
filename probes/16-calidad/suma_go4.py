"""¿El Go 4 suma L+R o elige un canal? (experimentos/15 §A, research/11 §3.3)

A **un** Go 4, directo a su sink Bluetooth (sin aurasync): un tono de 1 kHz en L solo, R solo,
L = R y L = −R, dos veces (la segunda en orden inverso), con un marcador de ruido al principio
para ubicar todo en la grabación del micrófono. Si L = −R queda 30 dB o más bajo L = R, el
parlante suma (la resta se cancela adentro, antes del aire); si suena igual y un canal solo no
suena, elige ese canal.

Antes de tocar nada comprueba que hay `pw-play`, `pw-record`, `pw-dump` y `pactl`, que el sink
existe y que **nadie más** le está mandando audio (la sesión de aurasync tiene que estar
detenida). Cada reproducción y la grabación se verifican en `pw-dump`. No cambia el volumen
del parlante: lo lee y lo anota.

Uso (PC-Ryzen5, desde la raíz del repositorio):

    PY="$(cd host && hatch env find default)/bin/python"
    $PY probes/16-calidad/suma_go4.py --parlante Red --microfono-en "1 m al frente, 1 m de alto" \\
        --bateria "Red=80" --nota "suma L+R"

Datos: `docs/research/experimentos/datos/15/<fecha>-suma-<parlante>.{json,wav}` (el .wav no se
versiona).
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
import sistema as S  # noqa: E402

SR = S.SR
CONDICIONES = ("L", "R", "LR", "LmR")


def estimulo(
    frecuencia: float, amplitud: float, tono_s: float = 3.0, pausa_s: float = 1.0
) -> tuple[np.ndarray, list[dict]]:
    """El estéreo completo y su plan: dónde empieza cada tramo, en segundos del archivo."""
    plan, partes, t = [], [], 0.0

    def agregar(nombre: str, x: np.ndarray) -> None:
        nonlocal t
        plan.append({"tramo": nombre, "inicio_s": t, "dur_s": len(x) / SR})
        partes.append(x)
        t += len(x) / SR

    silencio = lambda s: np.zeros((int(SR * s), 2))  # noqa: E731
    agregar("inicio", silencio(0.5))
    marca = S.fundido(S.ruido_rosa(int(SR * 0.5), semilla=1) * amplitud)
    agregar("marcador", np.stack([marca, marca], axis=1))
    agregar("piso", silencio(2.0))
    tono = S.fundido(amplitud * np.sin(2 * np.pi * frecuencia * np.arange(int(SR * tono_s)) / SR))
    canales = {"L": (tono, 0 * tono), "R": (0 * tono, tono), "LR": (tono, tono), "LmR": (tono, -tono)}
    for vuelta, orden in ((1, CONDICIONES), (2, CONDICIONES[::-1])):
        for c in orden:
            agregar(f"{c}#{vuelta}", np.stack(canales[c], axis=1))
            agregar("pausa", silencio(pausa_s))
    return np.concatenate(partes), plan


def analizar(grabacion: np.ndarray, x: np.ndarray, plan: list[dict], frecuencia: float) -> dict:
    marcador = next(p for p in plan if p["tramo"] == "marcador")
    i0, largo = int(marcador["inicio_s"] * SR), int(marcador["dur_s"] * SR)
    donde, nitidez = A.ubicar(grabacion, x[i0 : i0 + largo, 0])
    if nitidez < 10:  # noqa: PLR2004
        msg = f"el marcador no se encuentra en la grabación (nitidez {nitidez:.1f}): ¿el micrófono oye al parlante?"
        raise RuntimeError(msg)
    desfase = donde - i0
    niveles: dict[str, list[float]] = {c: [] for c in CONDICIONES}
    piso = None
    for p in plan:
        inicio = desfase + p["inicio_s"] * SR
        if p["tramo"] == "piso":
            piso = A.nivel_tono(A.recortar(grabacion, inicio, int(p["dur_s"] * SR), int(0.4 * SR)), frecuencia, 16384)
        elif "#" in p["tramo"]:
            tramo = A.recortar(grabacion, inicio, int(p["dur_s"] * SR), int(0.5 * SR))
            niveles[p["tramo"].split("#")[0]].append(A.nivel_tono(tramo, frecuencia, 16384))
    medios = {c: float(np.mean(v)) for c, v in niveles.items()}
    dispersion = {c: float(np.max(v) - np.min(v)) for c, v in niveles.items()}
    assert piso is not None
    # Lo que suena (20 dB sobre el piso) tiene que repetirse entre vueltas a 1 dB; lo que no suena
    # es ruido y varía más sin que importe.
    sonando = [c for c in CONDICIONES if medios[c] > piso + 20]
    repetible = all(dispersion[c] <= 1.0 for c in sonando)
    decision = A.decidir_suma(medios, piso)
    if not repetible:
        decision = {**decision, "veredicto": "inconcluso", "motivo": f"las vueltas no se repiten a 1 dB: {dispersion}"}
    return {
        "desfase_ms": desfase / SR * 1000,
        "nitidez_marcador": nitidez,
        "niveles_db": niveles,
        "medios_db": medios,
        "dispersion_entre_vueltas_db": dispersion,
        "piso_db": piso,
        "repetible": repetible,
        "decision": decision,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--parlante", required=True, help="el Go 4: nombre, MAC o nodo bluez_output…")
    p.add_argument("--microfono", help="nodo de PipeWire del micrófono (por defecto, el de aurasync)")
    p.add_argument("--frecuencia", type=float, default=1000.0)
    p.add_argument("--amplitud", type=float, default=0.1, help="por canal; nunca más de 0,2")
    S.agregar_anotaciones(p)
    args = p.parse_args(argv)
    amplitud = S.limitar_amplitud(args.amplitud)

    try:
        S.requerir("pw-play", "pw-record", "pw-dump", "pactl")
        objetos = S.pw_dump()
        sinks = S.elegir_sinks(objetos, [args.parlante])
        sink, descripcion = next(iter(sinks.items()))
        otros = S.quienes_alimentan(objetos, sink)
        if otros:
            msg = f"{descripcion} ya recibe audio de {otros}: detené la sesión de aurasync y la música antes"
            raise S.FaltaSistema(msg)
        microfono = S.resolver_microfono(args.microfono)
    except S.FaltaSistema as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    x, plan = estimulo(args.frecuencia, amplitud)
    base = S.ruta_datos("15", f"suma-{descripcion.split()[-1].lower()}")
    volumen = S.VolumenParlante().leer(sink)
    print(f"{descripcion} ({sink}) al {volumen:.0f} % · micrófono {microfono} · {len(x) / SR:.0f} s")
    registro = {
        "tipo": "suma_go4",
        "entorno": S.entorno(),
        **S.anotaciones(args),
        "parlante": descripcion,
        "sink": sink,
        "volumen_avrcp_pct": volumen,
        "frecuencia_hz": args.frecuencia,
        "amplitud": amplitud,
        "plan": plan,
    }
    grabacion = S.Grabacion(microfono)
    try:
        grabacion.iniciar()
        time.sleep(0.5)
        r = S.Reproduccion(sink, x).iniciar()
        print(f"  ✓ suena en {r.verificado['destinos']}; grabando de {grabacion.verificado['origenes']}")
        r.esperar()
        time.sleep(1.0)
    except (S.RuteoIncorrecto, S.FaltaSistema) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    finally:
        datos = grabacion.detener()
    registro["grabacion"] = {**grabacion.describir(), "wav": str(S.escribir_wav(base.with_suffix(".wav"), datos).name)}
    registro["reproduccion"] = r.verificado
    if grabacion.describir()["ganancia_cambio"]:
        print("  ⚠ la ganancia del micrófono cambió durante la medición: no es comparable", file=sys.stderr)
    try:
        registro["resultado"] = analizar(datos, x, plan, args.frecuencia)
    except (RuntimeError, ValueError) as e:
        registro["error"] = str(e)
        S.guardar_json(base.with_suffix(".json"), registro)
        print(f"✗ {e}", file=sys.stderr)
        return 1
    S.guardar_json(base.with_suffix(".json"), registro)
    res = registro["resultado"]
    for c in CONDICIONES:
        print(f"  {c:<4} {res['medios_db'][c]:7.1f} dB (entre vueltas {res['dispersion_entre_vueltas_db'][c]:.1f} dB)")
    print(f"  piso {res['piso_db']:7.1f} dB")
    d = res["decision"]
    print(f"\n→ {d['veredicto']}: {d['motivo']}\n  {base.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
