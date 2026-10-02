#!/usr/bin/env bash
# grabar.sh <nombre> | detener — graba el micrófono a datos/10/<nombre>-<hora>.wav.
# Anota el instante de inicio (epoch) para cruzarlo con el registro (`clics.py`).
source "$(dirname "$0")/lib.sh"
PIDF="$TMPD/grabar.pid"
if [[ "${1:-}" == detener ]]; then
  [[ -f "$PIDF" ]] && kill -INT "$(cat "$PIDF")" 2>/dev/null; sleep 1; rm -f "$PIDF"; exit 0
fi
MIC=$(cd "$HOST" && hatch run python -c 'from argparse import Namespace; from aurasync.cli import resolver_microfono; print(resolver_microfono(Namespace(microfono=None)) or "")')
[[ -n "$MIC" ]] || { echo "  ✗ no hay micrófono"; exit 1; }
WAV="$DATOS/$1-$(date +%H%M%S).wav"
pw-record --target "$MIC" --rate 48000 --channels 1 --format s16 "$WAV" &
echo $! >"$PIDF"
echo "$(date +%s.%N)" >"$WAV.inicio"
anotar grabacion "$WAV con $MIC"
echo "$WAV"
