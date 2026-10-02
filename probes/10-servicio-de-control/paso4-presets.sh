#!/usr/bin/env bash
# Paso 4: dos presets distintos, alternados. Requiere el paso 2 sonando (con música si se
# quiere juzgar la diferencia; con la señal de prueba si se quiere buscar clics).
# paso4-presets.sh [vueltas=3] [segundos=10]
source "$(dirname "$0")/lib.sh"
VUELTAS="${1:-3}"; SEG="${2:-10}"
esperar_sesion playing 2 || { echo "  primero el paso 2"; exit 1; }
anotar paso "4 · presets a (cerrado) y b (amplio), $VUELTAS vueltas de ${SEG}s"
api PATCH /global '{"rear_delay_ms": 8, "extract_ambience": true, "decorrelate": true}'
api PATCH "/speakers/JBL%20Go%204%20Blue" '{"ambience": 0.4}'
api PUT /presets/prueba-a
api PATCH /global '{"rear_delay_ms": 18}'
api PATCH "/speakers/JBL%20Go%204%20Blue" '{"ambience": 0.8}'
api PATCH "/speakers/JBL%20Go%204%20Red" '{"ambience": 0.3}'
api PATCH "/speakers/JBL%20Go%204%20Black" '{"ambience": 0.3}'
api PUT /presets/prueba-b
WAV=$("$PROBE/grabar.sh" paso4 | tail -1)
for v in $(seq "$VUELTAS"); do
  api POST /presets/prueba-a/load; pausa "$SEG" "A: cerrado (vuelta $v)"
  api POST /presets/prueba-b/load; pausa "$SEG" "B: amplio (vuelta $v). ¿Se oye la diferencia? ¿el corte molesta?"
done
api POST /presets/prueba-a/load; pausa 3 "A otra vez, aunque ya estaba: el corte tiene que oírse igual"
api POST /presets/prueba-a/load; pausa 3 "A otra vez"
"$PROBE/grabar.sh" detener
# Vuelve a la instalación del archivo: Red y Black 0,15, Blue 0,55, traseros 12 ms.
api PATCH /global '{"rear_delay_ms": 12}'
api PATCH "/speakers/JBL%20Go%204%20Red" '{"ambience": 0.15}'
api PATCH "/speakers/JBL%20Go%204%20Black" '{"ambience": 0.15}'
api PATCH "/speakers/JBL%20Go%204%20Blue" '{"ambience": 0.55}'
api DELETE /presets/prueba-a; api DELETE /presets/prueba-b
estado
py "$PROBE/clics.py" "$WAV" "$REGISTRO" "$(cat "$WAV.inicio")" | tee "$WAV.clics.txt"
