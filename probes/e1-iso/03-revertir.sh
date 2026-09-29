#!/usr/bin/env bash
# Revierte 02-activar-iso.sh: devuelve /etc/bluetooth/main.conf a como estaba y
# reinicia bluetoothd. Verifica por md5 que quedó idéntico al original.
set -euo pipefail
cd "$(dirname "$0")"
aqui="$(pwd)"

original=2bd6a06abf1b0a1d76c252098e00819e

echo "== antes =="
md5sum /etc/bluetooth/main.conf
grep -nE '^[^#]*(Experimental|KernelExperimental)' /etc/bluetooth/main.conf || echo "(sin tocar)"

echo
echo "== revirtiendo =="
sudo -n cp "$aqui/main.conf.original" /etc/bluetooth/main.conf
sudo -n systemctl restart bluetooth
sleep 2

echo
echo "== después =="
actual="$(md5sum /etc/bluetooth/main.conf | cut -d' ' -f1)"
md5sum /etc/bluetooth/main.conf
if [ "$actual" = "$original" ]; then
  echo "   → main.conf quedó byte a byte como estaba."
else
  echo "   → ATENCIÓN: no coincide con el original ($original)." >&2
  exit 1
fi
grep -nE '^[^#]*(Experimental|KernelExperimental)' /etc/bluetooth/main.conf || echo "   (sin líneas activas, correcto)"
sudo -n systemctl status bluetooth | sed -n '1,4p'

echo
echo "Queda por quitar, cuando se termine con los experimentos:"
echo "  sudo rm /etc/sudoers.d/bluetooth-sync"
