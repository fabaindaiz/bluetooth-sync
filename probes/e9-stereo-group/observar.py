#!/usr/bin/env python3
"""Observa la formación del Stereo Group de JBL desde afuera (E9, i-7c8794-3e42af).

Registra, con marca de tiempo, **cada cambio** en lo que anuncian y exponen los JBL
mientras se forma o se deshace el grupo: datos de fabricante, ServiceData, UUIDs, estado
de conexión y qué endpoints de audio crea BlueZ. La idea es ver la *transición*, que es
donde está la información: en reposo los anuncios ya se conocen
(`docs/research/experimentos/01-e2-anuncios-jbl-mac.md`).

**Lo que este probe NO puede ver, y conviene tenerlo claro:** el enlace entre los dos
parlantes. El AX210 no tiene los bits de LE Features 13 (Periodic Advertising) ni 31
(Synchronized Receiver), así que **no puede sincronizarse a los anuncios periódicos ni
recibir un BIS** (E1, experimento 03). Del relé Auracast entre primario y secundario solo
se ven los **anuncios extendidos**, no la BASE ni los datos. Para eso hacen falta las
SuperMini.

Lo que sí se ve, y alcanza para bastante:
- el byte que marca el modo estéreo (en el Mac se vio `09 60` en los bytes 8–9);
- si aparece o desaparece el anuncio de Auracast (0x1852) al formar el grupo;
- cuál de los dos parlantes deja de exponer A2DP, o sea **quién es el secundario**;
- si el primario cambia lo que anuncia al pasar a transmisor.

Uso:
    probes/e9-stereo-group/observar.py --segundos 180 > captura.txt

Mientras corre, formá o deshacé el Stereo Group desde los parlantes o la app.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# Los JBL conocidos de este equipo. Direcciones propias: el repositorio es privado
# (d-7c8794-8374e1).
CONOCIDOS = {
    "90:F2:60:DA:66:6D": "Go 4 Black",
    "90:F2:60:75:4A:83": "Go 4 Red",
    "90:F2:60:E3:07:39": "Go 4 Blue",
    "78:66:F3:93:1D:B7": "Charge 6",
    "88:92:CC:68:91:C0": "Tune 770NC",
}
CAMPOS = ("Connected", "ServicesResolved", "RSSI", "TxPower")


def desempaquetar(v):
    while isinstance(v, dict) and "data" in v:
        v = v["data"]
    return v


def objetos() -> dict:
    out = subprocess.run(
        ["busctl", "--json=short", "call", "org.bluez", "/",
         "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"],
        capture_output=True, text=True,
    ).stdout
    try:
        return json.loads(out)["data"][0]
    except (json.JSONDecodeError, KeyError, IndexError):
        return {}


def estado() -> dict[str, dict]:
    """Un resumen por dispositivo, comparable entre muestras."""
    d = objetos()
    # Endpoints de audio que BlueZ crea por dispositivo: sep* es A2DP, pac_* es LE Audio.
    endpoints: dict[str, list[str]] = {}
    for ruta in d:
        for addr in CONOCIDOS:
            clave = "dev_" + addr.replace(":", "_")
            if clave in ruta and ruta.rstrip("/").split("/")[-1] != clave:
                resto = ruta.split(clave + "/", 1)[-1]
                if resto and "/" not in resto:
                    endpoints.setdefault(addr, []).append(resto)

    res = {}
    for ruta, ifaces in d.items():
        dev = ifaces.get("org.bluez.Device1")
        if not dev:
            continue
        g = lambda k, dflt=None: desempaquetar(dev.get(k, {"data": dflt}))
        addr = str(g("Address", ""))
        if addr not in CONOCIDOS:
            continue
        md = {}
        for cid, val in (g("ManufacturerData", {}) or {}).items():
            md[f"0x{int(cid):04x}"] = bytes(desempaquetar(val)).hex()
        sd = {}
        for u, val in (g("ServiceData", {}) or {}).items():
            sd[str(u)[4:8]] = bytes(desempaquetar(val)).hex()
        res[addr] = {
            **{c: g(c) for c in CAMPOS},
            "ManufacturerData": md,
            "ServiceData": sd,
            "UUIDs_cortos": sorted({str(u)[4:8] for u in (g("UUIDs", []) or [])}),
            "endpoints": sorted(endpoints.get(addr, [])),
        }
    return res


def diferencias(viejo: dict, nuevo: dict) -> list[str]:
    lineas = []
    for addr in sorted(set(viejo) | set(nuevo)):
        a, b = viejo.get(addr), nuevo.get(addr)
        nombre = CONOCIDOS.get(addr, addr)
        if a is None:
            lineas.append(f"  + APARECE  {nombre}: {json.dumps(b, ensure_ascii=False)}")
            continue
        if b is None:
            lineas.append(f"  - SE VA    {nombre}")
            continue
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                # El RSSI cambia todo el tiempo y no dice nada del grupo.
                if k == "RSSI":
                    continue
                lineas.append(f"    {nombre:12s} {k}: {a.get(k)!r} → {b.get(k)!r}")
    return lineas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segundos", type=int, default=180)
    ap.add_argument("--intervalo", type=float, default=1.0)
    args = ap.parse_args()

    print(f"# observación del Stereo Group — {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    print(f"# {args.segundos} s, muestreando cada {args.intervalo} s")
    print("# El AX210 no puede ver anuncios periódicos ni BIS (E1): solo anuncios")
    print("# extendidos, estado de conexión y endpoints de BlueZ.\n")

    # Sin escaneo activo, BlueZ no refresca los datos de anuncio.
    scan = subprocess.Popen(
        ["bluetoothctl", "scan", "le"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    try:
        time.sleep(2)
        anterior = estado()
        print("== estado inicial ==")
        for addr, v in sorted(anterior.items()):
            print(f"  {CONOCIDOS.get(addr, addr):12s} {json.dumps(v, ensure_ascii=False)}")
        print("\n== cambios (formá o deshacé el grupo ahora) ==")
        t0 = time.time()
        cambios = 0
        while time.time() - t0 < args.segundos:
            time.sleep(args.intervalo)
            actual = estado()
            difs = diferencias(anterior, actual)
            if difs:
                print(f"\n[{time.time() - t0:7.1f} s]")
                for linea in difs:
                    print(linea)
                sys.stdout.flush()
                cambios += len(difs)
            anterior = actual
        print(f"\n== fin: {cambios} cambios registrados ==")
        if cambios == 0:
            print("Ningún cambio. ¿Se formó el grupo mientras corría? ¿Están a la vista?")
    finally:
        scan.terminate()
        scan.wait(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
