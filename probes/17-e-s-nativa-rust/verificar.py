"""Comprueba en `pw-dump` que la PoC quedó ruteada como se pidió (CLAUDE.md: lo que se le
pide a PipeWire se verifica, no se supone).

Uso, con `poc-pw` corriendo (o el servicio, con `--sink aurasync --salida-poc ''`):

    python3 verificar.py                      # aurasync_poc → aurasync_salida
    python3 verificar.py --dump volcado.json  # sobre un volcado guardado

Revisa:
1. que el sink `aurasync_poc` exista, sea `Audio/Sink` y **no** sea el sink por defecto
   (ni el actual, `default.audio.sink`, ni el guardado, `default.configured.audio.sink`);
2. que su stream de reproducción (`aurasync_poc_output`) tenga `target.object` al sink
   combinado y que **todos** sus enlaces terminen en él;
3. que cada salida del sink combinado llegue a un parlante (`bluez_output.*`);
4. que el volumen de los nodos propios y del sink combinado esté al 100 % y sin silenciar;
5. qué aplicaciones están entrando al sink.

Lo que `pw-dump` no dice es el *driver* de cada nodo: eso se mira con `pw-top` (README §4) y
con `same_driver` en el registro de la PoC. Sale con código 1 si alguna comprobación falla.
Solo la biblioteca estándar: corre con el `python3` del sistema.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TOLERANCIA_VOLUMEN = 1e-3
"""Un volumen a menos de esto de 1,0 cuenta como 100 %."""


def cargar(dump: Path | None) -> list[dict]:
    if dump is not None:
        return json.loads(dump.read_text())
    salida = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True).stdout
    return json.loads(salida)


def props(objeto: dict) -> dict:
    return (objeto.get("info") or {}).get("props") or {}


def por_defecto(objetos: list[dict]) -> dict[str, str | None]:
    """`default.audio.sink` y `default.configured.audio.sink` de la metadata `default`."""
    resultado: dict[str, str | None] = {"default.audio.sink": None, "default.configured.audio.sink": None}
    for o in objetos:
        if not str(o.get("type", "")).endswith("Metadata"):
            continue
        if (o.get("props") or {}).get("metadata.name") != "default":
            continue
        for entrada in o.get("metadata") or []:
            if entrada.get("subject") == 0 and entrada.get("key") in resultado:
                valor = entrada.get("value")
                resultado[entrada["key"]] = valor.get("name") if isinstance(valor, dict) else valor
    return resultado


def volumen(objeto: dict) -> tuple[list[float] | None, bool | None]:
    """(channelVolumes, mute) del último `Props` del nodo."""
    lista = ((objeto.get("info") or {}).get("params") or {}).get("Props") or []
    for p in reversed(lista):
        if "channelVolumes" in p or "mute" in p:
            return p.get("channelVolumes"), p.get("mute")
    return None, None


def verificar(objetos: list[dict], sink: str, salida_poc: str, destino: str) -> list[tuple[bool, str]]:
    nodos = {o["id"]: o for o in objetos if str(o.get("type", "")).endswith("Node")}
    nombre = {i: str(props(o).get("node.name", "")) for i, o in nodos.items()}
    por_nombre: dict[str, list[int]] = {}
    for i, n in nombre.items():
        por_nombre.setdefault(n, []).append(i)
    enlaces = []
    for o in objetos:
        if str(o.get("type", "")).endswith("Link"):
            p = props(o)
            if "link.output.node" in p and "link.input.node" in p:
                enlaces.append((int(p["link.output.node"]), int(p["link.input.node"])))
    r: list[tuple[bool, str]] = []

    ids = por_nombre.get(sink, [])
    r.append((len(ids) == 1, f"el sink {sink!r} existe una vez (ids {ids})"))
    if ids:
        clase = props(nodos[ids[0]]).get("media.class")
        r.append((clase == "Audio/Sink", f"{sink!r} es Audio/Sink ({clase})"))
    for clave, valor in por_defecto(objetos).items():
        r.append((valor != sink, f"{clave} = {valor!r}, no {sink!r}"))

    destinos = por_nombre.get(destino, [])
    r.append((len(destinos) == 1, f"el sink combinado {destino!r} existe una vez (ids {destinos})"))
    if salida_poc:
        salidas = por_nombre.get(salida_poc, [])
        r.append((len(salidas) == 1, f"el stream {salida_poc!r} existe una vez (ids {salidas})"))
        for s in salidas:
            target = props(nodos[s]).get("target.object")
            r.append((target == destino, f"{salida_poc!r} tiene target.object = {target!r}"))
            llega = sorted({nombre.get(b, "?") for a, b in enlaces if a == s})
            r.append((llega == [destino], f"{salida_poc!r} está enlazado a {llega} ({sum(a == s for a, _ in enlaces)} enlaces)"))
    combinadas = [i for i, n in nombre.items() if n.startswith(f"output.{destino}")]
    r.append((bool(combinadas), f"{len(combinadas)} salidas del sink combinado"))
    for c in combinadas:
        llega = sorted({nombre.get(b, "?") for a, b in enlaces if a == c})
        ok = len(llega) == 1 and llega[0].startswith("bluez_output.")
        r.append((ok, f"{nombre[c]} → {llega}"))

    # El sink combinado también: WirePlumber le restauraba un 46 % guardado (experimentos/10).
    for n in (sink, salida_poc, destino):
        for i in por_nombre.get(n, []) if n else []:
            volumenes, mute = volumen(nodos[i])
            ok = mute is not True and (volumenes is None or all(abs(v - 1.0) < TOLERANCIA_VOLUMEN for v in volumenes))
            r.append((ok, f"volumen de {n!r}: {volumenes}, mute {mute}"))

    for i in ids:
        apps = sorted(
            {
                str(props(nodos.get(a, {})).get("application.name") or nombre.get(a, "?"))
                for a, b in enlaces
                if b == i
            }
        )
        r.append((True, f"entran a {sink!r}: {apps or 'nada (sin música todavía)'}"))
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sink", default="aurasync_poc")
    ap.add_argument("--salida-poc", default=None, help="el stream de reproducción (por omisión: <sink>_output)")
    ap.add_argument("--destino", default="aurasync_salida")
    ap.add_argument("--dump", type=Path, default=None, help="un volcado de pw-dump guardado, en vez del vivo")
    a = ap.parse_args()
    salida_poc = f"{a.sink}_output" if a.salida_poc is None else a.salida_poc
    resultados = verificar(cargar(a.dump), a.sink, salida_poc, a.destino)
    for ok, texto in resultados:
        print(("ok    " if ok else "FALLA ") + texto)
    return 0 if all(ok for ok, _ in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
