# 22 · Escuchar a los JBL: decodificador de capturas

**Sonda desechable** (d-7c8794-3208b7): se borra cuando su resultado quede escrito en
`docs/research/experimentos/`. No es código de producto. El diseño y lo que se sabe está en
[research/02 §8](../../docs/research/02-le-audio-auracast-linux.md).

**No toca hardware.** `jbl_decode.py` solo lee un archivo. `capturar.sh` sí abre una SuperMini,
pero solo cuando alguien lo corre a mano, con el puerto que elija.

| Archivo | Qué hace |
|---|---|
| `jbl_decode.py` | Lee una captura y saca una línea JSON por evento: datos de fabricante de Harman (y cada **cambio**), Broadcast_ID, PBP, BASE (canal por BIS, presentation delay), BIGInfo, `CONNECT_IND`, y un resumen por anunciante (incluido el intervalo medido del tren periódico) |
| `test_jbl_decode.py` | Pruebas sin hardware. Cruza los formatos contra Bumble (BASE y eventos HCI) y contra las máscaras del BIGInfo de Wireshark |
| `capturar.sh` | `hci`: una SuperMini con `hci_uart_iso` y `bumble-auracast scan`, guardando todo el HCI en btsnoop. `nrf`: el nRF Sniffer en una SuperMini, sin la interfaz de Wireshark |

## Formatos que lee

- pcap clásico con DLT **272** (nRF Sniffer), **201** (`pcapsnoop` de Bumble) o **187** (H4).
- btsnoop H4 (`BUMBLE_SNOOPER=btsnoop:file:…`, o el registro HCI de Android).
- Un pcapng (lo que guarda Wireshark) se convierte antes: `editcap -F pcap in.pcapng out.pcap`.

## Uso

```bash
PY=~/.local/share/hatch/env/virtual/aurasync/RNX04zMz/aurasync/bin/python
cd probes/22-sniffer-jbl
$PY -m unittest -v test_jbl_decode            # sin hardware
$PY jbl_decode.py captura.pcap > eventos.jsonl  # --all para ver también lo que no es JBL ni Auracast
```

Los nombres de campo que vienen del experimento 01 (`hyp_broadcasting`, `hyp_stereo_group`,
`byte2_color_hypothesis`) son **hipótesis**: la sonda siempre entrega también los bytes crudos.
