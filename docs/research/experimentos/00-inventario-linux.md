# 00 · Inventario del equipo Linux

**Pregunta:** ¿este equipo Linux puede hacer E1–E4 con su chip Bluetooth interno,
sin comprar nada? (i-7c8794-d9c834, y la parte de lectura de i-7c8794-3f730a).

Es la mitad que faltaba del inventario: la otra está en
[00-inventario-mac.md](00-inventario-mac.md).

**Entorno:**
- PC de escritorio `PC-Ryzen5`, x86_64, **CachyOS** (Arch), kernel **7.2.7-1-cachyos**.
- Chip Bluetooth: **Intel AX210** (Typhoon Peak), una tarjeta combo que se conecta
  **físicamente por PCIe** pero que expone **dos interfaces separadas**: el Wi-Fi 6E
  por PCIe (`8086:2725`) y **el Bluetooth por USB** (`8087:0032`).
- BlueZ **5.87**, PipeWire **1.6.9**, WirePlumber **0.5.17**, liblc3 **1.1.3**.
- `python3` es **3.14.7**; hatch **1.16.2** en `~/.local/bin`.
- Placa **MSI B450M PRO-VDH MAX (MS-7A38)**, AMD Ryzen 5.
- Fecha: 2026-09-28.
- No hay adaptadores Bluetooth USB externos conectados.

**Datos crudos:** [datos/00/linux-inventario.txt](datos/00/linux-inventario.txt),
generado por `probes/00-inventario-linux/inventario.sh` (solo lectura).

**Qué se ejecutó:**
```bash
probes/00-inventario-linux/inventario.sh > docs/research/experimentos/datos/00/linux-inventario.txt
PY=python3.14 scripts/check.sh
```

## Resultado

### El chip: MEDIDO
| Campo | Valor |
|---|---|
| Modelo | **Intel AX210** (`8087:0032`, Typhoon Peak) |
| Transporte del Bluetooth | **USB full-speed (12 Mbps)**, driver `btusb` → `hci0` |
| Transporte del Wi-Fi | PCIe `26:00.0` (`8086:2725`), driver `iwlmvm` |
| Firmware cargado | `intel/ibt-0041-0041.sfi`, **Firmware Version 202-5.26** |
| Timestamp del firmware | **2026.5**, buildtype 1, build 82122, SHA1 `0x2925677d` |
| DDC | `intel/ibt-0041-0041.ddc`, aplicado |
| HCI Version que reporta BlueZ | `0x0d` (13) = **Bluetooth 5.4** |
| Dirección | `D4:AB:61:4B:98:07` |
| MGMT del kernel | ver 1.23 |

**La tarjeta es PCIe, pero su función Bluetooth es USB.** Es la arquitectura normal
del AX210: Wi-Fi por PCIe y Bluetooth por USB, dos interfaces en la misma placa. Lo
que se mide: el dispositivo `8087:0032` cuelga del **controlador USB del chipset
AMD** (`[1022:43d5]`), en el puerto `1-6`, y no de un puente PCIe de la tarjeta:

```
/sys/devices/pci0000:00/0000:00:02.1/0000:16:00.0/usb1/1-6   ← Bluetooth (btusb)
/sys/devices/pci0000:00/…/0000:26:00.0                        ← Wi-Fi (iwlmvm)
```

Entonces hay un camino USB físico aparte del PCIe. Como la placa **no tiene ranura
M.2 key E**, lo más probable es que la tarjeta sea un adaptador PCIe con un módulo
M.2 y un cable al header USB de la placa. INFERIDO: se confirma abriendo el equipo.

**Esto es lo bueno para la opción A:** Bumble llega a un controlador USB con
`hci-socket:0` deteniendo BlueZ ([05](../05-opcion-a-bumble.md) §7). En el Mac es
imposible, porque ahí el chip Bluetooth **sí** va por PCIe y es propiedad del sistema
([00-inventario-mac.md](00-inventario-mac.md)). El ancho de banda full-speed (12
Mbps) no es un límite para audio Bluetooth, que no pasa de ~1 Mbps. INFERIDO.

