# 00 · Inventario del Mac de desarrollo

**Pregunta:** ¿se pueden hacer en este Mac los experimentos E1–E4 del roadmap
(i-7c8794-d9c834, i-7c8794-3f730a)? Es decir: ¿el chip Bluetooth puede transmitir
Auracast, y Bumble puede llegar a él?

**Entorno:**
- MacBook con Apple Silicon (arm64), macOS 27.0 (build 26A428).
- Fecha: 2026-09-26.
- No hay adaptadores Bluetooth USB conectados.
- El equipo Linux es otro, y su inventario sigue pendiente.

**Datos crudos:** [datos/00/](datos/00/) contiene la salida completa de
`system_profiler SPBluetoothDataType`, con direcciones, y de `sw_vers`.

**Qué se ejecutó** (solo lectura; no se instaló ni se cambió nada):
```bash
sw_vers; uname -m
system_profiler SPBluetoothDataType
system_profiler SPUSBDataType SPUSBHostDataType
```
Además, se leyó la documentación de Bumble para macOS
(https://google.github.io/bumble/platforms/macos.html).

## Resultado

### Controlador interno: MEDIDO
| Campo | Valor |
|---|---|
| Chipset | **MTK_7932** (MediaTek) |
| Transporte | **PCIe** |
| Versión | HCI Revision 2600, LMP Subrevision 5106 |
| Servicios que reporta macOS | HFP, AVRCP, A2DP, HID, **LEA**, AACP, GATT, SerialPort, SCO |

- macOS declara **LEA (LE Audio)** para este controlador. Eso indica que el chip
  soporta ISO, pero **no confirma que pueda crear un BIG (iso-broadcaster)**, y
  macOS no lo expone. Queda INFERIDO.

### Parlantes emparejados con este Mac: MEDIDO
| Parlante | Product ID | Vendor ID (Device ID) | "Firmware" que reporta macOS |
|---|---|---|---|
| JBL Charge 6 (`78:66:F3:93:1D:B7`) | **0x20E3** | 0x0ECB | 1.0.0 |
| JBL Go 4 "de Fabi" (`90:F2:60:DA:66:6D`) | (no lo reporta) | (no lo reporta) | (no lo reporta) |
| **JBL Tune 770NC-LE** (audífonos, `88:92:CC:68:91:C0`) | 0x20B7 | 0x0ECB | 1.0.0 |

- La dirección del controlador del Mac es `5C:13:AC:0E:7D:17`.
- **Los JBL Tune 770NC-LE podrían servir como receptor de prueba.** El sufijo "LE"
  sugiere soporte LE Audio (INFERIDO). Si reciben Auracast estándar, sirven para
  validar un emisor propio (Bumble o nRF) antes de probar con los parlantes, que
  además exigen los datos de fabricante de JBL. No se verificó.

- El PID **0x20E3 del Charge 6 coincide** con el que documenta openjbl
  ([04](../04-implementaciones-y-stacks.md) §6). Eso confirma que openjbl se refiere
  a este mismo modelo.
- 0x0ECB es, probablemente, el vendor ID de Harman en el registro Device ID/USB
  (INFERIDO). No es el company ID del Bluetooth SIG (0x0057) que se usa en los
  datos de fabricante.
- Que la "versión 1.0.0" no sea la versión real del firmware es INFERIDO. openjbl
  reporta 3.0.7.1 para un Charge 6, así que **el firmware de cada medición hay que
  leerlo con la app JBL Portable o con openjbl**, no con macOS.

### ¿Bumble puede usar el controlador interno? No: VERIFICADO
La documentación de Bumble para macOS solo contempla **adaptadores USB**. Para
usarlos, hay que impedir que macOS tome el adaptador con este comando:
```bash
sudo nvram bluetoothHostControllerSwitchBehavior="never"
```
El controlador interno va por PCIe y es de macOS, así que Bumble no puede llegar a
él. En macOS no existe un equivalente al `hci-socket` de Linux.

## Veredicto

- **En este Mac no se pueden hacer E1–E4 con el chip interno.** Hace falta un
  adaptador USB externo o el equipo Linux.
- **El Mac sí sirve como estación de desarrollo para ambos caminos:**
  - **Opción A (Bumble):** lc3py trae binarios para macOS arm64
    ([05](../05-opcion-a-bumble.md) §4). Con un **dongle nRF52840 con `hci_uart`**,
    que Bumble usa por `serial:`, o con un dongle USB soportado, el Mac puede ser
    el emisor. En el caso de `serial:`, no debería hacer falta el cambio de `nvram`,
    porque macOS no toma un puerto serie como controlador (INFERIDO).
  - **Opción C (nRF5340):** macOS 26 es un sistema soportado para desarrollar con
    NCS ([06](../06-opcion-c-nrf5340.md) §7). Hay un problema conocido con el audio
    USB en macOS (OCT-2154), que no afecta a `nrf_auraconfig` porque este lee los
    archivos desde la SD.
- **Qué cambia:** aunque el equipo Linux no sirva, un dongle nRF52840
  (US$11.69) permitiría hacer E1–E4 desde este Mac.

## Lo que se puede probar en este Mac sin hardware nuevo (propuesto, sin ejecutar)

**E2 parcial con CoreBluetooth:**
1. Poner un Go 4 a transmitir Auracast.
2. Escanear desde el Mac con `bleak` en un entorno virtual temporal, y buscar
   anuncios con datos de fabricante 0x0057 terminados en `dffd`.

Lo que daría y lo que no:
- **Daría** los datos de fabricante exactos de cada modelo (Go 4 y Charge 6), lo
  que resuelve la duda de si el sufijo cambia entre modelos.
- **No daría** la BASE ni el BIGInfo, porque CoreBluetooth no se sincroniza a
  anuncios periódicos.
- **No se sabe** si CoreBluetooth en macOS entrega anuncios extendidos (INFERIDO).
  Si no los entrega, el resultado es "no visible", y eso también se anota.
