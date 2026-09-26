# Opción A en profundidad: Python + Google Bumble + liblc3

Investigación del 2026-09-25. Profundiza la opción (a) de
[04-implementaciones-y-stacks.md](04-implementaciones-y-stacks.md). Se leyó el
código de google/bumble en el commit `745d607` (2026-09-25, versión v0.0.235), y
los números de línea se refieren a ese commit. El código de Zephyr citado es del
commit `e560c91`.

**Qué significa cada marca:**
- **VERIFICADO**: leído en el código o en la documentación.
- **REPORTADO**: issue o foro.
- **INFERIDO**: deducción que nadie comprobó.

## Resumen

- **Bumble puede usar el controlador interno del laptop** con el transporte
  `hci-socket:N`. El kernel carga el firmware (btintel, btmtk, btrtl), el socket
  deja pasar paquetes ISO, y funciona también con MediaTek MT7921. **Si el chip del
  equipo tiene `iso-broadcaster`, no hace falta comprar nada para empezar.**
- Hay que **detener BlueZ mientras se usa Bumble**: Bumble toma el controlador en
  exclusiva.
- **Para 4 canales con distinta ubicación en un solo BIG, el parche es pequeño.**
  El bucle de envío ya reparte cada canal a su BIS; lo que falta es permitir más de
  2 canales, construir bien la BASE (índices globales y una ubicación por BIS) y
  exponer como opciones el presentation delay y la QoS.
- **Riesgos que hay que medir:**
  - **Bumble no envía timestamps en los paquetes ISO.** Nordic advierte que la
    sincronización entre streams "cannot be reliably achieved using time of
    arrival". Dos BIS del mismo BIG podrían quedar desfasados en un intervalo de
    SDU (10 ms) (INFERIDO).
  - **No hay remuestreo ni manejo del drift.** Con entrada desde archivo o stdin
    no importa, porque el controlador marca el ritmo. Con entrada desde un
    dispositivo de sonido hay dos relojes, y la cola termina desbordando o
    quedándose vacía (INFERIDO).
- **El asistente BASS existe, pero es limitado.** Hoy sincroniza a cualquier BIS
  del subgrupo 0; para elegir uno concreto hay que parchearlo. Además, `transmit`
  y `assist` no pueden compartir el controlador desde la línea de comandos: hace
  falta un segundo controlador o un script propio en un solo proceso.
- **No hay reportes de que un JBL exponga BASS o PACS.** La documentación de Bumble
  describe que los JBL se enganchan solos a la transmisión, con el botón Auracast y
  sin conexión clásica.

## 1. Controladores y transportes

### `hci-socket:N` (HCI user channel): el camino recomendado para empezar
VERIFICADO en `bumble/transport/hci_socket.py` y en `net/bluetooth/hci_sock.c` del
kernel.

- El **kernel carga el firmware** (btintel, btmtk, btrtl), así que funciona con el
  controlador interno del laptop, **MediaTek MT7921 incluido**.
- Para enlazarse al socket se necesita `CAP_NET_ADMIN`. Falla con `EBUSY` si
  `hciN` está activo, y con `EUSERS` si otro proceso ya tiene el canal.
- El kernel deja pasar paquetes ISO por el user channel en ambos sentidos
  (`hci_sock_sendmsg` y `hci_send_to_sock` en hci_sock.c).
- Pasos, según la documentación en `platforms/linux.md`. **Es un cambio de
  sistema**, así que la reversión va anotada:
  ```bash
  sudo systemctl stop bluetooth      # revertir: sudo systemctl start bluetooth
  sudo hciconfig hci0 down           # revertir: sudo hciconfig hci0 up
  sudo <venv>/bin/bumble-... hci-socket:0
  # sin sudo: capsh --caps="cap_net_admin+eip ..." (línea documentada)
  # si está bloqueado: rfkill unblock bluetooth   (INFERIDO)
  ```

### `usb:N` o `usb:VID:PID`
VERIFICADO en `transport/usb.py:932`.

- Usa libusb1 y **desconecta el driver del kernel automáticamente**. Hace falta
  permiso de escritura sobre `/dev/bus/usb/BBB/DDD` (chmod o una regla de udev).
- Los paquetes ISO van por el endpoint bulk-out (`usb.py:355`). **No hay que
  agregar `+sco=`**: con SCO activado, los paquetes ISO se descartan con el aviso
  "ISO over Isochronous endpoints not supported yet".

### Otros transportes y drivers
- **Otros transportes** (VERIFICADO): `serial:` (H4 por UART), `pyusb:`, `vhci`,
  `tcp-*`, `udp`, `ws-*`, `android-netsim`, `android-emulator`, `unix`, `pty`,
  `file`.
