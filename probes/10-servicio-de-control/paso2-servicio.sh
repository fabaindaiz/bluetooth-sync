#!/usr/bin/env bash
# Paso 2: el servicio arranca y suena. Deja todo sonando para los pasos 3 y 4.
# paso2-servicio.sh [archivo.wav]
source "$(dirname "$0")/lib.sh"
anotar paso "2 · servicio: arrancar y sonar"
"$PROBE/servicio.sh" arrancar || exit 1
api POST /session/start || { estado; exit 1; }
esperar_sesion playing 10 || exit 1
python3 "$PROBE/ruteo.py" --esperar-aurasync || echo "  ✗ RUTEO MAL: revisar antes de seguir"
"$PROBE/fuente.sh" "${1:-}" || exit 1
estado
pausa 20 "¿suenan los tres como en el paso 1?"
