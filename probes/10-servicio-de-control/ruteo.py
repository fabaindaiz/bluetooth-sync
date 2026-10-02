"""¿A dónde va de verdad cada stream de audio? Leído de `pw-dump`, sin pasar por aurasync.

Es la comprobación independiente que pide CLAUDE.md ("lo que el programa le pide al sistema
de audio se verifica"): el servicio ya revisa su propio ruteo, pero si esa revisión tuviera
un error, este script no lo compartiría.

Imprime cada stream de salida con su destino pedido (`target.object`) y el real (el nodo al
que está enlazado), y marca ✗ los que no coinciden. Sale con 1 si hay alguno mal, o si un
stream de un parlante termina en el sink virtual `aurasync` (el lazo de realimentación de
experimentos/09).

Uso: python3 ruteo.py [--esperar-aurasync]
"""

import json
import subprocess
import sys


def main() -> int:
    objetos = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, check=True).stdout)
    nodos, enlaces = {}, {}
    for o in objetos:
        props = (o.get("info") or {}).get("props") or {}
        tipo = str(o.get("type"))
        if tipo.endswith("Node"):
            nodos[o["id"]] = props
        elif tipo.endswith("Link") and "link.output.node" in props:
            enlaces.setdefault(int(props["link.output.node"]), set()).add(int(props["link.input.node"]))

    nombres = {i: str(p.get("node.name", "")) for i, p in nodos.items()}
    hay_aurasync = "aurasync" in nombres.values()
    malos = 0
    print(f"  sink virtual 'aurasync': {'existe' if hay_aurasync else 'no existe'}")
    for ident, props in sorted(nodos.items()):
        if not str(props.get("media.class", "")).startswith("Stream/Output/Audio"):
            continue
        app = props.get("application.name") or props.get("application.process.binary") or "?"
        pedido = props.get("target.object")
        destinos = sorted(nombres.get(d, str(d)) for d in enlaces.get(ident, ()))
        real = ", ".join(destinos) or "ningún destino"
        mal = pedido is not None and pedido not in destinos
        lazo = app == "pw-play" and pedido and pedido.startswith("bluez_output.") and "aurasync" in destinos
        marca = "✗" if (mal or lazo) else "✓"
        malos += mal or lazo
        print(f"  {marca} {app:<14} pedido {pedido or '(ninguno)':<36} → {real}")
    if "--esperar-aurasync" in sys.argv and not hay_aurasync:
        print("  ✗ se esperaba el sink 'aurasync' y no está")
        return 1
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())