- **El controlador virtual no transporta audio.** Su
  `on_hci_le_create_big_command` es un stub que no reenvía datos ISO
  (`controller.py:3929`). Sirve para probar la BASE y los anuncios, pero no pasa
  audio. Coincide con el issue #661, donde un loopback por ws-server no dio audio.
- **Drivers de firmware:** solo existen para **Intel** y **Realtek**
  (`drivers/__init__.py`, VERIFICADO).
  - El de Intel carga firmware `.sfi` para AX210 (8087:0032), AX211 (0033) y BE200
    (0036), desde el directorio de datos de Bumble, desde `/lib/firmware/intel` o
    con `bumble-intel-fw-download` (`drivers/intel.py:51-75,145-190`).
  - **MediaTek MT7921 no tiene driver en Bumble**, así que con ese chip solo sirve
    `hci-socket`.

### ¿Qué controladores funcionan con `bumble-auracast transmit`?
- **La documentación no nombra ninguno.** Dice que la recepción en el Go 4
  funciona, pero no qué adaptador se usó.
- Los issues #745 y #697 usaron `serial:/dev/ttyACM0` con una placa sin nombre. En
  el #745 aparecieron PDUs de BIS vacíos en una captura Ellisys y nunca se resolvió
  (REPORTADO).
- La demo de Collabora solo usó Bumble para `scan`, con un ASUS BT540 (REPORTADO).

### ¿Un dongle nRF como controlador barato para Bumble?
**Es plausible, pero nadie ha reportado que funcione.**

