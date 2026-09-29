#!/usr/bin/env bash
# E1, paso 1 (i-7c8794-3f730a): ¿qué declara el controlador?
#
# SOLO LEE. No cambia nada del sistema.
#
# Junta tres lecturas, de la más definitiva a la más indirecta:
#   1. los bits de LE Features del controlador (debugfs) — la respuesta real;
#   2. `btmgmt info`, que es lo que ve BlueZ (`supported settings`);
#   3. si el protocolo ISO está registrado en el kernel.
#
# Uso:  probes/e1-iso/01-capacidades.sh | tee salida.txt
set -uo pipefail
cd "$(dirname "$0")"

seccion() { printf '\n===== %s =====\n' "$1"; }
correr() { printf '\n$ %s\n' "$*"; "$@" 2>&1 || printf '(rc=%s)\n' "$?"; }

printf '# E1 paso 1 — capacidades del controlador — %s\n' "$(date -Is)"
correr uname -r
# "hci0:" para no traer el firmware del Wi-Fi, que va en la misma tarjeta.
correr sh -c 'journalctl -k -b | grep -E "hci0: Firmware (Version|timestamp)" | tail -2'

# Se prueba con un comando que la regla permite: `sudo -n true` fallaría siempre,
# porque `true` no está en la lista.
if ! sudo -n btmgmt info >/dev/null 2>&1; then
  echo
  echo "FALTA la regla de sudo. Sin ella no se puede leer nada de esto."
  echo "Ver probes/e1-iso/README.md."
  exit 1
fi

seccion "1. LE Features del controlador (la lectura que decide)"
features="$(mktemp)"
trap 'rm -f "$features"' EXIT
if sudo -n cat /sys/kernel/debug/bluetooth/hci0/features >"$features" 2>/dev/null; then
  cat "$features"
  echo
  python3 decodifica-le-features.py <"$features"
else
  echo "no se pudo leer /sys/kernel/debug/bluetooth/hci0/features"
  echo "(¿debugfs sin montar? probá: mount | grep debugfs)"
fi

seccion "2. Lo que ve BlueZ: btmgmt info"
# En 'supported settings' se buscan: iso-broadcaster, sync-receiver, cis-central,
# cis-peripheral (02 §2).
correr sudo -n btmgmt info
printf '\n-- las cuatro que importan --\n'
sudo -n btmgmt info 2>/dev/null | tr ' ' '\n' | grep -xE 'iso-broadcaster|sync-receiver|cis-central|cis-peripheral' | sort -u |
  sed 's/^/  presente: /' || true
for f in iso-broadcaster sync-receiver cis-central cis-peripheral; do
  sudo -n btmgmt info 2>/dev/null | grep -qw "$f" || echo "  AUSENTE:  $f"
done

seccion "3. ¿Está registrado el protocolo ISO en el kernel?"
# Aparece recién con KernelExperimental = 6fbaf188-… (lo activa 02-activar-iso.sh).
correr sh -c 'grep -iE "^(ISO|L2CAP|SCO)" /proc/net/protocols || echo "(ISO no registrado)"'
correr sh -c 'grep -nE "^[^#]*(Experimental|KernelExperimental)" /etc/bluetooth/main.conf || echo "(main.conf sin tocar)"'

seccion "Resumen"
echo "Si el bit 30 (Isochronous Broadcaster) está en NO, este chip no transmite y"
echo "E1 pasa a las SuperMini. Si el bit 28 (CIS Central) está en SI, se puede"
echo "probar unicast LE Audio con los Tune 770NC, que sí exponen PACS y ASCS"
echo "(experimentos/02-servicios-de-los-jbl-linux.md)."
