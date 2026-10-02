#!/usr/bin/env bash
# servicio.sh arrancar | detener | redeploy | estado
# Arranca `aurasync service` en segundo plano, solo en 127.0.0.1, con su salida en datos/10/.
source "$(dirname "$0")/lib.sh"
LOG="$DATOS/servicio-$(date +%F).log"
case "${1:-estado}" in
  arrancar)
    servicio_vivo && { echo "  ya hay un servicio respondiendo"; exit 0; }
    # El servicio no puede heredar ni la terminal ni la tubería de quien lo lanza: el
    # subshell también va redirigido y hace `exec`, así que no queda ningún bash intermedio
    # esperando con la tubería abierta (eso colgaba un `servicio.sh redeploy | tail`).
    ( cd "$HOST" || exit 1
      exec setsid python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' \
        hatch run aurasync service --bind "${BIND:-127.0.0.1}" --port "$PUERTO" ) </dev/null >>"$LOG" 2>&1 &
    for _ in $(seq 30); do servicio_vivo && break; sleep 0.5; done
    servicio_vivo || { echo "  ✗ el servicio no arrancó; ver $LOG"; tail -5 "$LOG"; exit 1; }
    [[ "$(stat -c %a "$CONF/service.json")" == 600 ]] || { echo "  ✗ service.json no tiene modo 600"; exit 1; }
    anotar servicio "arrancado (log en $LOG)"
    ;;
  detener)
    api POST /shutdown || true
    for _ in $(seq 20); do servicio_vivo || break; sleep 0.5; done
    if servicio_vivo; then echo "  ✗ sigue respondiendo"; exit 1; fi
    if nodo_aurasync; then echo "  ✗ el sink 'aurasync' quedó vivo"; exit 1; fi
    anotar servicio "detenido; el sink 'aurasync' desapareció"
    ;;
  redeploy)
    # Para lanzar código nuevo: el cierre ordenado es también la prueba del paso 7.
    "$0" detener || exit 1
    sobras=$(pgrep -af "pw-play --target bluez|node.name=aurasync" | grep -v pgrep)
    [[ -z "$sobras" ]] || { echo "  ✗ quedaron procesos: $sobras"; exit 1; }
    "$0" arrancar
    ;;
  estado) estado ;;
esac
