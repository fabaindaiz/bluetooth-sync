"""Una sesión de escucha contra el servicio vivo: guarda el estado, los cortes y la radio.

Pregunta cada segundo `GET /v1/state` (el respaldo por polling del stream, spec 2026-10-02
§6.3) y escribe en JSON Lines, a `docs/research/experimentos/datos/12/`:

- `inicio`: la nota, la condición, la batería y las posiciones que se anotaron a mano, el
  equipo y las versiones (kernel, PipeWire, WirePlumber, BlueZ);
- `muestra` (1 por segundo): la radio por parlante, los xruns, el nivel de la tubería, si
  Bluetooth buscaba, la batería que informa BlueZ;
- `corte`: cada corte nuevo de la tarjeta Cortes, una vez;
- `resumen`: cortes por minuto de la radio por parlante, cortes por causa.

Tolera campos ausentes: si el estado todavía no trae `radio` (antes del paquete E, o con
`--simular`), la sesión corre igual y el resumen lo dice.

Uso (desde la raíz del repositorio, con el servicio corriendo):

    python3 probes/14-microcortes/sesion.py --minutos 10 --nota "base 1" --condicion base \
        --bateria "Red=80,Blue=75,Black=90" --posiciones "Red=frente-izq,Blue=frente-der,Black=atras"

Python 3.12 o más nuevo, solo la biblioteca estándar. Ctrl-C termina antes y escribe el
resumen igual (marcado `completa: false`).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DATOS = AQUI.parents[1] / "docs/research/experimentos/datos/12"
CAIDA_MAXIMA_S = 30.0
"""Sin respuesta del servicio por más que esto, la sesión termina (incompleta)."""


class SinServicio(RuntimeError):
    pass


def leer_token(config: Path) -> str:
    archivo = config / "service.json"
    try:
        return json.loads(archivo.read_text())["token"]
    except (OSError, ValueError, KeyError) as e:
        msg = f"no pude leer el token de {archivo} ({e}). ¿Se corrió alguna vez `aurasync service` con ese XDG_CONFIG_HOME?"
        raise SinServicio(msg) from e


def pedir_estado(url: str, token: str, timeout: float = 3.0) -> dict:
    pedido = urllib.request.Request(f"{url}/state", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as r:
            respuesta = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            respuesta = json.loads(e.read())
        except ValueError:
            msg = f"el servicio respondió HTTP {e.code} en {url}/state"
            raise SinServicio(msg) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        msg = f"el servicio no responde en {url}/state ({e}). ¿Está corriendo `aurasync service`, en ese puerto?"
        raise SinServicio(msg) from e
    if not respuesta.get("ok"):
        error = respuesta.get("error") or {}
        msg = f"el servicio rechazó el pedido: {error.get('code')}: {error.get('message')}"
        raise SinServicio(msg)
    return respuesta["result"]


def pares(texto: str | None) -> dict[str, str]:
    """`"Red=80,Blue=75"` → `{"Red": "80", "Blue": "75"}`."""
    out = {}
    for parte in (texto or "").split(","):
        if "=" in parte:
            k, v = parte.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def version(*comando: str) -> str | None:
    if shutil.which(comando[0]) is None:
        return None
    try:
        r = subprocess.run(list(comando), capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    texto = (r.stdout or r.stderr).strip()
    return texto.splitlines()[-1] if texto else None


def entorno() -> dict:
    return {
        "equipo": platform.node(),
        "kernel": platform.release(),
        "sistema": platform.system(),
        "pipewire": version("pipewire", "--version"),
        "wireplumber": version("wireplumber", "--version"),
        "bluez": version("bluetoothctl", "--version"),
        "python": platform.python_version(),
    }


def radio_de(estado: dict) -> dict | None:
    """El resumen de radio del estado, donde lo ponga el paquete E (raíz o `health`)."""
    radio = estado.get("radio")
    if radio is None:
        radio = (estado.get("health") or {}).get("radio")
    return radio if isinstance(radio, dict) else None


def cortes_de(estado: dict) -> list[dict]:
    cuts = (estado.get("health") or {}).get("cuts") or estado.get("cuts") or {}
    eventos = cuts.get("events") if isinstance(cuts, dict) else None
    return eventos if isinstance(eventos, list) else []


def muestra(estado: dict) -> dict:
    health = estado.get("health") or {}
    return {
        "sesion": (estado.get("session") or {}).get("status"),
        "radio": radio_de(estado),
        "xruns": health.get("xruns"),
        "pipe_level_ms": health.get("pipe_level_ms"),
        "bt_discovering": health.get("bt_discovering"),
        "realtime_x": health.get("realtime_x"),
        "parlantes": {
            p.get("name"): {k: p.get(k) for k in ("battery_pct", "connected", "playing", "rssi_dbm") if k in p}
            for p in estado.get("speakers") or []
            if isinstance(p, dict)
        },
    }


def clave(evento: dict) -> tuple:
    return (evento.get("t"), evento.get("kind"), evento.get("where"), evento.get("detail"))


def resumir(primera: dict | None, ultima: dict | None, cortes: list[dict], minutos: float) -> dict:
    """Cortes de radio por minuto por parlante (por el contador de la radio) y cortes por causa."""
    radio_ini = radio_de(primera or {})
    radio_fin = radio_de(ultima or {})
    por_parlante: dict[str, dict] = {}
    if radio_fin is not None:
        antes = (radio_ini or {}).get("speakers") or {}
        for nombre, fin in (radio_fin.get("speakers") or {}).items():
            total_fin = fin.get("drops_total") or 0
            total_ini = (antes.get(nombre) or {}).get("drops_total") or 0
            drops = max(0, total_fin - total_ini)
            por_parlante[nombre] = {
                "drops": drops,
                "drops_por_min": round(drops / minutos, 3) if minutos > 0 else None,
                "identificado": fin.get("identified"),
                "bitpool_fin": fin.get("bitpool"),
                "bitpool_mediana_60s_fin": fin.get("bitpool_median_60s"),
            }
    fallas = [c for c in cortes if c.get("fault")]
    por_causa: dict[str, int] = {}
    radio_por_lugar: dict[str, int] = {}
    for c in fallas:
        por_causa[c.get("kind", "?")] = por_causa.get(c.get("kind", "?"), 0) + 1
        if c.get("kind") == "radio":
            radio_por_lugar[str(c.get("where"))] = radio_por_lugar.get(str(c.get("where")), 0) + 1
    return {
        "minutos": round(minutos, 3),
        "radio_disponible": bool(radio_fin and radio_fin.get("available")),
        "radio_motivo": (radio_fin or {}).get("reason")
        if radio_fin is not None
        else "el estado no trae `radio` (falta el cableado del paquete E, o es la simulación)",
        "radio_por_parlante": por_parlante,
        "cortes_por_causa": por_causa,
        "cortes_radio_por_lugar": radio_por_lugar,
        "cortes_por_min": round(len(fallas) / minutos, 3) if minutos > 0 else None,
        "intencionales": sum(1 for c in cortes if c.get("kind") == "fade"),
    }


def ruta_de_salida(nota: str, ahora: datetime) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", nota.lower()).strip("-")[:40] or "sesion"
    return DATOS / f"{ahora.strftime('%Y-%m-%dT%H%M%S')}-{slug}.jsonl"


def imprimir_resumen(resumen: dict) -> None:
    print(f"\n== resumen ({resumen['minutos']} min) ==")
    if resumen["radio_por_parlante"]:
        for nombre, r in resumen["radio_por_parlante"].items():
            marca = "" if r["identificado"] is not False else "  (enlace sin identificar)"
            print(f"  radio {nombre:<20} {r['drops']:>5} descartes  {r['drops_por_min']} /min{marca}")
    else:
        print(f"  radio: sin datos ({resumen['radio_motivo']})")
    if resumen["cortes_por_causa"]:
        for causa, n in sorted(resumen["cortes_por_causa"].items(), key=lambda kv: -kv[1]):
            print(f"  cortes {causa:<12} {n}")
    else:
        print("  cortes: ninguno registrado")
    print(f"  intencionales (no cuentan): {resumen['intencionales']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minutos", type=float, default=10.0)
    p.add_argument("--nota", required=True, help="qué es esta sesión, en palabras")
    p.add_argument("--condicion", default="base", help="la condición de C2/C3: base, dos-parlantes, …")
    p.add_argument("--bateria", help='la batería que se ve en cada parlante: "Red=80,Blue=75"')
    p.add_argument("--posiciones", help='dónde está cada uno: "Red=frente-izq,Blue=frente-der"')
    p.add_argument("--puerto", type=int, default=int(os.environ.get("PUERTO", "8731")))
    p.add_argument("--url", help="pisa http://127.0.0.1:<puerto>/v1")
    p.add_argument("--config", type=Path, help="carpeta con service.json (por defecto la de XDG_CONFIG_HOME)")
    p.add_argument("--cada", type=float, default=1.0, help="segundos entre muestras")
    p.add_argument("--salida", type=Path, help="archivo .jsonl (por defecto datos/12/<fecha>-<nota>.jsonl)")
    args = p.parse_args(argv)

    config = args.config or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "aurasync"
    url = args.url or f"http://127.0.0.1:{args.puerto}/v1"
    try:
        token = leer_token(config)
        primera = pedir_estado(url, token)
    except SinServicio as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    inicio = datetime.now().astimezone()
    salida = args.salida or ruta_de_salida(args.nota, inicio)
    salida.parent.mkdir(parents=True, exist_ok=True)
    detener = {"pedido": False}
    signal.signal(signal.SIGINT, lambda *_: detener.update(pedido=True))
    signal.signal(signal.SIGTERM, lambda *_: detener.update(pedido=True))

    vistos: set[tuple] = set()
    cortes: list[dict] = []
    ultima = primera
    completa = True
    t0 = time.monotonic()
    fin = t0 + args.minutos * 60
    ultimo_ok = t0
    with salida.open("a", encoding="utf-8") as f:

        def escribir(clase: str, **datos) -> None:
            f.write(json.dumps({"t": time.time(), "clase": clase, **datos}, ensure_ascii=False) + "\n")
            f.flush()

        escribir(
            "inicio",
            nota=args.nota,
            condicion=args.condicion,
            minutos=args.minutos,
            bateria=pares(args.bateria),
            posiciones=pares(args.posiciones),
            url=url,
            entorno=entorno(),
            muestra=muestra(primera),
        )
        for e in cortes_de(primera):  # lo que ya estaba: se anota, pero no es de esta sesión
            vistos.add(clave(e))
        print(f"sesión de {args.minutos:g} min → {salida}")
        while not detener["pedido"] and time.monotonic() < fin:
            time.sleep(max(0.0, args.cada - (time.monotonic() - t0) % args.cada))
            try:
                estado = pedir_estado(url, token)
            except SinServicio as e:
                escribir("error", texto=str(e))
                if time.monotonic() - ultimo_ok > CAIDA_MAXIMA_S:
                    print(f"\n✗ {e} — más de {CAIDA_MAXIMA_S:.0f} s sin respuesta: termino", file=sys.stderr)
                    completa = False
                    break
                continue
            ultimo_ok = time.monotonic()
            ultima = estado
            escribir("muestra", **muestra(estado))
            for e in cortes_de(estado):
                if clave(e) not in vistos:
                    vistos.add(clave(e))
                    cortes.append(e)
                    escribir("corte", corte=e)
            radio = radio_de(estado)
            drops = sum((s.get("drops_total") or 0) for s in ((radio or {}).get("speakers") or {}).values())
            restante = max(0, fin - time.monotonic())
            print(
                f"\r  {restante / 60:5.1f} min restantes · cortes {sum(1 for c in cortes if c.get('fault'))}"
                f" · radio {'—' if radio is None else drops}   ",
                end="",
                flush=True,
            )
        if detener["pedido"]:
            completa = False
        minutos = (time.monotonic() - t0) / 60
        resumen = resumir(primera, ultima, cortes, minutos)
        escribir("resumen", completa=completa, **resumen)
    imprimir_resumen(resumen)
    print(f"\n{'✓' if completa else '…'} {salida}")
    return 0 if completa else 1


if __name__ == "__main__":
    sys.exit(main())
