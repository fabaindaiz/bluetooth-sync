#!/usr/bin/env bash
# ¿Los JBL exponen BASS (0x184F) y PACS (0x1850)?
#
# De eso dependen los dos mecanismos por los que un receptor elige su BIS
# (02-le-audio-auracast-linux.md §4), y con ellos E4 (i-7c8794-eeac13) y el
# asistente BASS (i-7c8794-365524).
#
# NO NECESITA ROOT y no cambia nada: conecta, enumera GATT y desconecta.
#
# Uso:
#   probes/02-gatt-jbl/enumerar.sh                 # los cuatro conocidos
#   probes/02-gatt-jbl/enumerar.sh 90:F2:60:DA:66:6D
#
# EL PARLANTE TIENE QUE ESTAR ENCENDIDO. Conviene correrlo dos veces con cada
# Go 4: en reposo, y con el botón de Auracast activado, por si expone BASS solo
# entonces.
#
# Límite conocido: `bluetoothctl connect` no permite elegir el transporte. Estos
# parlantes están emparejados por BR/EDR, así que la conexión puede ir por ahí y
# GATT resolverse sobre BR/EDR. Si un parlante no muestra servicios LE Audio, eso
# NO cierra la pregunta: hay que repetirlo forzando LE (bleak, o Bumble con
# hci-socket y BlueZ detenido). Está anotado en
# docs/research/experimentos/02-servicios-de-los-jbl-linux.md.
set -uo pipefail

# Los UUID que decide este probe.
declare -A INTERES=(
  [0000184f]="BASS  · Broadcast Audio Scan — el asistente escribe BIS_Sync acá"
  [00001850]="PACS  · Published Audio Capabilities — trae Sink Audio Locations"
  [0000184e]="ASCS  · Audio Stream Control (unicast)"
  [00001844]="VCS   · Volume Control"
  [00001853]="CAS   · Common Audio"
  [00001855]="TMAS  · Telephony and Media Audio"
  [00001852]="Broadcast Audio Announcement"
  [0000184d]="MICS  · Microphone Control"
)

DISPOSITIVOS=("$@")
if [ ${#DISPOSITIVOS[@]} -eq 0 ]; then
  DISPOSITIVOS=(90:F2:60:DA:66:6D 90:F2:60:75:4A:83 78:66:F3:93:1D:B7 88:92:CC:68:91:C0)
fi

printf '# GATT de los JBL — %s\n' "$(date -Is)"
printf '# BlueZ %s · kernel %s\n' "$(bluetoothctl --version | awk '{print $2}')" "$(uname -r)"

for mac in "${DISPOSITIVOS[@]}"; do
  nombre="$(bluetoothctl info "$mac" 2>/dev/null | sed -n 's/^\s*Name: //p' | head -1)"
  printf '\n\n===== %s  (%s) =====\n' "$mac" "${nombre:-desconocido}"

  printf '\n-- conectando --\n'
  salida="$(printf 'connect %s\n' "$mac" | timeout 30 bluetoothctl 2>&1)"
  if printf '%s' "$salida" | grep -q 'Connection successful'; then
    echo "conectado"
  else
    printf '%s\n' "$salida" | grep -iE 'fail|error|not available|br-connection' | tail -3
    echo "NO se pudo conectar: ¿el parlante está encendido y a la vista?"
    echo "(se sigue con el siguiente; una conexión fallida también es un dato)"
    continue
  fi
  sleep 3

  printf '\n-- atributos GATT --\n'
  atributos="$(printf 'menu gatt\nlist-attributes %s\nquit\n' "$mac" | timeout 30 bluetoothctl 2>&1 |
    sed 's/\x1b\[[0-9;]*m//g')"
  printf '%s\n' "$atributos" | grep -iE 'service|characteristic|uuid|^\s*[0-9a-f]{8}-' || echo "(sin atributos)"

  printf '\n-- servicios LE Audio presentes --\n'
  hallado=0
  for uuid in "${!INTERES[@]}"; do
    if printf '%s' "$atributos" | grep -qi "$uuid-0000-1000-8000-00805f9b34fb"; then
      printf '  SI  %s  %s\n' "$uuid" "${INTERES[$uuid]}"
      hallado=1
    fi
  done
  [ "$hallado" -eq 0 ] && echo "  ninguno"

  printf '\n-- los dos que deciden --\n'
  for uuid in 0000184f 00001850; do
    if printf '%s' "$atributos" | grep -qi "$uuid-0000-1000-8000-00805f9b34fb"; then
      printf '  PRESENTE: %s  %s\n' "$uuid" "${INTERES[$uuid]}"
    else
      printf '  ausente:  %s  %s\n' "$uuid" "${INTERES[$uuid]}"
    fi
  done

  printf '\n-- UUID que cachea BlueZ --\n'
  bluetoothctl info "$mac" 2>/dev/null | grep -E 'UUID|Modalias'

  printf '\n-- desconectando --\n'
  printf 'disconnect %s\n' "$mac" | timeout 20 bluetoothctl 2>&1 | grep -iE 'successful|fail' | tail -1
  sleep 2
done

printf '\n\n== Qué hacer con esto ==\n'
echo "Si ningún parlante muestra BASS ni PACS, las dos vías del estándar para"
echo "asignar un canal por parlante están cerradas, y hay que repetirlo forzando LE"
echo "antes de concluir. El resultado va a"
echo "docs/research/experimentos/02-servicios-de-los-jbl-linux.md."
