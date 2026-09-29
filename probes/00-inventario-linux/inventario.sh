#!/usr/bin/env bash
# Inventario del equipo Linux para i-7c8794-d9c834 y la parte de lectura de E1
# (i-7c8794-3f730a). Solo lee: no instala ni cambia nada.
#
# Uso:  probes/00-inventario-linux/inventario.sh > salida.txt
#
# `btmgmt info` es la lectura que decide E1 (busca `iso-broadcaster` en
# `supported settings`). Se lee sin root: el kernel acepta lecturas en un socket
# mgmt no privilegiado (medido en HP-O16, 2026-09-29). Va con `timeout` porque,
# después de imprimir, btmgmt se queda en modo interactivo.
set -uo pipefail

seccion() { printf '\n===== %s =====\n' "$1"; }
correr() { printf '\n$ %s\n' "$*"; "$@" 2>&1 || printf '(rc=%s)\n' "$?"; }

printf '# Inventario Linux — %s\n' "$(date -Is)"

seccion "Sistema"
correr uname -a
correr sh -c 'cat /etc/os-release'

seccion "Chip Bluetooth"
correr sh -c 'lspci -nn | grep -iE "network|wireless|bluetooth"'
correr sh -c 'lsusb | grep -i bluetooth'
correr sh -c 'journalctl -k -b | grep -iE "bluetooth|btusb" '

seccion "Topología: por dónde entra cada función de la tarjeta combo"
# El AX210 es una tarjeta que se conecta por PCIe pero expone dos interfaces: Wi-Fi
# por PCIe y Bluetooth por USB. Esto muestra a qué bus cuelga cada una.
correr sh -c 'for d in /sys/bus/usb/devices/*/; do
  [ -f "$d/idVendor" ] || continue
  case "$(cat "$d/idVendor"):$(cat "$d/idProduct")" in
    8087:*) echo "BT  $(cat "$d/idVendor"):$(cat "$d/idProduct")  $(cat "$d/speed") Mbps  $(readlink -f "$d")" ;;
  esac
done'
correr sh -c 'readlink -f /sys/class/bluetooth/hci0'
correr sh -c 'for d in /sys/bus/pci/devices/*; do
  [ "$(cat "$d/class")" = 0x028000 ] && echo "Wi-Fi  $(readlink -f "$d")"; true
done'
correr rfkill list

seccion "Versiones del stack"
correr bluetoothctl --version
correr pipewire --version
correr wireplumber --version
correr sh -c 'pacman -Q bluez bluez-utils bluez-libs liblc3 pipewire wireplumber 2>/dev/null'

seccion "Kernel: opciones de Bluetooth y socket ISO"
correr sh -c 'zgrep -E "^CONFIG_BT(_LE|_ISO|=)" /proc/config.gz'
correr sh -c 'grep -c iso /proc/net/protocols'
correr sh -c 'cat /proc/net/protocols | grep -iE "^(ISO|L2CAP|SCO)"'

seccion "Controlador según BlueZ (sin root)"
correr bluetoothctl show
correr sh -c 'grep -nE "^[^#]*(Experimental|KernelExperimental)" /etc/bluetooth/main.conf'

seccion "Capacidades mgmt — la lectura que decide E1"
correr timeout 5 btmgmt info

seccion "PipeWire: BAP y LC3"
correr sh -c 'ls /usr/lib/spa-0.2/bluez5/'
correr sh -c 'ldconfig -p | grep -i lc3'

seccion "Python y hatch"
correr python3 --version
correr sh -c 'hatch --version'