> **Corrección ([06](06-opcion-c-nrf5340.md) §5):** `hci_usb` **no** sirve, porque
> Zephyr no transporta ISO por USB bulk
> ([issue #44013](https://github.com/zephyrproject-rtos/zephyr/issues/44013),
> abierto desde 2022). Lo que sí sirve es **`hci_uart` por USB CDC-ACM**, con el
> transporte `serial:` de Bumble. Las opciones de configuración ISO que se listan
> abajo siguen valiendo para `hci_uart`.

- **El controlador libre de Zephyr** (`BT_LL_SW_SPLIT`) soporta transmisión ISO,
  incluido el cifrado de BIS por software, en todos los SoC nRF. Está marcado
  EXPERIMENTAL (VERIFICADO: `Kconfig.ll_sw_split:43-45` y `Kconfig:895-972`).
- **Configuración:**
  - El `prj.conf` estándar de `hci_usb` no trae opciones ISO (VERIFICADO).
  - Se pueden copiar de `hci_ipc/extra-iso_broadcast-bt_ll_sw_split.conf` o de
    `hci_uart/overlay-all-bt_ll_sw_split.conf`: `CONFIG_BT_ISO_BROADCASTER`,
    `BT_CTLR_ADV_ISO`, `BT_CTLR_ADV_PERIODIC` y los buffers ISO de transmisión.
  - Esos archivos fijan **`CONFIG_BT_CTLR_ADV_ISO_STREAM_MAX=2`**. Hay que subirlo
    a 4 o más (el rango es 1–31) (VERIFICADO).
- **El SoftDevice Controller de Nordic** soporta transmisión ISO, pero en nRF52 solo
  el nRF52820 y el nRF52833 cifran ISO (VERIFICADO, README del SDC).
  - Coincide con el issue #787 (abierto): al dongle nRF52840 "le faltaba cifrado de
    BIS", y el nRF5340 Audio DK respondió `HCI_UNKNOWN_HCI_COMMAND_ERROR`
    (REPORTADO).
  - **Una transmisión sin cifrar no necesita cifrado ISO.**
- En un hilo de DevZone, hci_uart en nRF5340 solo pudo escanear después de cambiar
  a sysbuild (REPORTADO).

La opción C trata este tema en detalle: [06-opcion-c-nrf5340.md](06-opcion-c-nrf5340.md).

## 2. Cómo funciona `apps/auracast.py` por dentro

Todo lo de esta sección está VERIFICADO en el código.

### Subcomandos y opciones
- **Subcomandos:** `scan`, `receive`, `transmit`, `assist` (con `monitor-state`,
  `add-source`, `modify-source` y `remove-source`) y `pair`.
- **Opciones de `transmit`:** `--input`, `--input-format`, `--broadcast-id`,
  `--broadcast-code`, `--broadcast-name`, `--bitrate` (por canal, 80 kbps por
  defecto), `--manufacturer-data VID:HEX` y `--broadcast-list`.
- **El VID se interpreta como entero decimal** (`:1453`), así que `87` es 0x0057,
  Harman.

### Entradas de audio (`bumble/audio/io.py`)
- **WAV** con el módulo `wave`: solo int16, vuelve a empezar al llegar al final y
  lee cualquier cantidad de canales.
- **PCM crudo** desde archivo o `stdin`, con formato `int16le|float32le,tasa,canales`.
- **`device`**, con el paquete `sounddevice` (PortAudio). **Siempre entrega 2
  canales**, y el mono lo duplica con un bucle en Python puro.
- No hay entrada de ffmpeg, pero se puede pasar ffmpeg por una tubería a stdin.

### Valores fijos en el código
- Tasas de 16, 24 o 48 kHz (`:977`); **solo 1 o 2 canales por fuente** (`:962`).
- Frames de 10 ms (`:67`, `DURATION_10000_US` en `:1060`).
- **Presentation delay de 40000 µs** (`:1052`).
- `max_transport_latency=65`, `rtn=4` (`:1148`).
- Valores por defecto de `BigParameters`: PHY 2M, empaquetado SEQUENTIAL,
  UNFRAMED (`device.py:1040`).
- Dirección de anuncio fija F0:F1:F2:F3:F4:F5 (`:64`).

### Estructura de la transmisión
- Cada `Broadcast` tiene un set de anuncios y **un BIG con
  `num_bis=channel_count`** (`:1142`). La BASE tiene un subgrupo por fuente.
- Estéreo: BIS 1 = FRONT_LEFT y BIS 2 = FRONT_RIGHT.
- Mono: índice 1, FRONT_LEFT. **Dos fuentes mono generan dos veces el índice 1,
  lo que hace inválida la BASE**, porque los índices no son globales
  (`:1063-1088`).
- **Los datos de fabricante** van en los anuncios extendidos, después del nombre,
  la apariencia y el broadcast name (`:1103`). No van en los anuncios periódicos.

### Bugs del camino TOML (`--broadcast-list`)
- El parser arma la tupla de datos de fabricante, pero pasa el dict original
  (`:134-147`).
- El `examples/auracast_broadcasts.toml` que viene con Bumble pone
  `manufacturer_data` dentro de `sources`, donde el parser nunca lo lee.

### El parche para N BIS mono con ubicaciones distintas en un solo BIG
Es un parche pequeño:
1. Permitir más de 2 canales (`:962`).
2. Usar `lc3.Encoder(num_channels=N)`. El wrapper de liblc3 soporta multicanal
   intercalado y devuelve los frames concatenados.
3. Armar **un solo subgrupo** con BIS `index=i+1`, y la `audio_channel_allocation`
   de cada uno tomada de una opción nueva `--channel-map`. Todas las ubicaciones
   necesarias existen en `bap.AudioLocation`: FRONT_LEFT, FRONT_RIGHT,
   BACK_LEFT/SIDE_LEFT, BACK_RIGHT/SIDE_RIGHT, FRONT_CENTER y
   LOW_FREQUENCY_EFFECTS_1.
4. Exponer como opciones el presentation delay, el RTN, la latencia, el PHY y el
   empaquetado.
5. Corregir el camino TOML.

**El bucle de envío (`:1168-1189`) ya reparte el frame de cada canal a colas de
BIS consecutivas, así que no hay que tocarlo.**

Entrada de prueba: un WAV de 4 canales, o ffmpeg por una tubería:
```bash
ffmpeg -i fuente.flac -f s16le -ar 48000 -ac 4 - | \
  bumble-auracast transmit ... --input stdin --input-format int16le,48000,4
```

**Tiempo de radio** (INFERIDO): 4 BIS de 100 B con RTN 4 en PHY 2M quedan cerca del
límite del intervalo ISO de 10 ms. Si Create BIG falla, se baja el bitrate o el
RTN.

## 3. Asistente de transmisión (BASS)

- **El cliente existe** (VERIFICADO): `bass.BroadcastAudioScanServiceProxy` tiene
  `add_source`, `modify_source` y `remove_source`.
- **`assist add-source` es limitado** (VERIFICADO, `:680-695`):
  - Usa `SubgroupInfo.ANY_BIS` y solo el subgrupo 0. Para elegir un BIS hay que
    parchearlo con `bis_sync = 1 << (idx-1)`.
  - Hace su propio escaneo y después un PA Sync Transfer.
  - No tiene una función para Set Broadcast Code. Habría que llamar a
    `send_control_point_operation(SetBroadcastCodeOperation(...))`.
- El lado servidor es un stub (VERIFICADO).
- **`transmit` y `assist` a la vez:** cada comando abre su propio transporte, y
  tanto el user channel como el USB son exclusivos. Hace falta **un segundo
  controlador, o un script propio que haga las dos cosas en un solo proceso**. Que
  un mismo controlador mantenga a la vez una conexión, los anuncios periódicos y el
  BIG depende del controlador (INFERIDO).
- **No hay reportes de un JBL que exponga BASS o PACS.** La documentación dice que
  los receptores JBL se enganchan solos, y el único dispositivo que nombra como
  controlado por un asistente son los Pixel Buds Pro 2 (VERIFICADO en la
  documentación; el lado JBL no se pudo determinar).

## 4. Rendimiento y plataforma

- **lc3py llama a la biblioteca C liblc3 por ctypes**; los wheels `py3-none` traen
  el `.so`. Solo hay wheels para manylinux x86_64 y macOS 14 arm64 (v1.1.3,
  2025-02) (VERIFICADO, PyPI).
- **En aarch64 o Raspberry Pi:** `pip install git+https://github.com/google/liblc3.git`.
  Necesita meson-python y un compilador de C. No encontré reportes en una Pi
  (INFERIDO).
- **No hay benchmarks de CPU.** Codificar 4×48 kHz en C debería ser liviano; lo
  desconocido es el costo de Python por cada frame, y hay que medirlo (INFERIDO).
- **Control de flujo ISO** (VERIFICADO): Bumble lee `LE_Read_Buffer_Size_V2` para
  dimensionar una `DataPacketQueue` ISO en el host (`host.py:550-595`).
  `IsoPacketStream` permite 64 SDUs en vuelo por BIS, que se liberan con los
  eventos Number-of-Completed-Packets (`device.py:1728`). Con entrada desde archivo
  o stdin, el controlador marca el ritmo con esa contrapresión y se pueden acumular
  hasta ~640 ms de audio en cola (INFERIDO).
- **Requisitos:** Python 3.10 o superior (`pyproject.toml:13`). `create_big` está
  marcado `@experimental('Only for testing.')`.
- **Madurez:** versiones anteriores a 1.0, que salen cada 1 a 6 semanas (de la
  0.0.224 a la 0.0.235 entre febrero y septiembre de 2026) (VERIFICADO).

## 5. Sincronización

- **Bumble no envía timestamps** (TS_Flag=0). Lleva un `packet_sequence_number`
  por enlace que sube de uno en uno con cada SDU (`host.py:893-941`) (VERIFICADO).
- `HCI_IsoDataPacket` sí soporta `time_stamp` (`hci.py:8307-8314`), y
  `BisLink.get_tx_time_stamp()` envía `LE_Read_ISO_TX_Sync` (`device.py:1593`)
  (VERIFICADO). O sea, **agregar timestamps es posible**.
- Nordic documenta que la sincronización entre streams solo está garantizada con
  timestamps compartidos, y que "cannot be reliably achieved using time of arrival"
  (VERIFICADO).
- **Riesgo** (INFERIDO): los BIS de un mismo BIG podrían quedar a un intervalo de
  SDU (10 ms) de distancia. Hay que medirlo con micrófono y agregar timestamps si
  hace falta.
- **No hay remuestreo ni manejo del drift** (VERIFICADO):
  - con entrada desde archivo o stdin, el ritmo lo marca el reloj del controlador;
  - con entrada desde dispositivo hay dos relojes, así que la cola terminará
    desbordando (aviso "input overflow") o vaciándose (INFERIDO).

  **Consecuencia para el prototipo:** conviene alimentarlo desde un sink virtual de
  PipeWire por stdin, de modo que PipeWire siga el ritmo de la salida, o agregar
  remuestreo adaptativo.

## 6. Otros proyectos e issues

- **No encontré ningún fork de Bumble que haga Auracast multicanal.**
- Collabora (MT7921 + BlueZ) confirma el mismo requisito de los datos de fabricante
  de Harman terminados en `…dffd` (REPORTADO).
- Issues relevantes:

  | Issue | Qué muestra |
  |---|---|
  | [#782](https://github.com/google/bumble/issues/782) | Dos subgrupos con audio en uno solo; cerrado sin respuesta |
  | [#745](https://github.com/google/bumble/issues/745) | PDUs de BIS vacíos |
  | [#787](https://github.com/google/bumble/issues/787) | Error con el nRF5340 |
  | [#894](https://github.com/google/bumble/discussions/894) | El Clip 5 necesitó 80 ms de presentation delay |
  | [#620](https://github.com/google/bumble/issues/620) | Pide hooks para marcar el ritmo del envío ISO |
- Google usa Bumble en las pruebas Pandora/Avatar de Android (REPORTADO). No se
  verificó para transmisión LE Audio.

## 7. Primeros experimentos por este camino

Corresponden a E1–E4 del [roadmap](../roadmap.md). Los resultados van a
`experimentos/`.

1. **Instalar:**
   ```bash
   python3.11 -m venv v && v/bin/pip install "bumble[auracast]"
   sudo apt install libportaudio2     # opcional, solo para --input device
   ```
2. **Revisar el controlador (E1):**
   ```bash
   sudo systemctl stop bluetooth; sudo hciconfig hci0 down
   sudo v/bin/bumble-controller-info hci-socket:0
   ```
   Buscar en las funciones LE "ISO Broadcaster" y "Periodic Advertising", y la
   línea "LE ISO Flow Control". Con un dongle se usa `bumble-usb-probe` y `usb:0`.
3. **Leer un JBL (E2):** poner un Go 4 a transmitir Auracast y correr
   `bumble-auracast scan hci-socket:0`. Anotar como MEDIDO, junto con la versión de
   firmware, los datos de fabricante, la BASE (delay, cantidad de BIS y
   ubicaciones) y el BIGInfo (PHY, framing, intervalo).
4. **Mono (E3):**
   ```bash
   bumble-auracast transmit hci-socket:0 --input file:mono48k.wav \
     --broadcast-name T --manufacturer-data 87:00000000000000000000000000000000dffd
   ```
   El parlante entra en modo receptor al presionar el botón Auracast sin estar
   reproduciendo y sin conexión clásica. Si no suena, parchear el delay a 80000.
5. **2 BIS sin parche (E4a):** un WAV estéreo con L = 440 Hz y R = 880 Hz. Ver qué
   BIS reproduce cada Go 4 y el Charge 6, y medir con micrófono.
6. **Asistente desde un segundo controlador (E4b):**
   ```bash
   bumble-auracast pair <t2> <JBL-addr>
   bumble-auracast assist --command monitor-state <t2> <JBL-addr>   # ¿tiene BASS?
   bumble-auracast assist --command add-source --broadcast-name T <t2> <JBL-addr>
   ```
7. **Solo después:** el parche de 4 BIS en `probes/`, más una revisión de
   `get_tx_time_stamp` por BIS (E5).

## Lo que no se pudo determinar

- Qué controlador usaron los mantenedores de Bumble con el Go 4.
- Si los JBL exponen BASS, o qué BIS eligen de una BASE con varios BIS.
- Algún caso exitoso de Bumble con nRF52840 para Auracast.
- Cifras de CPU en x86 o en una Pi.
- Cómo cada controlador asocia los números de secuencia a los eventos del BIG.

## Fuentes

- Código de Bumble (commit 745d607): https://github.com/google/bumble
  (`apps/auracast.py`, `bumble/device.py`, `host.py`, `hci.py`, `audio/io.py`,
  `profiles/bap.py`, `bass.py`, `drivers/`, `transport/usb.py`, `hci_socket.py`,
  `controller.py`, `examples/auracast_broadcasts.toml`, `pyproject.toml`)
- Documentación: https://google.github.io/bumble/apps_and_tools/auracast.html,
  …/platforms/linux.html, …/platforms/zephyr.html, …/hardware/index.html,
  …/drivers/intel.html, …/drivers/realtek.html
- Issues y discusiones: https://github.com/google/bumble/discussions/894 y los
  issues #787, #782, #745, #697, #698, #661, #620
- Kernel: https://github.com/torvalds/linux/blob/master/net/bluetooth/hci_sock.c
- Zephyr (e560c91): `samples/bluetooth/hci_usb`,
  `hci_uart/overlay-all-bt_ll_sw_split.conf`,
  `hci_ipc/extra-iso_broadcast-bt_ll_sw_split.conf`,
  `subsys/bluetooth/controller/Kconfig`, `Kconfig.ll_sw_split`
- SoftDevice Controller de Nordic:
  https://github.com/nrfconnect/sdk-nrfxlib/blob/main/softdevice_controller/README.rst,
  https://nrfconnectdocs.nordicsemi.com/ncs/latest/nrfxlib/softdevice_controller/doc/isochronous_channels.html,
  https://devzone.nordicsemi.com/f/nordic-q-a/121293/
- liblc3: https://github.com/google/liblc3 (`python/lc3.py`, `pyproject.toml`),
  https://pypi.org/project/lc3py/, https://pypi.org/project/bumble/
- Collabora: https://www.collabora.com/news-and-blog/blog/2026/05/05/bluez-powered-auracast-broadcasting-on-genio-700/
