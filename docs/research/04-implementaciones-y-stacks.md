# Implementaciones existentes, lenguajes y stacks

Investigación del 2026-09-25. Es la cuarta línea de la fase de investigación (ver
[README](README.md)). El lenguaje, la licencia y la última actividad de cada
repositorio se revisaron en el propio repositorio (API de GitHub o GitLab, archivo
LICENSE, árbol de código, PyPI, crates.io).

**Qué significa cada marca:**
- **VERIFICADO**: leído en el repositorio o en su documentación.
- **REPORTADO**: dicho por terceros.
- **INFERIDO**: deducción que nadie comprobó.

"Actividad" es la fecha del último push o commit.

## Resumen

- **Sí, predomina C en las capas de protocolo.** BlueZ, PipeWire, Zephyr, NimBLE,
  BTstack, el SDK de Nordic y ESP-IDF están en C.
- **Las herramientas que de verdad se usan para hablar con parlantes JBL o para
  experimentar con Auracast son sobre todo Python**: Bumble, openjbl, Sendspin y
  los scripts de Collabora.
- Rust aparece solo en DSP (CamillaDSP), en HCI crudo (TrouBLE) y en una
  herramienta para JBL. No hay capa de perfiles LE Audio en Rust.
- **Para el prototipo hay cuatro opciones realistas.** La más rápida para el
  experimento decisivo es Python + Bumble + liblc3. La que requiere menos código es
  BlueZ + PipeWire, que es casi solo configuración (detalle al final).
- **Hallazgos nuevos:**
  - Hay proyectos que **hicieron ingeniería inversa del protocolo de JBL**:
    openjbl (Python, Charge 6 confirmado) y jbl-aura-play-together, que documenta
    el comando `SET_AURACAST_BROADCAST` y el servicio `DFFD`.
  - Existe un **kit para capturar transmisiones Auracast**, útil para ver qué
    transmite un JBL (experimento E2).

## 1. Stacks de host Bluetooth

