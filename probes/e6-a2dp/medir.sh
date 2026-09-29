#!/usr/bin/env bash
# E6 completo (i-7c8794-24ea65): arma el sink combinado con un canal por parlante,
# mide el desfase con el micrófono y limpia todo al salir.
#
# NO NECESITA ROOT. El sink vive en el proceso `pw-cli -m` y muere con él.
#
# ANTES DE CORRERLO, lo físico, que es lo que decide si el número sirve:
#   1. los dos parlantes encendidos y conectados;
#   2. el micrófono **equidistante** de los dos (cada 34 cm de diferencia = 1 ms);
#   3. volumen parejo y audible en los dos, y la sala razonablemente callada.
#
# Uso:  probes/e6-a2dp/medir.sh [repeticiones] [-- extras para medir-desfase.py]
set -uo pipefail
cd "$(dirname "$0")"
reps="${1:-10}"; shift || true
[ "${1:-}" = "--" ] && shift

pw-cli -m load-module libpipewire-module-combine-stream "$(cat combine.json)" >/dev/null 2>&1 &
mod=$!
trap 'kill $mod 2>/dev/null' EXIT
sleep 3

if ! pactl list short sinks | grep -q jbl_combine; then
  echo "no se creó jbl_combine; ¿están conectados los dos parlantes?" >&2
  pactl list short sinks >&2
  exit 1
fi
echo "sink jbl_combine listo. Asignación (de combine.json):"
grep -oE 'bluez_output\.[0-9A-F_]+\.[0-9]+|combine\.audio\.position = \[ [A-Z]+ \]' combine.json | paste - - | sed 's/^/  /'
echo

exec ./medir-desfase.py --repeticiones "$reps" "$@"
