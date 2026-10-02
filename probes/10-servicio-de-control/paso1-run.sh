#!/usr/bin/env bash
# Paso 1: `aurasync run` sigue sonando como antes (su lazo se movió a session.py).
# paso1-run.sh [segundos=45] [archivo.wav]
source "$(dirname "$0")/lib.sh"
SEG="${1:-45}"
anotar paso "1 · run durante ${SEG}s a -20 dB"
LOG="$DATOS/paso1-run-$(date +%H%M%S).log"
(cd "$HOST" && con_ctrl_c hatch run aurasync run --volumen-db -20 >"$LOG" 2>&1) &
for _ in $(seq 40); do nodo_aurasync && break; sleep 0.5; done
nodo_aurasync || { echo "  ✗ run no creó el sink"; cat "$LOG"; exit 1; }
sleep 2
python3 "$PROBE/ruteo.py" --esperar-aurasync | tee -a "$LOG" || echo "  ✗ RUTEO MAL: revisar antes de seguir"
"$PROBE/fuente.sh" "${2:-}" || { kill -INT "$(pid_de run)"; exit 1; }
pausa "$SEG" "¿suenan los tres? ¿Red a la izquierda, Black a la derecha, Blue difuso? ¿igual que el 29?"
"$PROBE/fuente.sh" detener
kill -INT "$(pid_de run)"
for _ in $(seq 20); do nodo_aurasync || break; sleep 0.5; done
t0=$SECONDS
for _ in $(seq 30); do pid_de run >/dev/null || break; sleep 0.5; done
if pid_de run >/dev/null; then echo "  ✗ run sigue vivo 15 s después de Ctrl-C"; else anotar paso "1 · run terminó $((SECONDS - t0)) s después de que se fue el sink"; fi
if nodo_aurasync; then echo "  ✗ el sink 'aurasync' quedó vivo después de Ctrl-C"; else anotar paso "1 · el sink desapareció al cerrar"; fi
echo "  log de run: $LOG"
