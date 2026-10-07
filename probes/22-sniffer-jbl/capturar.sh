#!/usr/bin/env bash
# Captures for experiment 22 (throwaway probe, d-7c8794-3208b7). Nothing here runs by itself:
# each mode opens a SuperMini that the person running it chose, and only that one.
#
#   capturar.sh hci <port> <seconds> <out.btsnoop>   # HCI observer: a SuperMini with hci_uart_iso
#   capturar.sh nrf <port> <seconds> <out.pcap>      # nRF Sniffer 4.1.1 on a SuperMini, no Wireshark GUI
#
# <port> is /dev/ttyACMn, never a /dev/serial/by-id/... path: the nRF extcap splits its interface
# name on '-' (nrf_sniffer_ble.py, sniffer_capture) and a by-id path has dashes.
set -euo pipefail

HOST_PY="${HOST_PY:-$HOME/.local/share/hatch/env/virtual/aurasync/RNX04zMz/aurasync/bin}"
EXTCAP="${EXTCAP:-$HOME/.local/lib/wireshark/extcap/nrf_sniffer_ble.py}"

usage() { sed -n '5,6p' "$0" >&2; exit 2; }
[ $# -eq 4 ] || usage
mode="$1" port="$2" seconds="$3" out="$4"
case "$port" in /dev/ttyACM[0-9]*) ;; *) echo "port must be /dev/ttyACMn" >&2; exit 2 ;; esac
[ -e "$out" ] && { echo "$out already exists" >&2; exit 2; }

case "$mode" in
  hci)
    # Bumble writes every HCI packet to a btsnoop file (bumble/transport/__init__.py, BUMBLE_SNOOPER).
    # `auracast scan` syncs to each broadcast's periodic advertising: BASE, BIGInfo, PBP, Broadcast_ID.
    BUMBLE_SNOOPER="btsnoop:file:$out" timeout -s INT "$seconds" \
      "$HOST_PY/bumble-auracast" scan "serial:$port" || true
    ;;
  nrf)
    [ -f "$EXTCAP" ] || { echo "nRF extcap not installed at $EXTCAP (see README.md)" >&2; exit 1; }
    # Headless capture: scan every advertiser, following AUX pointers and scan responses. Following one
    # device (its connection or its periodic train) needs the Wireshark toolbar. "-4.6" is the extcap
    # API version the script expects after the port; >= 3.4 selects the sniffer's protocol 3.
    # SIGINT, so the script's `finally` stops the sniffer cleanly. Its headless loop busy-waits.
    timeout -s INT "$seconds" python3 "$EXTCAP" --capture --extcap-interface "$port-4.6" \
      --fifo "$out" --scan-follow-aux --scan-follow-rsp || true
    ;;
  *) usage ;;
esac
ls -l "$out"
echo "decode: $HOST_PY/python $(dirname "$0")/jbl_decode.py $out"
