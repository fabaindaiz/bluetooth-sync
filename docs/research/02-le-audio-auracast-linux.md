# LE Audio y Auracast desde Linux: stack, hardware y multicanal

Investigación del 2026-09-25. Es una de las tres líneas de la fase de investigación
(ver [README](README.md)).

**Qué significa cada marca:**
- **VERIFICADO**: leído en una fuente primaria (documentación oficial, código fuente,
  notas de versión, documentos del Bluetooth SIG).
- **REPORTADO**: blog, foro o issue.
- **INFERIDO**: deducción que nadie comprobó.

Nada de esto se había medido cuando se escribió. **Actualizado el 2026-09-28:** el
controlador del equipo Linux (Intel AX210) ya está MEDIDO en §2, y el resultado es
que no puede transmitir. Con los parlantes propios sigue sin medirse nada.

## Resumen

- **Un equipo Linux ya puede transmitir en Auracast**, con el controlador adecuado
  y los modos experimentales activados. **Collabora lo demostró con un JBL Go 4
  como receptor** (BlueZ 5.86, PipeWire 1.6.0, MediaTek MT7921). **Google Bumble**
  también transmite a un Go 4. Para que JBL acepte la transmisión hay que incluir
  **datos de fabricante de Harman (company ID 87)**.
- **El estándar sí permite un canal distinto por parlante**: un BIS por canal, cada
  uno con su Audio Location (FL, FR, C, SL…). Todos los BIS de un BIG comparten
  referencia de tiempo, así que **la sincronización la garantiza el protocolo**. Es
  la gran diferencia con A2DP.
- **Lo que no se sabe (y es la pregunta clave):** si un JBL Go 4 o Charge 6
  **elige un BIS concreto**, ya sea por su propia Audio Location o porque un
  Broadcast Assistant se lo indica por BASS, o si siempre toma el primero o mezcla.
  Solo se resuelve con un experimento.
- 5.1 por Auracast es posible en teoría, pero el ancho de banda es justo. No
  encontré ningún proyecto abierto que lo haga.

## 1. El stack en Linux

### BlueZ
- BAP llegó en **5.66** como experimental y necesita el socket ISO del kernel.
  Se activa en `/etc/bluetooth/main.conf`:
  ```ini
  [General]
  Experimental = true
  KernelExperimental = 6fbaf188-05e0-496a-9885-d6ddfdb4e03e
  ```
  VERIFICADO: https://www.bluez.org/le-audio-support/