| Nombre | Capa | Lenguaje | Licencia | Actividad | Relevancia y datos clave | Marca | URL |
|---|---|---|---|---|---|---|---|
| **BlueZ** | Demonio de host en Linux + sockets ISO del kernel | C | GPL-2.0 | 2026-09-26, versiones hasta 5.87 | Tiene `profiles/audio/bap.c` y `bass.c`. Collabora logró que el Go 4 aceptara el stream con `bluetoothctl advertise.manufacturer 87 … 0xdf 0xfd`. Necesita `Experimental=true` y el UUID del socket ISO en `KernelExperimental` | VERIFICADO | https://github.com/bluez/bluez · https://www.collabora.com/news-and-blog/blog/2026/05/05/bluez-powered-auracast-broadcasting-on-genio-700/ |
| **Collabora auracast-demo** | Configuración y scripts | Python/Shell | no se indica | 2026-03-06 | Kernel 6.18.5, BlueZ 5.86, PipeWire 1.6.0, WirePlumber 0.5.13. **1 BIS mono** (`48_2_2`, allocation 0). Usa Bumble solo para escanear | VERIFICADO | https://gitlab.collabora.com/showcases/embedded-world-2026/auracast-demo |
| **Google Bumble** | Host completo en espacio de usuario; toma el control del controlador | Python ≥3.10 | Apache-2.0 | 2026-09-25 (0.0.235, todavía antes de 1.0) | `bumble-auracast transmit` tiene `--manufacturer-data`. Junta los canales de todas las fuentes en **un solo BIG** (`num_bis=channel_count`). Ver sus límites en la opción (a) | VERIFICADO | https://github.com/google/bumble/blob/main/apps/auracast.py |
| **BTstack** (BlueKitchen) | Host embebido y portable (tiene port libusb para Linux) | C | Estilo BSD, pero **la cláusula 4 limita el uso gratis a lo no comercial** | 2026-09-22 | El código público solo trae `le_audio_broadcast_source_lite.c` (`MAX_NUM_BIS 4`, usa liblc3). El README dice que para acceder a LE Audio hay que contactarlos | VERIFICADO | https://github.com/bluekitchen/btstack |
| **Apache NimBLE** | Host y controlador para RTOS | C | Apache-2.0 | 2026-09-18 | `nimble/host/audio` tiene broadcast source, BASS y Auracast. El ejemplo `apps/auracast` recibe audio USB y emite LC3 a 48 kHz con `AURACAST_CHAN_NUM: 2` | VERIFICADO | https://github.com/apache/mynewt-nimble |
| **Zephyr** | Host y controlador para RTOS | C | Apache-2.0 | 2026-09-25 | Ejemplos: `bap_broadcast_source`, `pbp_public_broadcast_source`, `tmap_bms`, `iso_broadcast`. Corre en nRF5340 o en un host Linux "con BlueZ" | VERIFICADO | https://github.com/zephyrproject-rtos/zephyr/tree/main/samples/bluetooth/audio |
| Android Bluetooth / Floss | Fluoride en C++ con una capa D-Bus en Rust | C++/Rust | Apache-2.0 (no revisado) | ? | Tiene `btif_le_audio_broadcaster.cc`. No se sabe si la transmisión funciona con Floss en Linux de escritorio | REPORTADO/INFERIDO | https://chromeos.dev/en/posts/androids-bluetooth-stack-fluoride-comes-to-chromeos |
| ESP-IDF ESP-BLE-AUDIO / ISO | SDK de chip (host derivado de Zephyr, distribuido como biblioteca binaria) | C | Apache-2.0 | 2026-09-25 | Solo hay documentación para **ESP32-H4 y ESP32-S31**; las de S3, C3, C5, C6, C61, H2 y P4 dan 404. ISO está en "preview". sskoog no encontró BIG/BIS en C6 ni S3 | VERIFICADO | https://docs.espressif.com/ (esp32s31, esp-ble-iso) · https://github.com/espressif/esp-ble-audio-lib |
| BlueR | Bindings en Rust sobre el D-Bus de BlueZ | Rust | BSD-2 | 2026-08-17 | GATT, L2CAP, RFCOMM, mesh. **Sin ISO ni LE Audio** | VERIFICADO | https://github.com/bluez/bluer |
| TrouBLE + bt-hci | Host en Rust; tipos HCI con transporte Linux, USB y serial | Rust | Apache-2.0 (bt-hci también MIT) | 2026-09-25 / 2026-08-26 | `host/src/iso.rs` solo expone comandos y paquetes ISO crudos. No tiene capa BAP, BASE ni LC3 | VERIFICADO | https://github.com/embassy-rs/trouble |

## 2. Servidores de audio

