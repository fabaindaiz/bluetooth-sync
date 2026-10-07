# SuperMini nRF52840: controlador HCI

**Estado (2026-10-07):** `hci_uart_iso/` es una app de Zephyr que compila el `main.c` del sample
`hci_uart` **desde el SDK** (no se copia) junto con `src/uf2_touch.c`, sobre NCS v3.4.1:
- `iso.conf`: emisor y receptor ISO en una sola imagen, con el **cristal** de 32 kHz;
- `rc.conf`: para placas sin cristal (RC calibrado, 500 ppm);
- `uf2_touch.c`: si el PC pone el puerto a 1200 baudios (`stty -F /dev/ttyACMn 1200`), la placa entra al
  bootloader UF2 sin doble reset (`GPREGRET=0x57`).

La versión sin `uf2_touch.c` funcionó en las placas A y C. La placa C tiene cristal y la A no se probó
([experimentos/21](../../docs/research/experimentos/21-f1-iso-en-la-supermini.md)). Falta P2
(i-7c8794-cb208f).

```bash
cd ~/ncs/v3.4.1
nrfutil sdk-manager toolchain launch --ncs-version v3.4.1 -- west build -p always \
  -b promicro_nrf52840/nrf52840/uf2 <repo>/firmware/supermini/hci_uart_iso -d <build> -- \
  -DEXTRA_CONF_FILE=iso.conf          # sin cristal: "-DEXTRA_CONF_FILE=iso.conf;rc.conf"
# grabar: copiar <build>/hci_uart_iso/zephyr/zephyr.uf2 al disco NICENANO
```

## Plan (revisado el 2026-10-07 con la validación de [research/02](../../docs/research/02-le-audio-auracast-linux.md) §7)

- **Firmware base:** el sample `hci_uart` de NCS con la SoftDevice Controller. ISO Broadcaster está
  soportado en el nRF52840 desde **NCS v2.6.0** (VERIFICADO); se fija una versión ≥ v3.2.1 y su commit.
  **El fork `bluekitchen/hci_uart_iso_timesync` no hace falta:** para alinear los BIS alcanza con el VS
  `ISO Read TX Timestamp` (0xfd17) o `LE Read ISO TX Sync` (VERIFICADO en `sdc_hci_vs.h`). Además, el fork
  no soporta el nRF52840 (portarlo parece fácil, INFERIDO).
- **Placa:** `promicro_nrf52840`. **Ya trae CDC-ACM como UART del HCI** (VERIFICADO): no se copia la conf
  del dongle ni un overlay.
- **Kconfig propio**, porque el `prj.conf` de `hci_uart` no activa ISO:
  - `CONFIG_BT_ISO_BROADCASTER=y` y la configuración de broadcaster de la SDC;
  - `CONFIG_BT_CTLR_PHY_2M=y`;
  - **búferes ISO para 4 SDU cada 10 ms** (VERIFICADO que vienen chicos): `CONFIG_BT_ISO_TX_BUF_COUNT`
    vale **1** por defecto (subirlo a 8–10), `CONFIG_BT_CTLR_SDC_ISO_TX_HCI_BUFFER_COUNT` vale 3 (subirlo a
    8–10), y `CONFIG_BT_ISO_TX_MTU` ≥ 120.
- **Sin cifrado:** el nRF52840 no cifra ISO (VERIFICADO), así que el BIG va sin Broadcast Code.
- **Reloj de 32 kHz:** antes de nada, ver si la placa trae cristal (inspección y un arranque con
  `K32SRC_XTAL`). Si no lo trae, `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC` con calibración, declarado a 500 ppm.
  Se anota en cada medición.
- **Transporte hacia el PC:** USB CDC-ACM, que Bumble abre como `serial:/dev/ttyACM*`. El usuario ya está
  en `uucp` y ModemManager está inactivo en `HP-O16` (verificado el 2026-10-07). Por UART hacia la Pico:
  subir de 115200 a 1 Mbaud con RTS/CTS.
- **Recuperación:** la Pico 2 W como sonda SWD (`debugprobe_on_pico2.uf2` v2.3.1), con `nrf52_recover`
  de OpenOCD.

## Qué va a haber aquí

- Solo overlays y `prj.conf` propios, más un `west.yml` o una nota con el commit
  exacto del firmware base.
- **No se copia el código de NCS ni de Zephyr.**