**HCI Version 5.4 es una capacidad del controlador, no del firmware de LE Audio.**
Un controlador 5.4 puede no exponer `iso-broadcaster`: es exactamente lo que
reportan de este chip ([02](../02-le-audio-auracast-linux.md) §2, "el firmware solo
soporta CIS"). Lo decide `btmgmt info`.

### Advertising extendido: MEDIDO
`bluetoothctl show` reporta `SupportedSecondaryChannels: 1M, 2M, Coded` y
`MaxAdvLen 251`. **Hay advertising extendido y canal secundario 2M**, que es el
requisito de radio que descalificó a la Pico 2 W
([08](../08-integracion-y-plan.md) §3.1). También hay 12 instancias de advertising
(`SupportedInstances: 0x0c`).

### El socket ISO del kernel: MEDIDO
- `CONFIG_BT_LE=y`, así que **el código ISO del kernel está compilado** (`iso.c` se
  construye con `BT_LE`).
- `/proc/net/protocols` **no lista ISO** (sí L2CAP y SCO). Eso es lo esperado con la
  bandera experimental apagada: el kernel registra el protocolo ISO recién cuando se
  habilita el feature experimental `6fbaf188-05e0-496a-9885-d6ddfdb4e03e`.
  **No es evidencia de que el chip no sirva.** INFERIDO.
- `/etc/bluetooth/main.conf` está **sin tocar**: `Experimental` y
  `KernelExperimental` siguen comentados (líneas 127 y 144). Este archivo es lo que
  hay que modificar en E1, y ese es el cambio al sistema que hay que anotar.

### PipeWire trae BAP: MEDIDO
`/usr/lib/spa-0.2/bluez5/` incluye **`libspa-codec-bluez5-lc3.so`**, además de
`hfp-lc3-swb` y `hfp-lc3-a127`. Con PipeWire 1.6.9 (la receta de Collabora usa
1.6.0) **el camino PipeWire de E3 está disponible sin compilar nada**.

`liblc3` 1.1.3 está en el sistema, así que `lc3py` de Bumble no es la única fuente
de LC3 en este equipo.

### El esqueleto del host corre en Linux: MEDIDO
`PY=python3.14 scripts/check.sh` pasa completo: `bundle.py verify`, los ids, la
lista cerrada de archivos, `hatch fmt --check` y los **9 tests en 0,47 s** (hatch
creó su entorno con Python 3.12.12). Eso cierra el pendiente de i-7c8794-f7f5b2.

Hizo falta **una corrección de portabilidad en `scripts/check.sh`**: la lista de
archivos permitidos se compara con la salida de `find … | sort`, y la colación del
locale en Linux ordena `cli.py` antes de `__init__.py`, al revés que en macOS. Ahora
usa `LC_ALL=C sort`.

### Capacidades mgmt (`iso-broadcaster`): NO MEDIDO todavía
**Corrección (2026-09-29):** no hacía falta root. `timeout 5 btmgmt info` se lee como
usuario normal; se midió en `HP-O16` ([00-inventario-hp-o16.md](00-inventario-hp-o16.md)).
Lo que sigue queda como se escribió.

`btmgmt info` necesita root y este equipo no tiene sudo sin contraseña. **Es la
lectura que decide E1** y queda pendiente.

**Cambio al sistema para poder leerlo, con su reversión:** se agrega
`/etc/sudoers.d/bluetooth-sync` con NOPASSWD acotado a `btmgmt`, `btmon`,
`bluetoothctl`, `dmesg` y `systemctl {start,stop,restart,status} bluetooth`.
**Se revierte con `sudo rm /etc/sudoers.d/bluetooth-sync`.**

### Toolchains instaladas (2026-10-07): MEDIDO
Lo que se instaló para dejar el equipo con todo el stack, cada cosa con cómo revertirla:

| Pieza | Cómo se instaló | Cómo se revierte |
|---|---|---|
| `rustup` 1.29.1 y la toolchain **1.99.0** (la fija `engine/rust-toolchain.toml`) | `sudo pacman -S --needed rustup && rustup default stable`; la 1.99.0 la bajó rustup sola | `sudo pacman -Rns rustup; rm -rf ~/.rustup ~/.cargo` |
| Motor en Rust (`aurasync_engine`) | `hatch run engine-build` en `host/` | se borra con el entorno de hatch (`hatch env remove`) |
| Dependencias de `host/web` y la PWA | `npm ci` y `npm run build:pwa` en `host/web` | `rm -rf host/web/node_modules host/web/dist-pwa` (los dos están en `.gitignore`) |
| `nrfutil` 8.2.1 | binario oficial de `files.nordicsemi.com` en `~/.local/bin`, más `nrfutil install sdk-manager` | `rm ~/.local/bin/nrfutil; rm -rf ~/.nrfutil` |
| **nRF Connect SDK v3.4.1** con su toolchain (13 GB) | `nrfutil sdk-manager install v3.4.1`, en `~/ncs`, sin sudo | `nrfutil sdk-manager uninstall v3.4.1`, o `rm -rf ~/ncs` |

Ya estaban antes y no se tocaron: Python 3.12.12 en hatch, `clang`, `elc3`/`dlc3` de liblc3,
`picotool` con su regla udev (`60-picotool.rules`), Node 26.10 / npm 12.2, Bumble 0.0.235 (es
dependencia del host) y los navegadores de Playwright (Chromium y Firefox). El usuario ya estaba en
`uucp`, lo que pide la SuperMini por `/dev/ttyACM*`.

Comprobado (kernel 7.2.9-1-cachyos):
- `PY=python3.14 scripts/check.sh` pasa: **1477 tests**, incluido el motor en Rust
  (`engine rust: available`).
- Las tres partes de `probes/17-e-s-nativa-rust` compilan con `cargo build --release`.
- `hatch run browser:test`: 251 bien, 2 saltados y 1 fallo,
  `test_the_pairing_qr_is_an_svg[firefox]`. Corrido solo, pasa 5 de 5 veces en los dos navegadores.
- La toolchain de NCS (`west` 1.5.0, `cmake` 4.2.1, `arm-zephyr-eabi-gcc` 14.3.0 del Zephyr SDK
  1.0.1, `dtc`) compila `zephyr/samples/bluetooth/hci_uart` para `promicro_nrf52840/nrf52840/uf2`
  (SoftDevice Controller, 19 % de flash, sale `zephyr.uf2`) y `nrf/samples/bluetooth/nrf_auraconfig`
  con `--sysbuild` para `nrf5340_audio_dk/nrf5340/cpuapp`.
- **No se flasheó nada:** no había ninguna placa nRF conectada.

Las herramientas de NCS no quedan en el `PATH`. Se usan desde
`nrfutil sdk-manager toolchain launch --ncs-version v3.4.1 --shell`.

**No se instaló, a propósito:**
- `arm-none-eabi-gcc` y el Pico SDK, para la Pico 2 W (P3): el usuario lo dejó para más adelante.
- J-Link de SEGGER, `nrfutil device` y las reglas udev de Nordic: solo se necesitan para
  `west flash` con un nRF5340 Audio DK. La SuperMini se flashea por UF2.

## Veredicto

- **El equipo Linux es el mejor candidato para E1–E4 que hay hoy**, y mejor que el
  Mac en dos puntos: el chip va por **USB** (Bumble puede tomarlo con
  `hci-socket:0`) y **PipeWire trae el códec LC3**, así que E3 puede probar los dos
  stacks como pide el roadmap.
- **La duda sigue siendo la misma y es del firmware del AX210, no del equipo:** si
  `btmgmt info` no muestra `iso-broadcaster`, este chip no puede transmitir y E1
  pasa a las SuperMini (d-7c8794-b82ee9) o a una tarjeta MT7921/BE200.
  **Cuidado con la opción de la tarjeta:** la placa es una **MSI B450M PRO-VDH MAX**,
  que no trae ranura M.2 para Wi-Fi, así que el AX210 está en una tarjeta PCIe (o en
  un adaptador PCIe que lleva un módulo M.2). Antes de comprar hay que **abrir el
  equipo y ver qué formato es**, y además confirmar que el candidato funcione en una
  placa AMD. Es una decisión con costo, así que es del usuario.
- **Qué no cambia:** nada de `docs/research/` se contradice. El inventario
  (i-7c8794-d9c834) queda **Hecho** en cuanto se lea `btmgmt info`; hasta entonces
  sigue **A medias**.
