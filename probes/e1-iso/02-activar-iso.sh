#!/usr/bin/env bash
# E1, paso 2 (i-7c8794-3f730a): activar el socket ISO experimental de BlueZ.
#
# ESTO CAMBIA EL SISTEMA. Escribe /etc/bluetooth/main.conf con
# `Experimental = true` y `KernelExperimental = 6fbaf188-05e0-496a-9885-d6ddfdb4e03e`,
# y reinicia bluetoothd.
#
# SE REVIERTE CON:   probes/e1-iso/03-revertir.sh
#
# El original está guardado en main.conf.original, copiado del sistema el
# 2026-09-28 (md5 2bd6a06abf1b0a1d76c252098e00819e). El único cambio son esas dos
# líneas: `diff main.conf.original main.conf.experimental`.
set -euo pipefail
cd "$(dirname "$0")"
aqui="$(pwd)"

esperado=2bd6a06abf1b0a1d76c252098e00819e
actual="$(md5sum /etc/bluetooth/main.conf | cut -d' ' -f1)"
if [ "$actual" != "$esperado" ]; then
  echo "AVISO: /etc/bluetooth/main.conf no es el que se respaldó." >&2
  echo "  esperado (main.conf.original): $esperado" >&2
  echo "  en el sistema:                 $actual" >&2
  if [ "$actual" = "$(md5sum main.conf.experimental | cut -d' ' -f1)" ]; then
    echo "  → es main.conf.experimental: ya está activado, no hay nada que hacer." >&2
    exit 0
  fi
  echo "  Alguien lo editó, o una actualización de bluez lo cambió. Revisá el diff" >&2
  echo "  antes de seguir; si lo sobrescribís, se pierde ese cambio." >&2
  exit 1
fi

echo "== antes =="
grep -nE '^[^#]*(Experimental|KernelExperimental)' /etc/bluetooth/main.conf || echo "(sin tocar)"

echo
echo "== activando =="
sudo -n cp "$aqui/main.conf.experimental" /etc/bluetooth/main.conf
grep -nE '^[^#]*(Experimental|KernelExperimental)' /etc/bluetooth/main.conf
sudo -n systemctl restart bluetooth
sleep 2

echo
echo "== después =="
echo "-- bluetoothd --"
sudo -n systemctl status bluetooth | sed -n '1,6p'
echo
echo "-- ¿se registró el protocolo ISO en el kernel? --"
if grep -qiE '^ISO' /proc/net/protocols; then
  grep -iE '^(ISO|L2CAP|SCO)' /proc/net/protocols
  echo "   → SÍ. El socket ISO del kernel está disponible."
else
  echo "   → NO. El protocolo ISO sigue sin registrarse."
  echo "     Puede ser que el kernel no lo traiga, o que bluetoothd no aplicara el"
  echo "     feature. Mirá: journalctl -u bluetooth -b | tail -30"
fi
echo
echo "-- btmgmt info --"
sudo -n btmgmt info | tr ' ' '\n' | grep -xE 'iso-broadcaster|sync-receiver|cis-central|cis-peripheral' | sort -u |
  sed 's/^/   presente: /' || echo "   (ninguna de las cuatro)"

echo
echo "REVERTIR:  probes/e1-iso/03-revertir.sh"
