# Opción C en profundidad: una placa Nordic como emisor Auracast

Investigación del 2026-09-25/26. Profundiza la opción (c) de
[04-implementaciones-y-stacks.md](04-implementaciones-y-stacks.md). Fuentes leídas:
- sdk-nrf `main` @5d1f559 (NCS 3.4.99; las últimas notas de versión son v3.4.1 y
  3.5.0-preview2);
- zephyr `main` @e560c91;
- sdk-nrfxlib `main`.

**Qué significa cada marca:**
- **VERIFICADO**: leído en el código o en la documentación.
- **REPORTADO**: foro (DevZone) o proveedor.
- **INFERIDO**: deducción que nadie comprobó.

Los precios son de DigiKey por unidad, vistos el 2026-09-25.

## Resumen

- **El hardware y el controlador soportan 4 BIS en un BIG** (VERIFICADO), y el
  controlador de Nordic manda cada frame con un **timestamp del controlador**
  (VERIFICADO). Así se evita el riesgo de alineación que tiene Bumble
  ([05](05-opcion-a-bumble.md) §5).
- **Ninguna app trae a la vez entrada USB de 4 canales y los datos de fabricante de
  JBL.** Lo que existe:

  | App | Qué ofrece | Qué le falta |
  |---|---|---|
  | **`nrf_auraconfig`** | Hasta 2 BIG × 4 BIS, **ubicación por BIS** y presentation delay configurables por shell | El audio sale solo de archivos `.lc3` en tarjeta SD, y solo corre en el nRF5340 Audio DK |
  | **`nrf_audio` broadcast_source** | Entrada USB desde el PC | Limitado a 1 BIG × 2 BIS y USB 48 kHz estéreo |
  | **Zephyr `bap_broadcast_source`** | Apache-2.0, con entrada USB | La entrada USB está fija en 2 canales |
- **La prueba decisiva más barata por este camino:** un nRF5340 Audio DK con
  `nrf_auraconfig`, unas 15 líneas para agregar los datos de fabricante y 4
  archivos LC3, uno por canal. **No necesita audio desde el PC.**
- **Como controlador para Bumble o BlueZ:** `hci_usb` **no** sirve, porque no
  transporta ISO. **`hci_uart` sí**, también por USB CDC-ACM en el dongle nRF52840.
- **Las licencias importan:**
  - `nrf_audio` usa archivos Packetcraft que **no se pueden modificar** y un códec
    LC3 binario.
  - **Una versión pública conviene basarla en los samples de Zephyr** (Apache-2.0,
    liblc3).

## 1. Hardware

| Placa | Precio | Emisor ISO | USB para entrada de audio | Depurador integrado |
|---|---|---|---|---|
| **nRF5340 Audio DK** | US$172.57 (VERIFICADO) | SDC, en el núcleo de red | Sí, full-speed | Sí (VERIFICADO, doc de la placa en Zephyr) |
| nRF5340 DK | US$48.95 (VERIFICADO) | SDC | Sí | Sí |
| **nRF52840 Dongle** | US$11.69 (VERIFICADO) | SDC; ISO del nRF52840 figura como "Supported" en la tabla de madurez de NCS (VERIFICADO) | Sí; hay una configuración de placa para el sample de Zephyr (VERIFICADO) | No, solo USB DFU |
| Seeed XIAO nRF52840 (`xiao_ble`) | ~US$10–15 sola (REPORTADO); el kit Meshtastic con Wio-SX1262 cuesta más y la radio LoRa no sirve aquí | El mismo chip que el dongle, así que el mismo controlador (INFERIDO) | USB-C con CDC-ACM (VERIFICADO, doc de la placa en Zephyr) | No: bootloader UF2 de Adafruit; para depurar hace falta un J-Link o Black Magic por los pads SWD (VERIFICADO) |
| SuperMini nRF52840 (clon de nice!nano, `promicro_nrf52840`) | ~US$3–6 (REPORTADO) | El mismo chip que el dongle (INFERIDO) | Sí, USB-C (VERIFICADO, doc de la placa en Zephyr) | No: bootloader UF2 de Adafruit; tiene pads SWD atrás (VERIFICADO) |
| nRF54LM20 DK | US$45.00 (VERIFICADO) | SDC | USB (capaz de HS, usado a FS) | J-Link |
| Raytac MDBT53-DB-40 (nRF5340) | ~US$17.50 (REPORTADO) | SDC | Sí | No (INFERIDO) |