| Nombre | Lenguaje | Licencia | Actividad | Datos | Marca |
|---|---|---|---|---|---|
| **PipeWire, plugin bluez5** | C | MIT (algunas partes LGPL/GPL) | 1.6.9, 2026-09-17 | Roles `bap_bcast_source` y `bap_bcast_sink`; archivos `iso-io.c` y `bap-codec-lc3.c`. `bluez5.bcast_source.config` acepta **una lista de BIS, cada uno con su `audio_channel_allocation`**, y `adapter` para tener varios BIGs. **No encontré una clave para los datos de fabricante**: habría que ponerlos del lado de BlueZ | VERIFICADO (https://gitlab.freedesktop.org/pipewire/pipewire, `doc/dox/config/pipewire-props.7.md`) |
| WirePlumber | C 80% / Lua 19% | MIT | 2026-09-25 | Gestor de sesión; lleva la configuración de la transmisión | VERIFICADO |
| BlueALSA | C | MIT | 2026-09-14 | En LE solo soporta BLE-MIDI. El README dice que los demás perfiles de audio BLE "not (yet) supported". **Solo sirve para el camino A2DP** | VERIFICADO (https://github.com/arkq/bluez-alsa) |
| PulseAudio | C | LGPL-2.1 | 2026-09-25 | El módulo Bluetooth tiene códecs A2DP y HFP; no tiene BAP ni LC3 | VERIFICADO |

## 3. Códecs

| Nombre | Lenguaje | Licencia | Datos | Marca |
|---|---|---|---|---|
| **google/liblc3** | C (con wrapper Python en `python/`) | Apache-2.0 | Último push 2026-09-09 | VERIFICADO |
| lc3py (PyPI) | Python | Apache-2.0 | Versión 1.1.3. Binarios solo para **manylinux x86_64 y macOS arm64**. El extra `auracast` de Bumble solo lo instala en esas plataformas, así que **en una Raspberry Pi (aarch64) hay que compilarlo** | VERIFICADO (https://pypi.org/pypi/lc3py, `pyproject.toml` de Bumble) |
| lc3-codec (ninjasource) | Rust, no_std | crates.io 0.2.0 | Último commit en 2023-07. `lc3-sys` es un binding FFI de 2024 | VERIFICADO |
| LC3plus (ETSI TS 103 634) | C ANSI de referencia dentro de la especificación | Patentes de Fraunhofer, €0.07 por unidad | **No es el LC3 que usa BAP, así que no sirve para Auracast** | REPORTADO (PDF de licencias de LC3plus en iis.fraunhofer.de) |

## 4. Emisores embebidos

| Nombre | Lenguaje | Licencia | Datos | Marca |
|---|---|---|---|---|
| nRF Connect SDK `applications/nrf_audio` (antes `nrf5340_audio`) | C | El código es LicenseRef-Nordic-5-Clause (solo chips Nordic). La app tiene **LicenseRef-PCFT** (Packetcraft, solo nRF53, "must not be … modified") | Emisor y receptor de transmisiones | VERIFICADO (https://github.com/nrfconnect/sdk-nrf) |
| `samples/bluetooth/nrf_auraconfig` | C | Nordic-5-Clause | Emisor configurable por shell, "maximum two BIG with four BIS streams each", solo para el nRF5340 Audio DK. **El código no tiene campo para datos de fabricante**, así que para los JBL habría que parchearlo | VERIFICADO (la necesidad del parche es INFERIDA) |
| sskoog/ble_audio (ahora `audio_multicast`) | C (ESP-IDF 6.0.2) | AGPL-3.0 | **Dejó BLE y pasó a ESP-NOW** porque C6 y S3 no tienen BIG/BIS. Conserva una app antigua `usb_ble_bumble` | VERIFICADO |
| NimBLE `apps/auracast` | C | Apache-2.0 | Ver sección 1 | VERIFICADO |

## 5. Sincronización multiparlante y DSP

| Nombre | Lenguaje | Licencia | Actividad | Cómo funciona | Marca |
|---|---|---|---|---|---|
| **Snapcast** | C++ | GPL-3.0 | 2026-06-27 | Los clientes sincronizan su reloj con el servidor todo el tiempo, con latencia por cliente vía JSON-RPC. Solo por Wi-Fi, no por Bluetooth | VERIFICADO |
| **CamillaDSP** | Rust | GPL-3.0 | 2026-09-25 | Mezclador y filtro de retardo por canal; backends ALSA, PipeWire y Jack | VERIFICADO |
| google/audio-sync-kit | Python | Apache-2.0 | **Archivado en 2018** | Mide la latencia entre señales grabadas; hecho para multiroom de Chromecast | VERIFICADO |
| Zigazou/hyperboom-duo-stereo | Bash | GPL-3.0 | 2024-12 | Sink virtual de PipeWire dividido entre 2 parlantes A2DP; sin mediciones | VERIFICADO |
| trudenboy/sendspin-bt-bridge | Python | MIT | 2026-09-25 | Un subproceso A2DP por parlante, sincronizados con Sendspin de Music Assistant; corre en una Pi. El "guided delay tuning" todavía está en su roadmap | VERIFICADO |
| AbhiCollegeWork/chorus-speaker-sync | PowerShell (Windows, Voicemeeter) | MIT | 2026-09-23 | Tren de clics → micrófono → detección de inicio → retardo por salida. **La calibración automática aún no está construida**; verificado con 2 parlantes BT | VERIFICADO |
| chicco-carone/sync-test | ? | ? | 2026-09-20 | Correlación cruzada con refinamiento bajo la muestra, sobre PipeWire | REPORTADO (fragmento de búsqueda) |
| shairport-sync | C/C++ | según el archivo | 2026-09-25 | Reloj NQPTP/PTP con relleno de muestras o remuestreo soxr | VERIFICADO |
| Squeezelite | C | GPL-3.0 | 2026-08-18 | Sincronización del lado cliente con LMS | VERIFICADO (solo la licencia) |
| SoundSeeder | — | propietaria | — | App de sincronización por Wi-Fi | REPORTADO (soundseeder.com) |

## 6. Proyectos específicos de JBL

| Nombre | Lenguaje | Licencia | Datos | Marca |
|---|---|---|---|---|
| **NiceDayZc/openjbl** | Python | MIT | Protocolo EQ de JBL Portable por GATT/SPP, sacado del APK 6.9.12. **Charge 6 confirmado en hardware**: PID `20E3`, firmware 3.0.7.1. **No trata Auracast** | VERIFICADO |
| **SongJunguo/jbl-aura-play-together** | Rust + Bash/Python (BlueZ) | MIT | Enlace entre Authentics 300 y Aura Studio 5. Documenta el comando OneOS `7957`, `SET_AURACAST_BROADCAST`, el token `0x3c` (estado de Auracast) y el **servicio `DFFD`**. Probado con BlueZ 5.64 | VERIFICADO |
| jklingberg/partybox-companion | ? | MIT | Control BLE del PartyBox, con notas de ingeniería inversa en `docs/`; Pi + A2DP | VERIFICADO (licencia y actividad) |
| pembem22/connect-plus | Android | GPL-3.0 | Trabajo antiguo sobre estéreo Connect+ y firmware; último commit 2025-04 | VERIFICADO |
| **auracast-research/auracast-hackers-toolkit** | C (fork de Zephyr, dongle nRF52840) | no se indica | Captura de BIS y secuestro de BIS. Sirve para **ver qué transmite de verdad un JBL o un teléfono** | VERIFICADO |

**Relación con los datos de fabricante:**
- El sufijo `dffd` del valor que usa Bumble (`87:…dffd`), el `0xdf 0xfd` de
  Collabora y el servicio `DFFD` de jbl-aura-play-together **apuntan al mismo
  identificador de JBL**. La coincidencia es INFERIDA.
- Con eso, el comando `SET_AURACAST_BROADCAST` podría permitir ordenar a un JBL
  que entre en modo receptor sin presionar el botón (INFERIDO; no probado en Go 4
  ni Charge 6).

## Qué stack usan normalmente estos proyectos

| Capa | Lenguaje dominante | Ejemplos |
|---|---|---|
| Protocolo Bluetooth (host y controlador) | **C** | BlueZ, Zephyr, NimBLE, BTstack, nRF, ESP-IDF |
| Audio del sistema | **C** | PipeWire, WirePlumber, PulseAudio, BlueALSA |
| Códec LC3 | **C** (con bindings) | liblc3 (+ lc3py) |
| Herramientas y experimentos con Auracast y JBL | **Python** | Bumble, openjbl, Sendspin, scripts de Collabora |
| Sincronización y DSP | C++ / Rust | Snapcast, CamillaDSP |

## Opciones realistas de stack para el prototipo

### (a) Python + Bumble + liblc3
- **A favor:**
  - Control total de los datos de anuncio y del BIG.
  - No necesita el modo experimental de BlueZ.
  - Corre como probe en espacio de usuario.
  - **Es el camino más rápido al experimento decisivo (E4).**
- **Costos:**
  - El controlador queda fuera de BlueZ (`hciconfig down` o USB passthrough), así
    que hace falta un dongle dedicado.
  - En una Pi hay que compilar liblc3.
  - Bumble todavía no llega a la versión 1.0.
- **Hay que parchear `auracast.py` para 4 parlantes** (INFERIDO, unas 50–150
  líneas). Dos cosas lo impiden hoy:
  - los índices de BIS de cada subgrupo empiezan de nuevo en 1;
  - las fuentes mono quedan siempre como `FRONT_LEFT`.
- **Posible bug:** con `--broadcast-list` (TOML), `manufacturer_data` se pasa como
  un dict sin procesar en vez de la tupla ya interpretada (INFERIDO). Con la opción
  de línea de comandos parece estar bien.

### (b) BlueZ + PipeWire (C, casi todo configuración)
- **A favor:**
  - El Go 4 ya se probó funcionando.
  - Tener varios BIS es nativo gracias a la lista `bis[]` con asignación de canal
    por BIS, así que casi no hay código que escribir.
  - Sirve en una Pi.
- **Costos:**
  - Necesita `Experimental` y `KernelExperimental` en `main.conf`. Es un cambio al
    sistema, y hay que anotar cómo revertirlo.
  - Kernel 6.4 o superior (Collabora usó 6.18), y versiones recientes de BlueZ y
    PipeWire.
  - Menos control sobre la temporización del BIG y los BIS.
  - Los datos de fabricante hay que ponerlos desde BlueZ.
  - **Enviar varios BIS a un JBL no está demostrado en ninguna parte**; Collabora
    usó 1.

### (c) Zephyr o nRF Connect SDK en un nRF5340 como emisor dedicado
- **A favor:**
  - La temporización ISO más determinista.
  - `nrf_auraconfig` ya soporta 4 BIS por BIG.
- **Costos:**
  - Hardware adicional: un nRF5340 Audio DK.
  - La toolchain de C con `west`.
  - Las licencias de Nordic y Packetcraft obligan a usar chips Nordic.
  - Hay que agregar los datos de fabricante.
- **Variante:** usar Zephyr `hci_usb` en el controlador y manejarlo desde Bumble o
  BlueZ (INFERIDO).

### (d) Rust
- No existe una capa de perfiles LE Audio en Rust. Habría que escribir BASE y BAP a
  mano sobre `iso.rs` de TrouBLE, con LC3 vía `lc3-sys`. Es la opción con más
  código y menos madurez, y BlueR no soporta ISO.
- **Solo tiene sentido** para retardos y mezcla al estilo de CamillaDSP en el
  camino A2DP.

### Camino A2DP (alternativa)
- Sink virtual de PipeWire dividido por parlante (el patrón de HyperBoom), retardos
  con CamillaDSP y calibración propia con micrófono.
- **Ninguna herramienta abierta para Linux hace calibración automática con
  micrófono de parlantes Bluetooth.** Chorus es solo para Windows y está
  incompleto.

## Correcciones a documentos anteriores

- **[02](02-le-audio-auracast-linux.md) §1** marcó como VERIFICADO (por los
  comentarios del código) que PipeWire crea un nodo por BIS. Esta línea **no pudo
  confirmar** si crea un nodo por BIS o un nodo multicanal. **Queda en disputa**
  hasta que se pruebe (E5).
- **[02](02-le-audio-auracast-linux.md) §3** presentaba `sskoog/ble_audio` como un
  emisor BIG/BIS en ESP32. Ahora el proyecto está en ESP-NOW, porque C6 y S3 no
  tienen BIG/BIS. **El ESP32 queda descartado como emisor**, salvo en las variantes
  H4 y S31, que están en preview.
- **[03](03-bluetooth-clasico-y-sync-por-software.md)** mencionaba BlueALSA como
  alternativa. Eso sigue en pie, pero **solo para A2DP**: en LE solo soporta MIDI.

## Lo que no se pudo determinar

- **El chipset del JBL Go 4 y del Charge 6.** fccid.io, fcc.report y apps.fcc.gov
  bloquearon el acceso (403 de Cloudflare/Akamai) y no hay un teardown. La única
  pista es débil (INFERIDO): openjbl muestra el UUID GATT de JBL
  `65786365-6c70-6f69-6e74-2e636f6d…`, que en ASCII es "excelpoint.com", un
  distribuidor y casa de diseño. También muestra Google Fast Pair (`FE2C`).
- Si Floss transmite en Linux de escritorio.
- Si PipeWire expone un nodo por BIS o un nodo multicanal.
- Si algún proyecto ha enviado más de un BIS a parlantes JBL.
- Si el controlador integrado de la Pi soporta ISO/BIG.
- Lenguaje y actividad de chicco-carone/sync-test y de Unified-JBL-Linking (la API
  de GitHub alcanzó su límite).

## Experimentos que esto sugiere (sin ejecutar)

1. **E2 con más herramientas.** Además de `bumble-auracast scan`, se puede usar
   `auracast-hackers-toolkit` (dongle nRF52840, barato) para capturar la BASE y los
   BIS de un par estéreo JBL.
2. **Probar `SET_AURACAST_BROADCAST`** con la secuencia de jbl-aura-play-together
   sobre un Go 4. Si funciona, el prototipo podría poner los parlantes en modo
   receptor sin tocarlos.
3. **Leer el firmware y el PID de cada parlante con openjbl**, para anotarlos en
   cada medición, como pide `CLAUDE.md`.
