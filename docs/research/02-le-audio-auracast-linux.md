# LE Audio y Auracast desde Linux: stack, hardware y multicanal

Investigación del 2026-09-25. Es una de las tres líneas de la fase de investigación
(ver [README](README.md)).

**Qué significa cada marca:**
- **VERIFICADO**: leído en una fuente primaria (documentación oficial, código fuente,
  notas de versión, documentos del Bluetooth SIG).
- **REPORTADO**: blog, foro o issue.
- **INFERIDO**: deducción que nadie comprobó.

Nada de esto se ha medido todavía con los parlantes propios.

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

| Controlador | Transmisión (BIS) | Unicast (CIS) | Estado | Fuente |
|---|---|---|---|---|
| **MediaTek MT7921** | **Sí** (probado con JBL Go 4) | ? | REPORTADO | Collabora 2026-05 |
| Intel BE200 | Sí (LE Audio + Auracast) | Sí | REPORTADO | Collabora 2025-11 |
| Intel AX1xx | No | Sí | REPORTADO | Collabora 2025-11 |
| Intel AX210 | Según reportes, el firmware solo soporta CIS | Sí (Pi 5, BlueZ 5.83, PipeWire 1.4.6) | REPORTADO | [ak-experiments](https://ak-experiments.blogspot.com/2025/08/bluetooth-le-audio-on-raspberry-pi-with.html), [Intel community](https://community.intel.com/t5/Wireless/Intel-Bluetooth-Isochronous-channels-and-other-mandatory/m-p/1583602) |
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

## Lo que no se pudo determinar

- Si los JBL eligen un BIS según su ubicación o respetan `BIS_Sync` por BASS, y si
  el modo estéreo del Charge 6 usa un par de BIS L/R.
- El estado actual del firmware BIS en Intel AX210 y AX211 (las páginas de Intel
  devolvieron 403).
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
