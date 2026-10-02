#!/usr/bin/env bash
# Paso 6: con el servicio sonando, `aurasync run` tiene que negarse (ya hay un nodo 'aurasync').
source "$(dirname "$0")/lib.sh"
esperar_sesion playing 2 || { echo "  primero el paso 2"; exit 1; }
anotar paso "6 · run con el servicio sonando"
salida=$(cd "$HOST" && timeout 30 hatch run aurasync run --volumen-db -20 2>&1); codigo=$?
echo "$salida" | tail -3
if ((codigo == 1)) && grep -q "already exists" <<<"$salida"; then
  anotar paso "6 · run se negó: $(tail -1 <<<"$salida")"
else
  echo "  ✗ run no se negó como se esperaba (código $codigo)"
fi
python3 "$PROBE/ruteo.py" --esperar-aurasync && estado
