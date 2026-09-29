#!/usr/bin/env bash
# E6, parte funcional (i-7c8794-24ea65): un canal por parlante con A2DP clásico y
# libpipewire-module-combine-stream.
#
# NO NECESITA ROOT y no deja nada: el sink vive en el proceso `pw-cli -m`, y
# desaparece cuando se lo mata (la misma propiedad que busca P1).
#
# Uso:  probes/e6-a2dp/combine.sh [segundos]
#
# Lo que NO mide: el desfase entre parlantes. Para eso hace falta un micrófono, y es
# la parte que decide si A2DP alcanza. Esto solo responde si el mecanismo funciona y
# si el adaptador aguanta dos streams.
set -uo pipefail
cd "$(dirname "$0")"
dur="${1:-10}"

# `pw-cli load-module` sin -m carga el módulo y sale, y el módulo muere con el
# proceso. Con -m el proceso queda vivo y el sink existe.
pw-cli -m load-module libpipewire-module-combine-stream "$(cat combine.json)" >/dev/null 2>&1 &
mod=$!
trap 'kill $mod 2>/dev/null' EXIT
sleep 3

if ! pactl list short sinks | grep -q jbl_combine; then
  echo "no se creó el sink jbl_combine; ¿están conectados los dos parlantes?" >&2
  pactl list short sinks >&2
  exit 1
fi
echo "sink jbl_combine creado. Streams:"
pw-dump | grep -A1 '"output.jbl_combine' | grep -oE 'output\.jbl_combine\S+' | sed 's/^/  /'

tono="$(mktemp --suffix=.wav)"
trap 'kill $mod 2>/dev/null; rm -f "$tono"' EXIT
python3 - "$tono" "$dur" <<'PY'
import math, struct, sys, wave
ruta, dur = sys.argv[1], float(sys.argv[2])
sr, amp = 48000, 0.25
w = wave.open(ruta, "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
# Izquierdo 440 Hz, derecho 880 Hz: se distinguen de oído sin instrumentos.
w.writeframes(b"".join(
    struct.pack("<hh",
        int(amp * 32767 * math.sin(2 * math.pi * 440 * n / sr)),
        int(amp * 32767 * math.sin(2 * math.pi * 880 * n / sr)))
    for n in range(int(sr * dur))))
w.close()
PY

echo
echo "Reproduciendo ${dur} s: 440 Hz (grave) en FL, 880 Hz (agudo) en FR."
echo "Escuchá qué parlante toca cuál. Según combine.json:"
echo "  FL → Go 4 Black (90:F2:60:DA:66:6D)"
echo "  FR → Charge 6   (78:66:F3:93:1D:B7)"
pw-play --target jbl_combine "$tono"

echo
echo "Listo. Al salir se mata el módulo; el sink desaparece solo."
