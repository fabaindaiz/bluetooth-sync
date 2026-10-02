#!/usr/bin/env bash
# fuente.sh [archivo.wav] | detener
# Reproduce la señal de prueba (o un archivo) en el sink virtual 'aurasync', en bucle, y
# COMPRUEBA que llegó ahí: si terminara en otro destino podría ir directo a un parlante.
source "$(dirname "$0")/lib.sh"
PIDF="$TMPD/fuente.pid"
if [[ "${1:-}" == detener ]]; then
  [[ -f "$PIDF" ]] && kill "$(cat "$PIDF")" 2>/dev/null; pkill -f "pw-play --target aurasync" 2>/dev/null
  rm -f "$PIDF"; anotar fuente "detenida"; exit 0
fi
nodo_aurasync || { echo "  ✗ no hay sink 'aurasync': primero run o start"; exit 1; }
ARCHIVO="${1:-$TMPD/senal.wav}"
if [[ ! -f "$ARCHIVO" ]]; then py "$PROBE/senal.py" "$ARCHIVO" 600; fi
( while true; do pw-play --target aurasync "$ARCHIVO" || break; done ) &
echo $! >"$PIDF"
sleep 1.5
# Comprobación independiente: el stream de pw-play de la fuente está enlazado a 'aurasync'.
destino=$(pw-dump | python3 -c '
import json,sys
o=json.load(sys.stdin); n={x["id"]:(x.get("info") or {}).get("props") or {} for x in o if str(x.get("type")).endswith("Node")}
for x in o:
    p=(x.get("info") or {}).get("props") or {}
    if str(x.get("type")).endswith("Link") and n.get(p.get("link.output.node"),{}).get("target.object")=="aurasync":
        print(n.get(p.get("link.input.node"),{}).get("node.name"))' | sort -u)
if [[ "$destino" != aurasync ]]; then
  echo "  ✗ la fuente terminó en '${destino:-ningún destino}', no en 'aurasync': se detiene"
  "$0" detener; exit 1
fi
anotar fuente "sonando en 'aurasync': $(basename "$ARCHIVO")"