- Hitos por versión (VERIFICADO, https://github.com/bluez/bluez/releases):

  | Versión | Qué agrega |
  |---|---|
  | 5.79 | sincronizarse a varios BIS |
  | 5.84 | transmisión cifrada y trabajo en BASS |
  | 5.86 | servidores locales MCP/GMCS y TMAS/GMAS, correcciones en VCP |
  | 5.87 (julio 2026) | correcciones de fallos en BASS y en la sincronización a BIS |
- El socket ISO sigue "experimental pero cerca de estable". Collabora recomienda
  **kernel 6.4 o superior**, y la calificación ante el SIG se espera entre 2026 y
  2027. REPORTADO:
  https://www.collabora.com/news-and-blog/blog/2025/11/24/implementing-bluetooth-le-audio-and-auracast-on-linux-systems/

### PipeWire
- Soporta BAP en todos los roles mediante `bluez5.roles`: `bap_sink`,
  `bap_source`, `bap_bcast_source`, `bap_bcast_sink`. VERIFICADO:
  https://docs.pipewire.org/page_man_pipewire-props_7.html
- `bluez5.bcast_source.config` es una lista de BIGs. Cada BIG lleva
  `broadcast_code`, `encryption`, `sync_factor`, un `adapter` opcional y un arreglo
  `bis[]`. Cada BIS tiene sus propios `qos_preset` (p. ej. `"48_2_2"`),
  `audio_channel_allocation` y `metadata`. VERIFICADO (pipewire-props y
  https://gitlab.freedesktop.org/pipewire/pipewire/-/raw/master/spa/plugins/bluez5/bluez5-dbus.c)
- PipeWire crea **un nodo aparte por cada BIS** de un BIG con varios BIS. Eso
  permite rutear L y R, o más canales, a nodos BIS distintos. VERIFICADO en los
  comentarios del código fuente. Que funcione de punta a punta para un canal por
  parlante es INFERIDO: no encontré ninguna demostración.
  **En disputa:** la línea [04](04-implementaciones-y-stacks.md) no pudo confirmar
  si PipeWire crea un nodo por BIS o un nodo multicanal. Se resuelve en E5.
  **Evidencia nueva a favor, MEDIDA en unicast** (2026-09-28,
  [experimentos/04](experimentos/04-e8-unicast-le-audio-tune-770nc.md)): con unos
  audífonos BAP estéreo, PipeWire crea **un nodo interno por cada stream isócrono**
  (`api.bluez5.internal = True`), los agrupa en un *device set* de BlueZ
  (`api.bluez5.set`) y expone un **combine-sink** como único sink estéreo, con un nodo
  marcado `set.leader`. El mecanismo de repartir un estéreo en varios streams de un
  mismo grupo existe y funciona. Que haga lo mismo con `bis[]` sigue INFERIDO.

### Configuración de referencia que funciona (Collabora, mayo 2026)
- Hardware y software: Genio 700 con **MediaTek MT7921**, kernel 6.18.5, BlueZ
  5.86, PipeWire 1.6.0 y WirePlumber 0.5.13.
- Transmitieron **un BIS mono** (`48_2_2`, allocation 0) con **datos de fabricante
  de Harman (company ID 87)** para que los parlantes JBL lo acepten.
- Receptores: **JBL Go 4** y Creative Zen Hybrid Pro.
- Según ellos, las herramientas son "impenetrables" y "necesitan mejorar mucho".

REPORTADO (blog del fabricante):
https://www.collabora.com/news-and-blog/blog/2026/05/05/bluez-powered-auracast-broadcasting-on-genio-700/

- El hackfest de PipeWire de 2026 planea un demonio Auracast separado, y la
  sincronización entre dispositivos sigue en la lista de trabajo pendiente.
  REPORTADO: https://arunraghavan.net/2026/06/notes-from-the-pipewire-hackfest-2026-part-2/

## 2. Controladores Bluetooth

Para revisar un controlador: `bluetoothctl` → `menu mgmt`. Tienen que aparecer
`iso-broadcaster` (para transmitir) y `cis-central` (para unicast). REPORTADO
(Collabora 2025-11).

**Mejor que eso: leer los bits de LE Features del propio controlador**, en
`/sys/kernel/debug/bluetooth/hci0/features` (línea `LE:`), o en una traza de `btmon`
del arranque. `btmgmt info` muestra lo que BlueZ deriva de ahí, una capa más arriba.
Los bits que importan (Core 5.4, Vol 6, Part B §4.6): **30** Isochronous Broadcaster
(transmitir), **31** Synchronized Receiver (recibir un BIS), **28/29** CIS
central/peripheral (unicast) y **13** LE Periodic Advertising (leer una BASE). La
lista de Supported Commands de la traza lo confirma: si no aparece `LE Create BIG`,
no transmite. MEDIDO en este repositorio
([experimentos/03](experimentos/03-e1-iso-en-el-ax210.md)).

**El socket ISO del kernel se activa aparte**, con `Experimental = true` y
`KernelExperimental = 6fbaf188-05e0-496a-9885-d6ddfdb4e03e` en `main.conf`. **Sin
comentarios al final de esas líneas: BlueZ los lee como parte del UUID y descarta el
valor sin avisar** (`Invalid KernelExperimental UUID`). Se comprueba con
`grep ISO /proc/net/protocols`, o abriendo un socket `BTPROTO_ISO` (8), que sin la
bandera da `EPROTONOSUPPORT`. MEDIDO.

| Controlador | Transmisión (BIS) | Unicast (CIS) | Estado | Fuente |
|---|---|---|---|---|
| **MediaTek MT7921** | **Sí** (probado con JBL Go 4) | ? | REPORTADO | Collabora 2026-05 |
| Intel BE200 | Sí (LE Audio + Auracast) | Sí | REPORTADO | Collabora 2025-11 |
| Intel AX1xx | No | Sí | REPORTADO | Collabora 2025-11 |
| **Intel AX210** | **No.** Faltan los bits 30 (Isochronous Broadcaster), 31 (Synchronized Receiver) y **13 (LE Periodic Advertising)**: tampoco puede *escuchar* un BIS ni leer una BASE | **Sí**, CIS central y peripheral | **MEDIDO** (2026-09-28, firmware `202-5.26`, LE Features `ff 59 01 3c ae 00 00 00`) | medición propia, [experimentos/03](experimentos/03-e1-iso-en-el-ax210.md); antes REPORTADO por [ak-experiments](https://ak-experiments.blogspot.com/2025/08/bluetooth-le-audio-on-raspberry-pi-with.html) e [Intel community](https://community.intel.com/t5/Wireless/Intel-Bluetooth-Isochronous-channels-and-other-mandatory/m-p/1583602) |
| Infineon CYW55573 (Sona IF573) en Pi 5 | Muestra `iso-broadcaster` y `sync-receiver` | Sí | REPORTADO. Necesita `brcm_patchram_plus` en vez de `btattach` | [RPi forum](https://forums.raspberrypi.com/viewtopic.php?t=392061) |
| Realtek RTL8852BE | La conexión ISO falla con ENOSYS | — | REPORTADO; issue cerrado sin solución | [bluez#1490](https://github.com/bluez/bluez/issues/1490) |
| Bluetooth integrado de la Raspberry Pi | No (BT 5.0) | No | REPORTADO | ak-experiments |
| nRF5340 como controlador HCI | Resultados mixtos. Necesita las opciones Kconfig de periodic advertising e ISO | — | REPORTADO | [DevZone 121293](https://devzone.nordicsemi.com/f/nordic-q-a/121293/is-hci_uart-sample-support-le-audio-auracast), [DevZone 124106](https://devzone.nordicsemi.com/f/nordic-q-a/124106/) |

**No pude confirmar ningún dongle USB que funcione de forma comprobada.** Esto
condiciona qué hardware comprar.

## 3. Stacks alternativos

### Google Bumble (Python): `auracast transmit`
VERIFICADO en el código fuente y la documentación:
- https://google.github.io/bumble/apps_and_tools/auracast.html
- https://github.com/google/bumble/blob/main/apps/auracast.py
- https://github.com/google/bumble/blob/main/examples/auracast_broadcasts.toml

Qué hace:
- Acepta como entrada un dispositivo de sonido, stdin o un archivo WAV o raw, con
  1 o 2 canales a 16, 24 o 48 kHz.
- `--bitrate` se da por canal.
- `--manufacturer-data`: la documentación trae el valor probado con el JBL Go 4,
  `87:00000000000000000000000000000000dffd`.
- Una entrada estéreo se convierte en **2 BIS marcados como FRONT_LEFT y
  FRONT_RIGHT**.
- Un archivo TOML en `--broadcast-list` define varias transmisiones o fuentes, y
  cada fuente queda en su propio subgrupo.

Sus límites:
- Las fuentes mono quedan fijas como FRONT_LEFT, y el presentation delay está fijo
  en 40 ms. **Para ubicaciones más allá de L/R (surround) hay que modificar el
  código.**
- El paquete `lc3py` solo trae binarios para Linux x86_64 y macOS arm64. **En una
  Raspberry Pi hay que compilar liblc3.**
- Usa el controlador directamente como dispositivo HCI por USB, sin pasar por
  BlueZ.

### Nordic nRF5340
- **`nrf5340_audio` broadcast source**: recibe audio por USB desde un PC (o por
  I2S) y emite un BIG con 2 BIS (L/R), entre 32 y 124 kbps a 48 kHz. VERIFICADO:
  https://nrfconnectdocs.nordicsemi.com/ncs/2.8.0/nrf/applications/nrf5340_audio/broadcast_source/README.html
- **nRF Auraconfig**: hasta 2 BIGs de 4 BIS cada uno, con un comando `location`
  por BIS. El audio solo sale de archivos LC3 ya codificados en una tarjeta SD, no
  por USB. VERIFICADO:
  https://nrfconnectdocs.nordicsemi.com/ncs/2.9.0-nRF54H20-1-rc2/nrf/samples/bluetooth/nrf_auraconfig/README.html

### Zephyr y otros
- **Zephyr `bap_broadcast_source`**: la cantidad de streams se fija con
  `CONFIG_BT_BAP_BROADCAST_SRC_STREAM_COUNT`. VERIFICADO:
  https://docs.zephyrproject.org/latest/samples/bluetooth/audio/bap_broadcast_source/README.html
- **ESP32 `sskoog/ble_audio`**: un emisor BIG/BIS al que se le pasa LC3 por USB
  serial. REPORTADO: https://github.com/sskoog/ble_audio
  **Corregido por [04](04-implementaciones-y-stacks.md) §4:** el proyecto pasó a
  ESP-NOW porque C6 y S3 no tienen BIG/BIS. El ESP32 queda descartado como emisor,
  salvo H4 y S31, que están en preview.

## 4. Multicanal por Auracast

### Qué permite el estándar
- En la BASE (Broadcast Audio Source Endpoint), la entrada de nivel 3 de cada BIS
  lleva `Audio_Channel_Allocation`. Un BIS mono simplemente la omite. Un BIG admite
  **hasta 31 BIS**. VERIFICADO:
  - SIG, "How to design Auracast earbuds" §2.6.2:
    https://www.bluetooth.com/wp-content/uploads/2024/05/2403_Auracast_Earbuds.pdf
  - https://cloud2gnd.com/fundamentals-of-le-audio-broadcast-isochronous-streams/

### Cómo elige su BIS cada receptor
Hay dos caminos (VERIFICADO, documento del SIG sobre audífonos):
1. El receptor compara sus propias **Sink Audio Locations** (del servicio PACS)
   con las asignaciones de canal que anuncia la BASE.
2. Un **Broadcast Assistant** (un teléfono, o un PC con BlueZ) escribe un campo de
   bits `BIS_Sync` en el Broadcast Receive State del servicio BASS del receptor.
   Así le indica a qué BIS engancharse.

Además:
- Un parlante con selector L/R/mono tiene que exponer sus Sink Audio Locations; el
  valor 0 significa mono. VERIFICADO (mismo documento, §6.2).
- Los receptores no están obligados a hacer downmix. Por eso el SIG recomienda
  incluir **un BIS mono aparte, en su propio subgrupo**. VERIFICADO (mismo
  documento y las recomendaciones del SIG para transmisores:
  https://www.bluetooth.com/wp-content/uploads/2022/10/Auracast-Transmitter_Recommendations.pdf)

### El caso de los JBL
- **No se pudo determinar** si el Go 4 y el Charge 6:
  - exponen Sink Audio Locations;
  - respetan un `BIS_Sync` enviado por BASS;
  - qué hacen con una transmisión de 2 BIS.
- Lo que sí se sabe: **descartan las transmisiones que no llevan datos de
  fabricante de Harman.** REPORTADO en la documentación de Bumble.

### ¿5.1 por Auracast?
Presets LC3 (VERIFICADO:
https://github.com/zephyrproject-rtos/zephyr/blob/main/include/zephyr/bluetooth/audio/bap_lc3_preset.h):

| Preset | SDU | Bitrate | RTN | Latencia |
|---|---|---|---|---|
| `48_2_2` | 100 B | 80 kbps | 4 | 65 ms |
| `48_2_1` | 100 B | 80 kbps | 4 | 20 ms |
| `24_2_1` | 60 B | 48 kbps | 2 | — |

- Con PHY 2M, un BIS `48_2_x` ocupa unos 3 ms de cada intervalo de 10 ms, así que
  caben unos 3 BIS. Para 6 BIS habría que bajar a presets tipo `24_2` con menos
  retransmisiones, y además los controladores suelen limitar la cantidad de BIS
  (Auraconfig permite 4 por BIG). INFERIDO.
- El SIG indica que varios streams pueden requerir varios BIGs. VERIFICADO
  (recomendaciones para transmisores).
- Hisense vende surround para TV con varios parlantes Auracast, pero no publica
  cómo asigna los canales. REPORTADO:
  https://www.bluetooth.com/blog/auracast-broadcast-audio-transforms-the-home-theater-experience/
- **No encontré ningún proyecto abierto de 5.1 por Auracast.**
- Para 4 parlantes (3 Go 4 + 1 Charge 6), una cuadrafonía FL/FR/RL/RR, o L/R más
  un centro o subwoofer, cabe dentro de los ~3–4 BIS. INFERIDO.

## 5. Sincronización

- Todos los BIS de un BIG comparten una referencia de tiempo (el ancla del BIG más
  BIG_Sync_Delay). Cada receptor reproduce exactamente un **Presentation Delay**
  después del final del último BIS. VERIFICADO (documento del SIG sobre audífonos;
  cloud2gnd).
- Todo receptor tiene que soportar 40 ms, y los receptores HAP/TMAP, de 20 a 40 ms.
  Si el valor pedido está fuera de su rango, el receptor usa el más cercano que
  soporte. VERIFICADO (mismo documento).
- Por lo tanto, los parlantes enganchados a un mismo BIG deberían quedar
  alineados con una diferencia de pocas muestras según el estándar. INFERIDO.
- **Dos BIGs independientes (o dos transmisiones separadas de Bumble) no tienen
  alineación garantizada entre sí.** INFERIDO. Consecuencia de diseño: todos los
  canales tienen que ir en un solo BIG.

## 6. Emisores comerciales

| Emisor | Qué ofrece | ¿Asigna canal por parlante? | Estado |
|---|---|---|---|
| Android 16 Audio Sharing | Pixel 8 en adelante, salvo el 8a | No encontrado | REPORTADO ([Android Authority](https://www.androidauthority.com/android-16-audio-sharing-3501252/)) |
| Samsung "Auracast" (en One UI 8.5 pasa a llamarse "Audio Broadcast") | Desde One UI 6.1, en S23 y S24 | No encontrado | VERIFICADO ([samsung.com](https://www.samsung.com/us/support/answer/ANS10001042/)) |
| Windows 11 Shared Audio (24H2/25H2) | Unicast a solo 2 dispositivos; Auracast completo todavía no | No | REPORTADO ([Windows Central](https://www.windowscentral.com/microsoft/windows-11/windows-11-moves-toward-multi-device-bluetooth-streaming-but-your-pc-probably-isnt-invited)) |
| Creative BT-W6 (dongle USB) | Modo BIS con PBP | No se sabe cómo distribuye los canales | VERIFICADO ([creative.com](https://us.creative.com/p/speakers/creative-bt-w6)) |
| EPOS | No investigado | — | — |

**Ningún emisor comercial encontrado permite asignar un canal a cada parlante.**
Para eso hace falta un emisor programable: PipeWire, Bumble o Nordic.

## 7. Validación del controlador nRF52840 con fuentes primarias (2026-10-07)

Se hizo al llegar las 4 SuperMini, contra el código y la documentación oficiales: sdk-nrfxlib, sdk-nrf,
Zephyr y bluekitchen en GitHub, leídos el 2026-10-07 (Zephyr en `main`, NCS en `ncs-v3.2.1` donde se
indica). El bloque del reloj (afirmación 6) se leyó con una herramienta que resume las páginas: sus citas
salen de un extracto, no de la página completa. **No se pudieron leer** el product specification del
nRF52840 (403 / timeout), la sección del Core Spec sobre el SCA (llegó truncada) ni la página del vendedor
de la SuperMini.

| Nº | Afirmación | Veredicto | Evidencia |
|---|---|---|---|
| 1 | La SoftDevice Controller (SDC) soporta ISO Broadcaster en el nRF52840 | **VERIFICADO**, soportado desde **NCS v2.6.0** (experimental en v2.5.0) | `sdk-nrfxlib/softdevice_controller/CHANGELOG.rst` L684: *"The LE Isochronous Channels feature is now supported instead of experimental, both … CIS and … BIS"*; `sdk-nrf/doc/nrf/releases_and_maturity/software_maturity.rst` L603-609: nRF52840 = Supported². La fila agrupa CIS y BIS |
| 2 | El nRF52840 no cifra ISO, así que el BIG va sin Broadcast Code | **VERIFICADO** en la práctica | README de la SDC L90: *"nRF52820 and nRF52833 are the nRF52 Series devices that support encrypting and decrypting the Isochronous Channels packets"*; nota ² L948. Causa de hardware: el CCM del 52840 no tiene `headermask-supported` (`zephyr/dts/arm/nordic/nrf52840.dtsi` L313-318). Qué hace la SDC (binaria) ante `Encryption=1`: sin determinar, se ve en el banco |
| 3 | 4 BIS a 48 kHz caben en radio y RAM | **Matizado** | ~96 kbps es **48_4** (120 B, RTN 4; `bap_lc3_preset.h` L609-613), no 48_2 (100 B, 80 kbps). RAM: ~6 kB de 256 kB (`sdc.h` L340/343). Tiempo (INFERIDO, cálculo): ~674 µs por subevento en 2M → con RTN 4 cuatro BIS piden ~13,5 ms de cada 10: **no cabe**; la SDC toma RTN como tope (*"treated as upper limits"*, `isochronous_channels.rst` L111) y lo baja sola. Búferes: `BT_ISO_TX_BUF_COUNT` **vale 1 por defecto** (`zephyr/subsys/bluetooth/Kconfig` L352-354), `BT_CTLR_SDC_ISO_TX_HCI_BUFFER_COUNT` 3 y el de PDU por stream 3 (`sdk-nrf/subsys/bluetooth/controller/Kconfig` L429-455): poco para 4 SDU cada 10 ms |
| 4 | `hci_uart` por CDC-ACM lleva ISO; `hci_usb` no | **VERIFICADO** | zephyr#44013 **sigue abierto** (último comentario 2026-05-27, sin respuesta); el PR #93453 se cerró sin fusionar; `subsys/usb/device_next/class/bt_hci.c` L94-99: *"we do not implement isochronous endpoints handling"*. `samples/bluetooth/hci_uart/src/main.c` L48/L76: `H4_ISO 0x05`. **La placa `promicro_nrf52840` ya trae CDC-ACM como UART del HCI** (`boards/others/promicro_nrf52840/Kconfig.defconfig` L8; `boards/common/usb/cdc_acm_serial.dtsi` L13) |
| 5 | `bluekitchen/hci_uart_iso_timesync` no soporta el nRF52840 | **Matizado** | No lo lista (`Kconfig` L7 lo excluye); hubo un `.conf` del 52840 agregado y luego borrado (commits 066fe620 y 667c17db); el repo no tiene issues. Portarlo parece fácil (INFERIDO): `src/controller_time_nrf52.c` usa RTC0/RTC2/TIMER1/PPI/EGU0, presentes en el 52840. **Hay una alternativa estándar:** `sdc_hci_vs.h` L101 `VS_ISO_READ_TX_TIMESTAMP = 0xfd17` y `LE Read ISO TX Sync`; `isochronous_channels.rst` L252-262 recomienda usar el mismo timestamp en todos los BIS para que salgan alineados |
| 6 | La SuperMini puede no traer cristal de 32 kHz; con RC sigue sirviendo | **Matizado** | El nice!nano original sí lo trae (*"32.768 kHz oscillator on board"*, nicekeyboards.com); que la SuperMini no lo traiga es **REPORTADO** por la comunidad. LFRC: *"requires periodic calibration"* (doc de MPSL); tolerancia real ±500 ppm aun calibrando (Nordic en DevZone, REPORTADO). El SCA viaja en el SyncInfo del periodic advertising, **no en el BIGInfo** (`zephyr/subsys/bluetooth/controller/ll_sw/pdu.h`). Que la SDC acepte RC con BIS: no lo dice ningún texto (NO DETERMINADO) |
| 7 | El nRF52840 no tiene Bluetooth clásico | **VERIFICADO** (por ausencia) | nordicsemi.com/Products/nRF52840: *"Bluetooth LE, Bluetooth Mesh, NFC, Thread and Zigbee"* |
| 8 | 4 BIS caben en USB CDC y en UART a 1 Mbaud | **INFERIDO** (cálculo) | 4 × ~133–138 B cada 10 ms ≈ 53–55 kB/s: 55 % de un UART a 1 Mbaud, más de 9× de margen en USB full-speed. El `hci_uart` de Zephyr viene a **115200 baud**: por UART hay que subirlo, con RTS/CTS |

**Conclusión:** las SuperMini sirven como controlador Auracast con Bumble para 4 BIS, con un BIG **sin
cifrar**, los **búferes ISO subidos** y un **RTN efectivo de 1–2** (o 48_2). No hacen falta el fork de
bluekitchen ni la configuración del dongle. El mayor riesgo propio del hardware es el reloj de 32 kHz
junto con el tiempo de radio; el riesgo mayor del proyecto sigue siendo E4.

### 7.1 Caminos alternativos (2026-10-07; INFERIDO salvo lo marcado)

| Camino | Qué es | Gana | Cuesta / riesgo | Cuándo |
|---|---|---|---|---|
| **A. Bumble + SuperMini con la SDC** (el plan) | `hci_uart` de NCS como controlador; Bumble codifica LC3 y arma el BIG | lo que el repositorio ya planeó; el LC3 en el PC; la SDC es la que Nordic soporta | parches locales de Bumble para 4 BIS; la SDC es binaria, sin poder inspeccionarla | ahora |
| **B. BlueZ + PipeWire con la SuperMini como controlador del kernel** | `btattach` sobre `/dev/ttyACM*` (H4): BlueZ ve un controlador con `iso-broadcaster` (lo que le faltaba al AX210, E1) y PipeWire hace el broadcast con LC3 (§1, Collabora) | integra directo con el motor y PipeWire de hoy, sin Bumble; el camino del producto si Auracast se adopta | modos experimentales de BlueZ (`Experimental`, `KernelExperimental`, `bcast_source.config`: cambio de sistema N4, anotado con su reversión); cuánto aguanta BIS con 4 canales en BlueZ, sin medir | **E3 como segundo emisor** ("Bumble frente a PipeWire" ya está en el roadmap) |
| **C. El controlador abierto de Zephyr (LL_SW)** en vez de la SDC | `hci_uart` con `overlay-all-bt_ll_sw_split.conf` | código abierto e inspeccionable; errores explícitos (rechaza el cifrado con `CMD_DISALLOWED`, `ull_adv_iso.c` L157-160) | menos probado con BIS en el nRF52840; `BT_CTLR_ADV_ISO_STREAM_MAX=2` hay que subirlo | si la SDC falla de forma opaca |
| **D. Otro chip: nRF52833 o nRF5340 (Audio DK)** | el controlador oficialmente cubierto | cifra ISO; el fork de bluekitchen lo soporta tal cual; la opción C de [06](06-opcion-c-nrf5340.md) | **es una compra** (preguntar antes, CLAUDE.md) | solo si hace falta cifrar o si la SuperMini falla por el reloj |
| **E. Un BIG mono por SuperMini** (4 placas, 4 BIG) | cada JBL escucha su propio broadcast | sirve aunque el JBL ignore la selección de BIS | d-7c8794-203de2: dos broadcasts no quedan alineados (provisional hasta E5); alinearlos pediría leer el reloj ISO de cada controlador (el fork de bluekitchen, o 0xfd17) | es E4(c); solo si E4(a) y (b) fallan |
| **F. Si E4 dice que no** | A2DP sigue como el camino principal (como hoy) | ya funciona | los límites de A2DP (unos 3 enlaces por adaptador) | según la decisión i-7c8794-0d129c |

### 7.2 Lo que agregó leer NCS v3.4.1 al armar el firmware (2026-10-07)

Leído en la copia local que instaló `nrfutil sdk-manager` (`~/ncs/v3.4.1`, `nrf` en el commit `b20f8619`,
2026-09-17) y en Bumble 0.0.235 instalado en el entorno de `host/`. El firmware se compiló
(`firmware/supermini/hci_uart_iso/`, [experimentos/21](experimentos/21-f1-iso-en-la-supermini.md)).

| Nº | Afirmación | Veredicto | Evidencia |
|---|---|---|---|
| 9 | Con Bumble tal cual, los 4 BIS **no quedan alineados por garantía** | **VERIFICADO** por la documentación. **En la práctica se alinearon** en 80 arranques medidos en la fuente y 41 en el aire, con y sin carga (MEDIDO, [experimentos/21](experimentos/21-f1-iso-en-la-supermini.md)). Como la SDC no lo garantiza, el emisor del producto usa igual el modo timestamp | La SDC acepta SDU en tres modos: timestamp, momento de llegada y número de secuencia (`nrfxlib/softdevice_controller/doc/isochronous_channels.rst` L182-240). *"Providing the same sequence number to different CISes or BISes does not time-synchronize the provided SDUs"* (L265); lo que sincroniza es dar **el mismo `Time_Stamp`** a las SDU de un mismo intervalo (L252-262). Bumble manda las SDU **sin timestamp** y con un número de secuencia propio por cada enlace (`bumble/host.py` L905-941: cabecera de 4 B, `packet_sequence_number` por enlace). Si la primera SDU de un BIS llega a la SDC en otro evento que la de los demás, ese BIS queda un intervalo (10 ms) corrido |
| 10 | El modo timestamp es implementable desde el host sin tocar la SDC | **VERIFICADO** (código) | `VS ISO Read TX Timestamp` (0xfd17) está compilado en el `hci_uart` (`nrf/subsys/bluetooth/controller/hci_internal.c` L1785-1791) y devuelve `{handle, psn, tx_time_stamp}` (`sdc_hci_vs.h` L668-680). Las SDU tienen que llegar al menos `HCI_ISO_TX_SDU_ARRIVAL_MARGIN_US` = **1000 µs** antes de su timestamp (`sdc_hci.h` L83); si el timestamp ya pasó, la SDU **se descarta** (L201-202). Bumble no expone el campo, pero `HCI_IsoDataPacket` sí lo serializa (comprobado offline: cabecera `0x6010`, TS_Flag = 1) |
| 11 | Tiempo reservado para el periodic advertising en cada evento BIG | **Contradicción en la fuente** | La doc dice 2,5 ms (`isochronous_channels.rst` L147-149), pero `BT_CTLR_SDC_BIG_RESERVED_TIME_US` vale **1600 µs** por defecto en v3.4.1 (`nrf/subsys/bluetooth/controller/Kconfig` L519-525), y así quedó en el `.config` compilado. Se cambia en vivo con el VS 0xfd18. Con 4 BIS de 120 B, ese margen es la mitad del problema del tiempo de radio (afirmación 3) |
| 12 | La alimentación desde el PC va a depender de cómo la SDC cuenta los búferes | **VERIFICADO** (código) | El host ve un solo grupo de búferes ISO para todos los BIS (`bumble/host.py` L593-598, `iso_packet_queue`), y la SDC tiene 10 búferes HCI compartidos y 3 PDU por BIS (`.config` compilado). El tope de 3 PDU por BIS **limita el PTO** que puede elegir (Kconfig L407-418) |
| 13 | En un build HCI crudo, ISO se activa con las opciones del host | **VERIFICADO** (build) | `BT_CTLR_ADV_ISO` depende de `BT_ISO_BROADCASTER` y `BT_CTLR_SYNC_ISO` de `BT_ISO_SYNC_RECEIVER` (`zephyr/subsys/bluetooth/controller/Kconfig` L896-925); con ambas, una sola imagen emite y recibe. `BT_CTLR_ADV_DATA_LEN_MAX` vale **31** por defecto y la SDC dimensiona con él los datos extendidos y periódicos (`hci_driver.c` L73-106): un anuncio Auracast con nombre y datos de JBL no cabe sin subirlo |

**Qué cambia:** el plan de E5 ya pedía el modo timestamp. Ahora se sabe que **sin él la alineación es
cuestión de suerte en cada arranque**, y se mide antes, en el experimento 21, sin parlantes: el emisor
escribe el mismo contador de cuadro en los 4 BIS y otra SuperMini lo lee en el aire.

## 8. Escuchar a los JBL: sniffer y observación (2026-10-07)

Preparado en una sesión lateral **sin tocar hardware**: no se abrió ningún puerto, no se grabó ninguna
placa y no se instaló nada. Lo descargado quedó **fuera del repositorio**, en el directorio temporal de
la sesión (`/tmp/claude-1000/…/scratchpad/sniffer/`, se pierde al reiniciar; §8.1 dice cómo rehacerlo).
El caso de uso principal es entender tres protocolos de los JBL: su **Auracast** (modo fiesta), su
**sincronización** y cómo se arma el **Stereo Group**. Nada de esta sección está MEDIDO con los JBL.

### 8.1 Qué sniffer corre en una SuperMini: el nRF Sniffer de Nordic, sí

| Nº | Afirmación | Veredicto | Evidencia |
|---|---|---|---|
| 14 | La última versión es la **4.1.1**, y el `nrfutil ble-sniffer` más nuevo trae el mismo firmware | **VERIFICADO** | `nrf_sniffer_for_bluetooth_le_4.1.1.zip` (sha256 `26502447…dd40`, de `nsscprodmedia.blob.core.windows.net`; 4.1.2, 4.2.0 y 5.0.0 dan 404). `nrfutil-ble-sniffer` 0.21.0 (publicado 2026-08-14, sha256 `b95389ec…83f7`): su `.bin` del dongle es **idéntico byte a byte** al `.hex` del zip. Nordic no promete versiones nuevas (REPORTADO, [DevZone 117393](https://devzone.nordicsemi.com/f/nordic-q-a/117393/ble-sniffer-in-nrf52840-coded-phy-don-t-work/515761)) |
| 15 | La imagen del **nRF52840 Dongle (PCA10059)** se enlaza en **`0x1000`–`0x11FF4`** (69 620 B, reset en `0xAB1D`), detrás del MBR y sin SoftDevice; la del DK, en `0x0000` | **VERIFICADO** | rangos de direcciones leídos de los `.hex` con un lector propio |
| 16 | El bootloader 0.6.0 acepta un UF2 de familia `0xADA52840` desde `0x1000`: escribe **encima de la S140** y salta lo que cae bajo `0x1000` (el MBR) | **VERIFICADO** | Adafruit_nRF52_Bootloader, tag `0.6.0` (`42d9d9f`): `USER_FLASH_START = MBR_SIZE` (`src/usb/uf2/uf2cfg.h` L20), `write_block()` (`ghostfat.c` L385-425); `src/boards/nice_nano/board.h` es la placa de nuestro `INFO_UF2.TXT` |
| 17 | Sin la S140, el bootloader **arranca la aplicación en `0x1000`** | **VERIFICADO** (código) | `bootloader_app_start()` (`bootloader.c` L372-388): si `is_sd_existed()` es falso, `app_addr = MBR_SIZE` y reenvía las interrupciones ahí. `is_sd_existed()` mira la palabra mágica en `0x3004` (`dfu_types.h` L36-39); en la imagen del sniffer esa palabra vale `0x1AC073BC`, no `0x51B1E5DB`. El UF2 deja el CRC del banco en 0, y con 0 no se chequea (`msc_uf2.c` L189-229, `bootloader.c` L171). Changelog 0.4.0: *"bootloader will always work with and/or without softdevice present"* |
| 18 | **Adafruit ya publica esta misma conversión** para placas con su bootloader | **VERIFICADO** (que existe) / REPORTADO (que funcione) | release [`softdevice-uf2`](https://github.com/adafruit/Adafruit_nRF52_Bootloader/releases/tag/softdevice-uf2) (2021-12): `sniffer_nrf52840dongle_4.1.0.uf2`, familia `0xADA52840`, `0x1000`–`0x12000`, el mismo diseño que el nuestro; guía [BLE Sniffer with nRF52840](https://learn.adafruit.com/ble-sniffer-with-nrf52840) |
| 19 | La SuperMini **no necesita el cristal de 32 kHz** para el sniffer | **VERIFICADO** | `release_notes.txt` 4.1.0: *"Removed dependency on external 32KHz crystal in all boards"* |
| 20 | Se enumera por el USB nativo como `1915:522A` (Nordic) | **VERIFICADO** el descriptor / INFERIDO que la SuperMini enumere igual | descriptor USB dentro de la imagen; el USB es el del chip, el mismo en el dongle y en la SuperMini |
| 21 | Los LED y el botón del dongle no existen en la SuperMini | INFERIDO inocuo | la imagen manejará pines del PCA10059 que en la SuperMini son pads libres; no se desensambló (la licencia lo prohíbe): de la imagen solo se leyeron los rangos, la palabra en `0x3004` y el descriptor USB. `REGOUT0` ya está en 3,3 V: lo fija el bootloader de la nice!nano (`board.h` L30) |
| 22 | Licencias | **VERIFICADO** | El zip dice MIT (`LICENSE.txt`, *"All files in this package"*), pero las cabeceras de sus `.py` son **Nordic 5-Clause**: solo con un circuito de Nordic (la SuperMini lo es) y **los binarios no se modifican ni se desensamblan**. `nrfutil ble-sniffer`: `LicenseRef-Nordic-1-Clause` (solo binario, sin redistribuir). Que pasar de `.hex` a `.uf2` no cuente como "modificar" es INFERIDO: cambia el contenedor y los bytes que se graban son los mismos. **Nada de esto entra al repositorio** |

**El UF2 preparado** (en el directorio temporal, sin grabar):

| Archivo | sha256 | Qué es |
|---|---|---|
| `out/sniffer_nrf52840dongle_4.1.1_supermini.uf2` | `bb8f0a497a298e1710c4a8fe3a3dca11f41dd74b3f1b8c8daef9e84f83c43277` | 272 bloques, `0x1000`–`0x12000`, familia `0xADA52840`. **Dos conversores independientes dieron el mismo sha256**: `uf2conv.py` de Microsoft (`microsoft/uf2` `90e9741`, MIT) y uno propio |
| `out/restore_s140_6.1.1.uf2` | `f3b6f67c69bd90f1f83e17f6165918e6173dd5f715fc4358fea60b38cf0a67c4` | solo la S140 6.1.1 (`0x1000`–`0x25E00`, sin MBR). **Idéntica byte a byte** a la S140 del respaldo de fábrica de A y C (`~/supermini-respaldo/`) y a la del repositorio de Adafruit. Sirve también el `s140_nrf52_6.1.1_softdevice.uf2` de Adafruit (sha256 `67a0e0b1…7461`) |

**Cómo se graba y se vuelve atrás** (lo hace quien tenga las placas). **Propuesta, revisada el
2026-10-07:** una de las dos placas **sin estrenar**, dedicada al sniffer. Viene sin aplicación, así que
arranca sola en modo UF2 y se graba sin doble reset. El sniffer no sabe volver al bootloader por software,
y el doble reset resultó difícil de hacer a mano ([experimentos/21](experimentos/21-f1-iso-en-la-supermini.md)).
Como hay solo 2 puertos, se cambia de placa en vez de regrabar:
1. Doble reset → disco `NICENANO` → copiar `sniffer_…_supermini.uf2`. Si no aparece el puerto, desconectar
   y reconectar (lo que pasó con la placa C, [experimentos/21](experimentos/21-f1-iso-en-la-supermini.md) §0).
2. Comprobar `lsusb` → `1915:522a` y un `/dev/ttyACMn` nuevo.
3. **Para volver: grabar `restore_s140_6.1.1.uf2`.** El sniffer solo ocupa `0x1000`–`0x12000`, así que al
   volver la S140 la placa queda como estaba: de fábrica o con su `hci_uart_iso` en `0x26000` (A, C).
   **Grabar solo `hci_uart_iso` encima del sniffer no basta**: sin la S140 el bootloader seguiría
   saltando a `0x1000` (afirmación 17). INFERIDO del código; se mide en S0 (§8.5).

**Rehacerlo desde otra máquina** (todo con fuentes públicas):
```bash
curl -LO https://nsscprodmedia.blob.core.windows.net/prod/software-and-other-downloads/desktop-software/nrf-sniffer/sw/nrf_sniffer_for_bluetooth_le_4.1.1.zip
unzip nrf_sniffer_for_bluetooth_le_4.1.1.zip -d nrfsniffer
git clone --depth 1 https://github.com/microsoft/uf2
python3 -I uf2/utils/uf2conv.py -c -f 0xADA52840 -o sniffer_nrf52840dongle_4.1.1_supermini.uf2 \
  nrfsniffer/hex/sniffer_nrf52840dongle_nrf52840_4.1.1.hex   # -c: solo convierte, nunca copia a un disco
```

### 8.2 Qué ve cada instrumento

| Qué | nRF Sniffer 4.1.1 (una SuperMini dedicada) | Observador HCI: `hci_uart_iso` + Bumble (A o C) | Registro HCI de Android |
|---|---|---|---|
| Anuncios legacy y extendidos (`ADV_EXT_IND`, `AUX_ADV_IND`, `AUX_CHAIN_IND`) | **Sí**, el PDU entero (extendidos desde 4.0.0, cadenas desde 4.1.0) | Sí, como reportes HCI | Solo los que escanea el teléfono |
| Tren periódico (`AUX_SYNC_IND`): la **BASE** y el **BIGInfo** en el ACAD | **Sí, siguiendo a un anunciante** (4.1.0, *"following a periodic advertiser"*): BIGInfo completo, con `bisPayloadCount`, mapa de canales y Seed AA | **Sí**: BASE en el reporte periódico; BIGInfo resumido (el evento HCI no trae Seed AA ni mapa de canales) | No |
| PDU de los BIS (el audio) | **No**: el protocolo del sniffer no define ISO | **Sí, las SDU** (LC3) con su timestamp, al sincronizarse al BIG (MEDIDO con nuestro BIG, [experimentos/21](experimentos/21-f1-iso-en-la-supermini.md)) | No |
| Una **conexión LE entre otros** (p. ej. entre dos JBL, o teléfono–JBL) | **Sí, si ve el `CONNECT_IND`**: sigue a un dispositivo. Cifrada: con passkey, OOB, LTK o la clave privada de LESC | No | La del propio teléfono, **en claro** (HCI está encima del cifrado) |
| BR/EDR (A2DP, SPP, un posible enlace "TWS" propietario) | **No** (radio LE) | No | La del propio teléfono |
| Tiempo | µs del inicio de cada paquete, reloj del sniffer | µs del controlador en cada SDU | ms del teléfono |
| Fuente | `release_notes.txt`, `doc/sniffer_uart_protocol.txt` (tipos `AUX_*`, sin ISO) | Bumble 0.0.235 `apps/auracast.py`, [experimentos/21](experimentos/21-f1-iso-en-la-supermini.md) | [source.android.com](https://source.android.com/docs/core/connect/bluetooth/verifying_debugging) (VERIFICADO) |

**Las alternativas, y por qué no van primero:**

| Herramienta | Chips | Licencia | Qué agrega | Veredicto |
|---|---|---|---|---|
| [auracast-hackers-toolkit](https://github.com/auracast-research/auracast-hackers-toolkit) (`e20e1e3`, 2026-10-06) | nRF52840 Dongle, con su **fork de Zephyr** (LL abierto) | **ambigua**: el repositorio no tiene LICENSE; los archivos de build dicen Apache-2.0 y los `.c` no traen cabecera | captura de los **PDU de los BIS** (con retransmisiones, `sniff greedy`) y extcap de Wireshark (REPORTADO, su README) | el único que ve el BIS en el aire. Pide `K32SRC_XTAL` (habría que pasarlo a RC) y portarlo a `promicro_nrf52840`. **Decisión del usuario** por la licencia |
| [Sniffle](https://github.com/nccgroup/Sniffle) | **solo TI** CC13xx/CC26xx | GPL-3.0 | extendidos *"non-periodic"* | no corre en el nRF52840 (VERIFICADO, README) |
| [ButteRFly](https://github.com/whad-team/butterfly) (WHAD) | nRF52840 Dongle y MDK (trae un `.uf2`) | MIT | seguir e inyectar en conexiones LE | no menciona periódico ni ISO (REPORTADO); útil solo si el nRF Sniffer pierde la conexión entre los JBL |

### 8.3 Los tres casos de uso, paso a paso

**(a) El Auracast de un JBL (modo fiesta).** Ya se sabe lo del anuncio extendido
([experimentos/01](experimentos/01-e2-anuncios-jbl-mac.md)); falta la BASE y el BIGInfo (E2).
1. El observador HCI (A) corre `capturar.sh hci` y el sniffer `capturar.sh nrf`, a la vez.
2. Go 4 conectado por A2DP al teléfono, reproduciendo; botón Auracast. 60 s. Repetir con el Charge 6.
3. `jbl_decode.py` sobre las dos capturas: Broadcast_ID, PBP, **BASE** (cuántos BIS, qué Audio Location, el
   presentation delay), **BIGInfo** (NSE, BN, PTO, IRC, PHY, si va cifrado: 57 B en vez de 33).
4. **Las dos capturas tienen que coincidir** en la BASE y el BIGInfo: son instrumentos independientes
   (la regla del CLAUDE.md). Repetir el botón dos veces: ¿el Broadcast_ID es fijo (`0x112233` en el
   Charge 6 parece de prueba)?

**(b) Cómo se arma el Stereo Group.** Mientras reproduce en estéreo no hay `0x1852` visible
([experimentos/01](experimentos/01-e2-anuncios-jbl-mac.md), resultado 4). Las hipótesis y qué las separa:

| Hipótesis | Qué se vería | Instrumento |
|---|---|---|
| H1: un BIG con un BIS por canal, y el primario apaga el anuncio extendido cuando el secundario ya se sincronizó | durante la formación, `ADV_EXT_IND`/`AUX_ADV_IND` con SyncInfo del primario; después solo el tren periódico. **Si se empieza a capturar tarde, el tren ya no se encuentra** (sin SyncInfo no hay cómo seguirlo) | sniffer escaneando todo desde antes; observador HCI |
| H2: igual que H1, pero con cifrado o una BASE propietaria | BIGInfo de 57 B; PBP con el bit 0; BASE con LTV de fabricante | los dos |
| H3: una conexión LE (ACL o CIS) entre los dos parlantes | `CONNECT_IND` o `AUX_CONNECT_REQ` entre dos direcciones JBL | sniffer siguiendo a un JBL |
| H4: un enlace BR/EDR propietario (como los auriculares "TWS") | en LE, nada más que el cambio de los bytes de reposo (`09 60`) | **ninguno de los de aquí**; queda por descarte |

What Hi-Fi llama al modo "Auracast in stereo mode" (REPORTADO, [01](01-parlantes-jbl.md) [14]), lo que
favorece H1 o H2. El estéreo se forma desde el botón o desde la app (REPORTADO, sin pasos verificados).
Procedimiento: capturar **desde 30 s antes** de formar el par hasta 60 s después, anotar la hora de cada
acción y quién queda primario, deshacer y repetir **3 veces**. Si aparece un tren periódico o una
conexión, repetir siguiendo esa dirección en Wireshark (selector *Device*).

**(c) La sincronización.**
- El BIGInfo da la estructura del tiempo (ISO_Interval, Sub_Interval, BIS_Spacing, NSE, BN, PTO, IRC) y
  la BASE el **presentation delay**: el retardo que cada receptor aplica (VERIFICADO §5).
- **El ancla del BIG = el inicio del `AUX_SYNC_IND` + BIG_Offset**: así lo calcula el receptor de Zephyr
  (`ull_sync_iso.c` L585-600, v3.4.1; VERIFICADO en código). Con el µs del sniffer, `jbl_decode.py` lo
  estima (`big_anchor_fw_us`). Que el timestamp del sniffer marque el mismo instante es INFERIDO.
- **Deriva del reloj del JBL:** el intervalo medido entre `AUX_SYNC_IND` frente al nominal del SyncInfo,
  en el reloj del sniffer (`aux_sync_interval_us`), y por otro camino los timestamps de las SDU en el
  observador HCI, como en el experimento 21. Dos instrumentos, dos relojes: si no coinciden, no se cree.
- **Lo que ningún sniffer ve** es cuándo suena cada parlante: eso sigue siendo del micrófono
  ([experimentos/15](experimentos/15-calidad-medida-con-microfono.md)).

**(d) El protocolo de la app (pairing del Stereo Group).** Con un teléfono Android: activar el registro
HCI en Opciones de desarrollador, formar y deshacer el par desde JBL Portable, y sacar el btsnoop
(`data/misc/bluetooth/logs` o con un bugreport y `btsnooz.py`). Ve GATT y SPP **en claro** y lo lee
`jbl_decode.py` (eventos) o Wireshark (todo). Es la vía más directa para los comandos; openjbl ya
documentó una parte del protocolo de JBL Portable ([04](04-implementaciones-y-stacks.md) §6).

### 8.4 Integración con el equipo Linux

**Qué tiene que instalar el usuario** (nada se instaló; `HP-O16`, CachyOS):

| Paso | Comando | Para qué | Cómo revertirlo |
|---|---|---|---|
| 1 | `sudo pacman -S --needed wireshark-qt python-pyserial` | Wireshark (4.7.3 en Arch, trae `wireshark-cli` con `tshark` y `editcap`) y `pyserial`, que **falta** en el `python3` 3.14 del sistema (MEDIDO); `psutil` ya está (`python-psutil` 7.2.2, MEDIDO) | `sudo pacman -Rns wireshark-qt python-pyserial` |
| 2 | `sudo usermod -aG wireshark $USER` y volver a entrar | solo para capturar en vivo desde Wireshark: `dumpcap` queda `0754 root:wireshark` (PKGBUILD de Arch, L149-150). Leer archivos con `tshark` no lo necesita | `sudo gpasswd -d $USER wireshark` |
| 3 | `mkdir -p ~/.local/lib/wireshark/extcap && cp -r <zip>/extcap/* ~/.local/lib/wireshark/extcap/ && chmod +x ~/.local/lib/wireshark/extcap/nrf_sniffer_ble.{sh,py}` | el extcap personal: `~/.local/lib/wireshark/extcap` (`wsutil/filesystem.c`, `init_extcap_pers_dir`, VERIFICADO). Opcional: `cp -r <zip>/Profile_nRF_Sniffer_Bluetooth_LE ~/.config/wireshark/profiles/` | `rm -r ~/.local/lib/wireshark/extcap/{nrf_sniffer_ble.sh,nrf_sniffer_ble.py,SnifferAPI,requirements.txt}` |
| Alternativa a 1 y 3 | `nrfutil install ble-sniffer` y `nrfutil ble-sniffer bootstrap` | el extcap de Nordic como binario, en el espacio del usuario, sin `pyserial`; `nrfutil ble-sniffer sniff --port …` captura sin Wireshark | `nrfutil uninstall ble-sniffer` |

**Un riesgo con las otras placas (VERIFICADO en el código, efecto INFERIDO):** al abrirse, Wireshark le
pide al extcap la lista de interfaces, y `UART.find_sniffer()` **abre cada puerto serie** a 1 Mbaud y a
460 800, con `rtscts`, 0,3 s cada uno. Si A o C están en una sesión de Bumble, ese ir y venir puede
fallar o tocar la configuración del puerto. Regla: **no abrir Wireshark con el extcap instalado mientras
corre una sesión HCI**, o capturar sin la interfaz con `capturar.sh nrf`, que solo abre el puerto pedido.
El puerto se da como `/dev/ttyACMn`: el extcap parte el nombre de la interfaz en el primer `-`.

**tshark** (nombres de campo VERIFICADOS en el código de Wireshark `master`, 2026-10-07):
```bash
editcap -F pcap c.pcapng c.pcap                                          # para jbl_decode.py
tshark -r c.pcapng -Y 'btcommon.eir_ad.entry.company_id == 0x0057'       # todo lo de Harman
tshark -r c.pcapng -Y 'nordic_ble.aux_type == 2'                         # AUX_SYNC_IND: el tren periódico
tshark -r c.pcapng -Y 'btle.advertising_header.pdu_type == 0x05'         # CONNECT_IND: alguien conectó
tshark -r c.pcapng -Y 'btcommon.eir_ad.entry.type == 0x2c' -T fields -e frame.time_relative \
  -e btcommon.eir_ad.entry.biginfo.num_bis -e btcommon.eir_ad.entry.biginfo.nse \
  -e btcommon.eir_ad.entry.biginfo.bn -e btcommon.eir_ad.entry.biginfo.pto \
  -e btcommon.eir_ad.entry.biginfo.irc -e btcommon.eir_ad.entry.biginfo.max_pdu \
  -e btcommon.eir_ad.entry.biginfo.sdu_interval -e btcommon.eir_ad.entry.biginfo.phy  # BIGInfo
tshark -r h.btsnoop -Y 'bthci_evt.le_meta_subevent == 0x22'              # BIGInfo por HCI
```

**El pipeline**, en `probes/22-sniffer-jbl/` (sonda desechable, d-7c8794-3208b7):
`captura (pcap/btsnoop) → jbl_decode.py → JSON por línea`. Lee el archivo directamente, sin pasar por el
JSON de tshark, por dos razones: **Wireshark no decodifica la BASE, ni `0x1852` ni `0x1856`** (la tabla de
service data solo tiene GAEN `fd6f` y Matter `fff6`, `packet-bluetooth.c`; VERIFICADO), y en el JSON las
entradas AD repetidas no se pueden emparejar sin ambigüedad. tshark queda para mirar y como **segundo
decodificador del BIGInfo**: si los dos no coinciden, el error es de uno de ellos. Las pruebas
(`test_jbl_decode.py`, 7, sin hardware) cruzan la BASE y los eventos HCI contra Bumble y el BIGInfo contra
las máscaras de Wireshark.

**Cómo podría entrar en `aurasync` después** (diseño, sin código; solo cuando la sonda dé su resultado):
1. Los decodificadores (datos de Harman, BASE, BIGInfo) a un módulo nuevo en inglés, p. ej.
   `aurasync/observe.py` (d-7c8794-7b3093).
2. Un comando de diagnóstico, `aurasync diag auracast --transport serial:/dev/ttyACMn --seconds 30`: el
   observador HCI con Bumble (ya es dependencia) y un resumen JSON de qué JBL transmiten, con qué BASE y
   qué BIGInfo.
3. Si se confirma que los bytes de reposo marcan el Stereo Group (`09 60`), el servicio podría avisar en el
   panel que un parlante está en un par (y por eso el secundario no aparece por A2DP, E9).
4. El nRF Sniffer queda **fuera** de `aurasync`: herramienta de Wireshark con licencia de Nordic.

### 8.5 Experimentos de captura, en orden

1. **S0, el banco sin JBL**, con 2 puertos (la placa del sniffer y A emitiendo con `tx_big.py`): que el
   sniffer enumere como `1915:522a`, que vea el `AUX_ADV_IND` con SyncInfo y el BIGInfo de A, y que ese
   BIGInfo coincida con lo que A pidió y con lo que contestó su `LE Create BIG Complete` (NSE 2, IRC 2,
   PTO 0, [experimentos/21](experimentos/21-f1-iso-en-la-supermini.md)). La restauración
   (`restore_s140_6.1.1.uf2`) solo se prueba si un día hace falta reusar esa placa.
   Valida el instrumento con un emisor conocido antes de creerle con un JBL.
2. **S1, E2 completo:** Go 4 y Charge 6 en modo fiesta, con los dos instrumentos (§8.3 a).
3. **S2, el Stereo Group formándose:** §8.3 b, tres veces, escaneando todo desde antes.
4. **S3, qué lleva cada BIS:** si S1 o S2 muestran un BIG, el observador HCI se sincroniza a cada BIS
   mientras el teléfono manda un tono distinto por canal, y se decodifican las SDU (LC3). Responde cómo
   viaja L/R y es evidencia directa para E4.
5. **S4, la sincronía:** 10 min de BIG anclado en los dos instrumentos (§8.3 c), y el micrófono para lo
   que suena.
6. **S5, la app:** el registro HCI de Android mientras se arma el par (§8.3 d), si hay un Android a mano.

## Lo que no se pudo determinar

- Si los JBL eligen un BIS según su ubicación o respetan `BIS_Sync` por BASS, y si
  el modo estéreo del Charge 6 usa un par de BIS L/R.
- El estado del firmware BIS en Intel AX210: **resuelto por medición** (2026-09-28).
  El firmware `202-5.26` no tiene BIS ni advertising periódico
  ([experimentos/03](experimentos/03-e1-iso-en-el-ax210.md)). El AX211 sigue sin
  saberse (las páginas de Intel devolvieron 403).
- Un dongle USB confirmado como funcional.
- Alguna demostración de PipeWire con varios BIS L/R.

## Experimentos que esto sugiere (sin ejecutar)

1. **Probar primero el controlador.** Con el hardware que ya hay (PC o laptop),
   revisar en `bluetoothctl` → `menu mgmt` si aparece `iso-broadcaster`. Si no,
   evaluar comprar un controlador MT7921 o BE200 (M.2), o un nRF5340 DK.
2. **Transmitir mono a un Go 4** con Bumble y el `--manufacturer-data` de Harman,
   para reproducir el resultado ya conocido.
3. **La prueba decisiva:** transmitir estéreo (2 BIS, FL y FR) y ver qué reproduce
   cada Go 4. Después, como Broadcast Assistant, escribir `BIS_Sync` por BASS para
   forzar a un parlante al BIS 1 y a otro al BIS 2. Si funciona, el proyecto es
   viable con sincronización garantizada por el estándar.
4. Si el paso 3 funciona, extenderlo a 4 BIS en un solo BIG (modificando Bumble o
   usando PipeWire con `bis[]`) y medir la alineación con un micrófono.
