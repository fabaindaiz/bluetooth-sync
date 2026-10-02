#!/usr/bin/env bash
# Paso 5: una sesión que falla. Con el servicio sonando, la persona APAGA los tres Go 4.
# El servicio tiene que pasar a 'error', seguir respondiendo y quitar el sink.
# Después se prenden, se espera a que reconecten, y se arranca otra vez.
source "$(dirname "$0")/lib.sh"
esperar_sesion playing 2 || { echo "  primero el paso 2"; exit 1; }
anotar paso "5 · apagar los tres parlantes"
echo "  👉 APAGÁ LOS TRES GO 4 AHORA (hay 120 s)"
esperar_sesion error 120 || exit 1
estado
servicio_vivo && anotar paso "5 · el servicio sigue respondiendo"
"$PROBE/fuente.sh" detener
sleep 2
if nodo_aurasync; then echo "  ✗ el sink 'aurasync' quedó vivo"; else anotar paso "5 · el sink desapareció"; fi
echo "  👉 PRENDÉ LOS TRES GO 4 (hay 180 s para que reconecten)"
fin=$((SECONDS + 180))
while ((SECONDS < fin)); do
  n=$(pactl list short sinks | grep -c 'bluez_output.90_F2_60')
  ((n == 3)) && break
  sleep 2
done
((n == 3)) || { echo "  ✗ reconectaron $n de 3"; exit 1; }
sleep 3
api POST /session/start || exit 1
esperar_sesion playing 10 && "$PROBE/fuente.sh" && pausa 10 "¿suenan los tres otra vez?"