- `nrf_audio` compila para `nrf5340_audio_dk`, `nrf5340dk` y `nrf54lm20dk`
  (VERIFICADO, `boards/`). En el nRF54LM20 solo soporta entrada USB (VERIFICADO).
- La configuración del sample de Zephyr para el dongle nRF52840 usa el preset
  16_2_1, mientras que la del nRF5340 DK usa 48_2_1 (VERIFICADO). **Eso sugiere que
  el CPU del nRF52840 queda justo para LC3 a 48 kHz** (INFERIDO).
- **La XIAO nRF52840 reemplaza al dongle** para la opción combinada y para E2
  (INFERIDO): mismo chip, 256 KiB de RAM, 1 MiB de flash, y Zephyr la soporta
  como `xiao_ble` (VERIFICADO). Diferencias:
  - no hay un `boards/xiao_ble.conf` en el sample `hci_uart`; hay que copiar el del
    dongle (`nrf52840dongle_nrf52840.conf`) (INFERIDO, no revisado archivo por
    archivo);
  - se flashea copiando un `.uf2` tras doble toque en reset, sin `nrfutil`
    (VERIFICADO);
  - la variante Sense agrega IMU y micrófono PDM, que aquí no aportan.
- **La SuperMini nRF52840 también sirve como controlador** (INFERIDO), con más
  riesgo que la XIAO:
  - es un clon de fabricantes variados, así que la calidad cambia de un lote a
    otro (REPORTADO);
  - hay reportes de cristales de 32,768 kHz que fallan; se arregla por firmware
    usando el oscilador RC (REPORTADO, wiki de joric/nrfmicro). Con RC el
    controlador anuncia una precisión de reloj peor (hasta 500 ppm), algo legal
    para un BIG, pero **cualquier medición de sincronía con esta placa tiene que
    anotar qué fuente de reloj de baja frecuencia usó** (INFERIDO);
  - Zephyr la cubre con el target genérico `promicro_nrf52840` (VERIFICADO).
  - la wiki del vendedor (icbbuy) confirma USB-C, bootloader UF2 de nice!nano y
    antena en la placa, pero **no dice si lleva cristal de 32,768 kHz**; el
    esquemático es un aporte de la comunidad (VERIFICADO, 2026-09-26);
  - el footprint KiCad del vendedor (`.kicad_mod`, versión 20221018) trae solo
    pads: los pines Pro Micro, el USB-C y, atrás, 4 pads SMD en columna a 1,5 mm y
    2 pads más. **No trae componentes, así que no responde lo del cristal.** Que los
    4 pads sean SWD es INFERIDO (la doc de Zephyr habla de pads de depuración
    atrás); no tienen nombre de señal (VERIFICADO, 2026-09-26);
  - el "Bluetooth 5.0" de la publicidad no descarta Auracast: ISO lo da el
    firmware del controlador, no el silicio, y el nRF52840 figura con ISO en la
    tabla de NCS (INFERIDO a partir de la fila del dongle).
- No revisados: Thingy:53, Adafruit. El nRF54L15 no tiene USB.

## 2. Entrada de audio desde el PC

### `nrf_audio`
- `audio_usb.h` tiene `#error USB only supports 48kHz stereo` (VERIFICADO).
- `AUDIO_INPUT_CHANNELS` tiene `range 1 2` (VERIFICADO).
- El nodo UAC2 del devicetree declara solo front-left y front-right, 16 bits,
  `sof-synchronized` (VERIFICADO).
- **Para pasar a 4 canales habría que editar** el Kconfig, el `#error`, el overlay
  UAC2, `CONFIG_LC3_ENC_CHAN_MAX` y `CONFIG_AUDIO_ENCODE_CHANNELS_MAX` (hoy en 2), y
  `broadcast_source_default_create()`, que tiene 2 ubicaciones fijas
  (VERIFICADO/INFERIDO).
- **Advertencia:** un usuario de DevZone modificó `subgroup_send()` para enviar 2
  streams distintos y **obtuvo el mismo audio en ambos** (hilo 128676, NCS v3.4.0,
  sin resolver) (REPORTADO).

### Zephyr `bap_broadcast_source`
- `#define USB_CHANNELS 2U`, y solo tiene un diezmado simple desde 48 kHz
  (VERIFICADO).
- Su overlay dice: "Falsely claim synchronous audio because we currently don't
  calculate feedback value" (VERIFICADO).

