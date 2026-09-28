# SuperMini nRF52840: controlador HCI

**Estado:** vacía. Se llena en E1 (i-7c8794-3f730a) y P2 (i-7c8794-cb208f).

## Plan

Todo lo que sigue es INFERIDO, salvo lo marcado.

- **Firmware base:** `bluekitchen/hci_uart_iso_timesync`. VERIFICADO: es un fork de
  Zephyr `hci_uart` para NCS ≥3.2.1 con la SoftDevice Controller.
  - Trae `CONFIG_BT_ISO_BROADCASTER=y` y H4 a 1 Mbaud con RTS/CTS.
  - Agrega el comando `LE Read ISO Clock` (OGF 0x3f, OCF 0x200), que sirve para el
    lazo de reloj.
  - Detalle en [08](../../docs/research/08-integracion-y-plan.md) §4.
- **Placa:** el target `promicro_nrf52840`, copiando la configuración de
  `nrf52840dongle_nrf52840.conf` ([roadmap](../../docs/roadmap.md), hardware).
- **Transporte hacia el PC:** USB CDC-ACM, que Bumble abre como
  `serial:/dev/cu.usbmodem*` en macOS y `serial:/dev/ttyACM*` en Linux.
  - En Linux, el usuario tiene que estar en el grupo `dialout` (`uucp` en Arch).
    Es el único cambio al sistema ([08](../../docs/research/08-integracion-y-plan.md) §2.1).
- **Para 4 BIS:** subir `CONFIG_BT_ISO_TX_BUF_COUNT` (5 por defecto).
- **Recuperación:** si se pierde el bootloader UF2, la Pico 2 W sirve de sonda SWD
  con `debugprobe_on_pico2.uf2` ([08](../../docs/research/08-integracion-y-plan.md) §3.1).

## Qué va a haber aquí

- Solo overlays y `prj.conf` propios, más un `west.yml` o una nota con el commit
  exacto del firmware base.
- **No se copia el código de NCS ni de Zephyr.**
