#!/usr/bin/env bash
# Paso 3: ajustes en vivo, grabando el micrófono para buscar clics después.
# Requiere el paso 2 sonando con la señal de prueba (sin archivo: los tonos dejan ver clics).
# paso3-ajustes.sh [segundos entre cambios=6]
source "$(dirname "$0")/lib.sh"
ESPERA="${1:-6}"
esperar_sesion playing 2 || { echo "  primero el paso 2"; exit 1; }
anotar paso "3 · ajustes en vivo, ${ESPERA}s entre cambios"
WAV=$("$PROBE/grabar.sh" paso3 | tail -1)
pausa 5 "referencia: así suena antes de cualquier cambio"
n=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$PROBE/cambios-paso3.json")
for i in $(seq 0 $((n - 1))); do
  IFS=$'\t' read -r ruta cuerpo escuchar < <(python3 -c '
import json,sys; c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(c["ruta"], json.dumps(c["cuerpo"]), c["escuchar"], sep="\t")' "$PROBE/cambios-paso3.json" "$i")
  echo "  [$((i + 1))/$n]"
  api PATCH "$ruta" "$cuerpo"
  pausa "$ESPERA" "$escuchar"
done
"$PROBE/grabar.sh" detener
estado
echo "  análisis de clics:"
py "$PROBE/clics.py" "$WAV" "$REGISTRO" "$(cat "$WAV.inicio")" | tee "$WAV.clics.txt"