### El driver USB y otras entradas
- **`usbd_uac2` por sí mismo soporta muchas posiciones de canal**
  (FL/FR/FC/LFE/BL/BR…) y feedback explícito. Hay un sample
  `samples/subsys/usb/uac2_explicit_feedback` para el nRF5340 DK (VERIFICADO).
- **Ancho de banda:** 4 canales × 48 kHz × 16 bits son 384 B/ms, dentro de los
  límites del modo isócrono full-speed (INFERIDO).
- **Otras entradas:** I2S (`CONFIG_AUDIO_SOURCE_I2S`, 2 canales) (VERIFICADO). Enviar
  LC3 ya codificado por CDC-ACM requeriría código propio (INFERIDO).
- **Latencia agregada por el USB:** no encontré una cifra medida. `nrf_audio`
  recibe bloques USB de 1 ms y arma frames de 10 ms (VERIFICADO). Estimación: unos
  10–20 ms antes del presentation delay (INFERIDO).

## 3. Varios BIS con una ubicación cada uno

### Configuración
- **Kconfig de la cantidad de BIS** (VERIFICADO):
  - host: `BT_BAP_BROADCAST_SRC_STREAM_COUNT`,
    `BT_BAP_BROADCAST_SRC_SUBGROUP_COUNT`, `BT_ISO_MAX_CHAN`;
  - SoftDevice Controller (`sysbuild/ipc_radio/prj.conf` del núcleo de red):
    `BT_CTLR_ADV_ISO_SET`, `BT_CTLR_ADV_ISO_STREAM_COUNT`;
  - controlador libre de Zephyr: `BT_CTLR_ADV_ISO_STREAM_MAX` (rango 1–31) y
    `BT_CTLR_ISOAL_SOURCES`.
- **Ubicación por BIS:** se fija con codec data a nivel de BIS
  (`BT_AUDIO_CODEC_CFG_CHAN_ALLOC`) en los parámetros del stream (VERIFICADO,
  `setup_broadcast_source()`).
- **`nrf_auraconfig`** (VERIFICADO):
  - soporta 2 BIG × 4 BIS (`CONFIG_BT_ISO_MAX_CHAN=8`,
    `BT_BAP_BROADCAST_SRC_STREAM_COUNT=8`; en el núcleo de red,
    `BT_CTLR_ADV_ISO_STREAM_COUNT=8`);
  - la ubicación se fija con `nac location fl 0 0 0`;
  - el presentation delay, con `nac pd <us> <BIG> <subgrupo>`;
  - la fuente de audio es solo `.lc3` en SD (`CONFIG_SW_CODEC_NONE`), y solo corre
    en el nRF5340 Audio DK.

### Tiempo de radio
- El SDC reserva 2.5 ms de cada intervalo ISO para los anuncios periódicos
  (`BT_CTLR_SDC_BIG_RESERVED_TIME_US`). Trata el RTN y la latencia como límites
  superiores (VERIFICADO, `isochronous_channels.rst`). **El SDC solo prueba
  configuraciones BAP de 2 BIS** (VERIFICADO).
- Cálculo (INFERIDO):
  - un SDU de 100 B en PHY 2M ocupa ~444 µs en el aire, es decir, ~594 µs por
    subevento;
  - en un intervalo de 10 ms quedan 7.5 ms libres, así que **4 BIS con 48_2_1
    caben con unas 3 transmisiones cada uno (RTN≈2, no el 4 del preset)**;
  - 48_4_1 da unas 2 transmisiones, y 48_6_1 apenas cabe.
- **La propia transmisión del JBL Go 4 usa un intervalo ISO de 20 ms con BN=2**
  (REPORTADO, DevZone 127785). Zephyr puede imitarlo con
  `CONFIG_BT_ISO_TEST_PARAMS` (iso_interval/irc/pto/num_subevents en
  `bap_broadcast_source.c`) (VERIFICADO), y el SDC soporta "LE Create BIG Test"
  (VERIFICADO, CHANGELOG).
  Esto también sirve para la opción A: **imitar los parámetros del propio JBL**
  puede mejorar la compatibilidad (INFERIDO).

## 4. Datos de fabricante y presentation delay

### Dónde se arma el anuncio (VERIFICADO)
- `nrf_auraconfig`: `ext_adv_populate()` en `nrf_auraconfig.c`.
- `nrf_audio`: `ext_adv_populate()` en `broadcast_source/main.c` y
  `broadcast_source_ext_adv_populate()`, que terminan en `bt_le_ext_adv_set_data()`
  dentro de `bt_mgmt_adv.c`.
