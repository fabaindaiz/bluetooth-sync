#!/usr/bin/env bash
# Paso 7: cerrar sonando, de dos maneras: shutdown por la API y Ctrl-C (SIGINT).
# En las dos, la salida virtual desaparece y el proceso termina.
source "$(dirname "$0")/lib.sh"
anotar paso "7a · shutdown por la API"
"$PROBE/fuente.sh" detener
"$PROBE/servicio.sh" detener || exit 1
anotar paso "7b · Ctrl-C con una sesión sonando"
"$PROBE/servicio.sh" arrancar || exit 1
api POST /session/start && esperar_sesion playing 10 || exit 1
kill -INT "$(pid_de service)"
for _ in $(seq 20); do servicio_vivo || break; sleep 0.5; done
sleep 1
if servicio_vivo || nodo_aurasync || pgrep -f "aurasync service" >/dev/null; then
  echo "  ✗ quedó algo vivo: servicio $(servicio_vivo && echo sí || echo no), sink $(nodo_aurasync && echo sí || echo no)"
else
  anotar paso "7b · Ctrl-C cerró el servicio y el sink"
fi
python3 "$PROBE/ruteo.py"
