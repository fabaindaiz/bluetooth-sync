#!/usr/bin/env bash
# Antes de cualquier paso: el entorno, el firmware de cada Go 4 y que no quede nada de
# una prueba anterior. No cambia nada. Guarda lo que imprime en datos/10/.
source "$(dirname "$0")/lib.sh"
SALIDA="$DATOS/preflight-$(date +%F-%H%M).txt"
{
  echo "== $(date -Iseconds) · $(hostname)"
  echo "kernel $(uname -r) · $(pactl info | sed -n 's/^Server Name: //p') · $(wireplumber --version 2>/dev/null | tail -1) · $(bluetoothctl --version)"
  echo "AX210: $(journalctl -k -b --no-pager 2>/dev/null | grep -m1 -o 'Firmware revision [0-9.]*\|Bluetooth: hci0: Firmware SHA1: 0x[0-9a-f]*' || echo 'sin leer (journal)')"
  echo
  echo "== parlantes (Modalias = vendedor, producto y versión que anuncia el firmware)"
  for mac in 90:F2:60:75:4A:83 90:F2:60:DA:66:6D 90:F2:60:E3:07:39; do
    info=$(bluetoothctl info "$mac" 2>/dev/null)
    printf '  %s  %-16s conectado: %-3s  %s\n' "$mac" \
      "$(sed -n 's/^\s*Name: //p' <<<"$info")" \
      "$(sed -n 's/^\s*Connected: //p' <<<"$info")" \
      "$(sed -n 's/^\s*Modalias: //p' <<<"$info")"
  done
  echo
  echo "== aurasync doctor"
  aurasync doctor 2>&1
  echo
  echo "== restos de una prueba anterior"
  if nodo_aurasync; then echo "  ✗ ya existe un sink 'aurasync': hay un run o un service vivo"; else echo "  ✓ no hay sink 'aurasync'"; fi
  if servicio_vivo; then echo "  ✗ algo responde en el puerto $PUERTO"; else echo "  ✓ el puerto $PUERTO está libre"; fi
  echo "  sink por defecto: $(pactl get-default-sink)"
  echo
  echo "== ruteo actual"
  python3 "$PROBE/ruteo.py"
} 2>&1 | tee "$SALIDA"
echo "guardado en $SALIDA"