- Sample de Zephyr: un arreglo `ext_ad[3]` en `main.c`.

### Lo que hay que agregar
- **El campo "manufacturer" que ya existe no es lo que necesita JBL.** El
  `CONFIG_BT_DEVICE_MANUFACTURER_ID=0xFE58` de Nordic se agrega como UUID16, no como
  datos de fabricante (VERIFICADO).
- Hay que añadir un elemento `BT_DATA_MANUFACTURER_DATA` con `0x57,0x00` seguido del
  payload (INFERIDO; es trivial).

### Presentation delay
- `nrf_audio`: `CONFIG_BT_AUDIO_PRESENTATION_DELAY_US`. Su valor por defecto es
  `AUDIO_MIN_PRES_DLY_US` = 3000 µs (VERIFICADO; sorprendentemente bajo, conviene
  revisarlo en el `.config` compilado). El máximo está limitado por
  `AUDIO_MAX_PRES_DLY_US` = 60000, así que **para 80 ms también hay que subir ese
  límite** (VERIFICADO).
- Los presets BAP de Zephyr usan 40000 µs (VERIFICADO, `bap_lc3_preset.h`).
- `nrf_auraconfig`: se fija con `nac pd`.

## 5. Una placa Nordic como controlador HCI para BlueZ o Bumble en Linux

- **`hci_usb`: no sirve.** El `bt_hci.c` de Zephyr manda a ACL todo lo que llega por
  bulk OUT (VERIFICADO), y el issue
  [#44013 "HCI ISO over USB"](https://github.com/zephyrproject-rtos/zephyr/issues/44013)
  está abierto desde 2022 (REPORTADO). **Esto corrige lo que se sugirió en
  [05](05-opcion-a-bumble.md) §1.**
- **`hci_uart`: funciona, también por USB CDC-ACM en el dongle nRF52840**
  (`boards/nrf52840dongle_nrf52840.conf`) (VERIFICADO).
  - Con el controlador de Zephyr, `overlay-all-bt_ll_sw_split.conf` activa
    `BT_ISO_BROADCASTER` y `CTLR_ADV_ISO` (VERIFICADO).
  - El SDC expone LE Create BIG por HCI (VERIFICADO).
- **Reportes en DevZone con Bumble y el Audio DK:**
  - [121293](https://devzone.nordicsemi.com/f/nordic-q-a/121293/is-hci_uart-sample-support-le-audio-auracast/534385):
    "Periodic advertising not supported"; se resolvió compilando `hci_uart` con
    sysbuild (REPORTADO).
  - [121593](https://devzone.nordicsemi.com/f/nordic-q-a/121593/there-is-a-problem-sending-auracast):
    LE Create BIG devolvía 0x11; la solución fue `CONFIG_BT_CTLR_PHY_2M=y`
    (REPORTADO).
- **[bluekitchen/hci_uart_iso_timesync](https://github.com/bluekitchen/hci_uart_iso_timesync):**
  un `hci_uart` basado en el SDC para nRF5340 y nRF54L15, con un comando propio para
  leer el reloj ISO. Requiere NCS 3.2.1 o superior (REPORTADO). **Puede servir para
  la opción A con timestamps.**
- El hilo 124106 devuelve 404.

**Consecuencia: las opciones A y C se pueden combinar.** Bumble en el PC (Python,
entrada de audio flexible) más un nRF como controlador con `hci_uart` por CDC-ACM
(US$12–49). Así no se depende del chip interno del laptop. Nadie lo ha probado con
un JBL (INFERIDO).

## 6. Asistente de transmisión (BASS)

- El sample `bap_broadcast_assistant` de Zephyr pone `bis_sync` en todos los BIS
  del subgrupo (VERIFICADO). Para elegir un BIS por parlante basta cambiar una línea.
- **La shell BT de Zephyr ya permite elegir por receptor:**
  `bap_broadcast_assistant add_broadcast_id <id> <sync_pa> [<sync_bis>]`
  (VERIFICADO). Una segunda placa, o la misma, podría mandar a cada parlante su
  propio BIS_Sync.
- **No se pudo determinar** si el Go 4 y el Charge 6 exponen BASS como Scan
  Delegator.

## 7. Licencias y herramientas

### Licencias
Se contaron los encabezados SPDX de `nrf_audio` y `nrf_auraconfig`: 156 archivos
Nordic-5-Clause, 4 PCFT y 1 Apache (VERIFICADO).

| Licencia | Qué cubre | Qué permite |
|---|---|---|
| **LicenseRef-Nordic-5-Clause** | La mayor parte del código | Modificar y redistribuir, pero solo para chips Nordic |
| **LicenseRef-PCFT** (Packetcraft) | `audio_datapath.c/.h`, `audio_i2s.c/.h` | **"must not be … modified"** |
| **Códec LC3 de T2 Software** (`nrfxlib/lc3/license.txt`) | El códec de `nrf_audio` | Solo binario, "may not be modified…", solo chips Nordic |
| **Apache-2.0** | Samples de Zephyr, con liblc3 (`select LIBLC3`) | Libre |
| Binario | El SDC | Solo uso |

**Qué implica** (INFERIDO): **un fork público queda más limpio sobre los samples de
Zephyr.** Un fork de `nrf_audio` se puede publicar, pero sin tocar los archivos PCFT
y solo para chips Nordic.

### Herramientas
- Sistemas soportados para desarrollar: **Linux (Ubuntu 24.04)**, Windows 11 y
  macOS 26 (VERIFICADO).
- `west` y nRF Connect para VS Code; se flashea con `nrfutil` o J-Link. El Audio DK
  trae un script `buildprog.py`.
- Hay un problema conocido de audio USB en macOS, OCT-2154 (VERIFICADO).

## 8. Sincronización y temporización

- La documentación del SDC promete "microsecond-scale accuracy" (VERIFICADO).
- **`bt_le_audio_tx.c` envía los SDU de cada frame con un timestamp del
  controlador** (`bt_cap_stream_send_ts`) y se resincroniza periódicamente con
  `hci_vs_sdc_iso_read_tx_timestamp` (VERIFICADO).
  - Todos los BIS comparten el ancla del BIG, así que los canales salen alineados.
  - Lo que llega al oído depende de que cada JBL respete el presentation delay
    (INFERIDO).
- **Por el camino USB "no synchronization module is used"** (VERIFICADO). El drift
  entre el reloj USB y el de la radio se absorbe descartando bloques
  (`USB IN overrun`) (VERIFICADO). A 50 ppm serían cerca de un bloque de 1 ms cada
  ~20 s (INFERIDO), lo que puede sonar como un clic.
- La compensación de drift y de presentación de `audio_datapath.c` es solo del lado
  receptor (VERIFICADO).
- **No se encontraron mediciones de desfase entre parlantes.**

## 9. Proyectos existentes

- `sebhuet/nrf5340-audio-4-channels`: pese al nombre, es una copia sin cambios con
  1 commit (REPORTADO).
- AuraPlug: un dongle receptor con nRF5340, no un emisor (REPORTADO).
- [DevZone 127785](https://devzone.nordicsemi.com/f/nordic-q-a/127785/le-audio-interoperability-with-non-nordic-device-nrf5340-audio-dk-and-airoha-ab156x/564922):
  un nRF5340 configurado igual que un stream de JBL Go 4 no se reprodujo en un
  parlante con chip Airoha, posiblemente por dónde va la dirección AdvA en los
  paquetes de anuncio. Sin resolver (REPORTADO). **Es un aviso de que imitar los
  parámetros puede no bastar.**
- No encontré nada sobre una transmisión nRF reproducida en parlantes JBL, Sony o
  Bose.

## 10. Primer experimento por este camino

1. **Comprar** un nRF5340 Audio DK (~US$173) y una microSD. Un nRF5340 DK (US$49)
   es opcional, para hacer de asistente.
2. **Instalar NCS v3.4.1** en Linux con `nrfutil sdk-manager` o la extensión de VS
   Code:
   ```bash
   west init -m https://github.com/nrfconnect/sdk-nrf --mr v3.4.1 && west update
   ```
3. **Crear 4 archivos LC3** con contenido distinto (una voz que diga "izquierda",
   "derecha", etc., más un tren de clics para medir tiempos), usando `elc3` de
   liblc3 a 48 kHz, 80 kbps y 10 ms.
4. **Parchear `nrf_auraconfig`:** agregar los datos de fabricante `57 00 …dffd` en
   `ext_adv_populate()`.
5. **Compilar y flashear:**
   ```bash
   west build -b nrf5340_audio_dk/nrf5340/cpuapp samples/bluetooth/nrf_auraconfig --sysbuild
   west flash
   ```
6. **Configurar desde la shell:**
   ```
   nac preset 48_2_1 0
   nac num_bises 4 0 0
   nac location fl|fr|bl|br 0 0 <i>
   nac file select … 0 0 <i>
   nac pd 40000 0 0        (después probar 80000)
   nac rtn 2 0 0
   nac start
   ```
7. **Revisar qué BIS reproduce cada JBL** (la pregunta de seguir o no). Si hace
   falta, probar la selección de BIS desde un teléfono o desde la shell del
   asistente de Zephyr. Después, medir con micrófono el desfase entre parlantes.

**Esfuerzo estimado** (INFERIDO): de 2 a 4 días para los pasos 1–7, contando la
instalación de las herramientas. La entrada USB de 4 canales con feedback suma de 1
a 3 semanas.

## Lo que no se pudo determinar

- Si los JBL soportan BASS, y cómo elige un JBL su BIS cuando no hay asistente.
- La carga de CPU de LC3 con 4 canales a 48 kHz en el nRF5340.
- Cualquier cifra medida de latencia o sincronización.
- Si el sysbuild de NCS reemplaza la imagen `hci_ipc` del núcleo de red (controlador
  de Zephyr) por el SDC en el sample de Zephyr.

## Fuentes

- https://github.com/nrfconnect/sdk-nrf (`applications/nrf_audio/**`,
  `samples/bluetooth/nrf_auraconfig/**`,
  `doc/nrf/releases_and_maturity/{software_maturity,known_issues}.rst`,
  `doc/nrf/installation/recommended_versions.rst`)
- https://github.com/nrfconnect/sdk-nrfxlib
  (`softdevice_controller/doc/isochronous_channels.rst`, `CHANGELOG.rst`,
  `lc3/license.txt`)
- https://github.com/zephyrproject-rtos/zephyr
  (`samples/bluetooth/audio/bap_broadcast_source`, `bap_broadcast_assistant`,
  `samples/bluetooth/hci_{usb,uart,ipc}`,
  `subsys/usb/device_next/class/{bt_hci.c,usbd_uac2.c}`,
  `subsys/bluetooth/controller/Kconfig`,
  `subsys/bluetooth/audio/{bap_broadcast_source.c,shell/bap_broadcast_assistant.c}`,
  `include/zephyr/bluetooth/audio/bap_lc3_preset.h`)
- https://github.com/zephyrproject-rtos/zephyr/issues/44013
- DevZone: 121293, 121593, 127785 y
  [128676](https://devzone.nordicsemi.com/f/nordic-q-a/128676/creating-multiple-bigs-with-one-gateway-nrf5340-audio-dk/569232)
- https://github.com/bluekitchen/hci_uart_iso_timesync
- https://github.com/sebhuet/nrf5340-audio-4-channels
- https://www.hackster.io/alexlynd/auraplug-synchronized-le-audio-563657
- https://docs.zephyrproject.org/latest/boards/seeed/xiao_ble/doc/index.html (XIAO nRF52840: `xiao_ble`, UF2, SWD).
- https://docs.zephyrproject.org/latest/boards/others/promicro_nrf52840/doc/index.html (SuperMini: `promicro_nrf52840`, UF2, SWD).
- https://wiki.icbbuy.com/doku.php?id=developmentboard:nrf52840 (wiki del vendedor de la SuperMini).
- https://github.com/joric/nrfmicro/wiki/ALternatives (reportes sobre el cristal de 32,768 kHz de los clones).
- https://google.github.io/bumble/hardware/index.html,
  https://google.github.io/bumble/platforms/zephyr.html
- https://docs.zephyrproject.org/latest/boards/nordic/nrf5340_audio_dk/doc/index.html
- DigiKey: [nRF5340 Audio DK](https://www.digikey.com/en/products/detail/nordic-semiconductor-asa/NRF5340-AUDIO-DK/16399476),
  [nRF5340 DK](https://www.digikey.com/en/products/detail/nordic-semiconductor-asa/NRF5340-DK/13544603),
  [nRF52840 Dongle](https://www.digikey.com/en/products/detail/nordic-semiconductor-asa/NRF52840-DONGLE/9491124),
  [nRF54LM20 DK](https://www.digikey.com/en/products/detail/nordic-semiconductor-asa/NRF54LM20-DK/27685498),
  [Raytac MDBT53-DB-40](https://www.digikey.com/en/products/detail/raytac/MDBT53-DB-40/16630695)
