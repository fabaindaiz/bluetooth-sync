# Información externa que vale la pena conocer

Esto no es una lista de enlaces. Cada entrada dice qué aporta **a este proyecto**, y
si contradice algo ya decidido, también lo dice.

**Regla para mantenerla:** una fuente entra cuando cambió o confirmó una decisión,
no porque esté bien escrita.

El detalle completo de cada fuente, con su marca VERIFICADO, REPORTADO o INFERIDO,
está en [docs/research/](research/README.md). Las decisiones que produzcan estas
fuentes se registrarán en `docs/decisions.md` cuando se tome la decisión de seguir o
no.

## Parlantes JBL

- **[Documentación de Bumble Auracast](https://google.github.io/bumble/apps_and_tools/auracast.html)**
  Transmite Auracast desde Python con un controlador USB. Documenta los datos de
  fabricante de Harman (`87:…dffd`) que hacen que un Go 4 acepte la transmisión, y
  convierte una entrada estéreo en 2 BIS (FL/FR).

  **Qué confirma:** los JBL pueden ser receptores de un emisor propio.

  **Qué confirma (MEDIDO):** el Go 4 y el Charge 6 transmiten exactamente
  `87:…dffd` ([experimentos/01](research/experimentos/01-e2-anuncios-jbl-mac.md)).
  Un solo valor sirve para los dos.

- **[Discusión de Bumble #894](https://github.com/google/bumble/discussions/894)**
  El parlante JBL transmite o recibe según haya o no una conexión clásica activa.
  El Clip 5 necesitó un presentation delay de 80 ms en vez de 40 ms.

  **Qué confirma:** el Auracast de JBL es un relé de A2DP a BIS.

  **No aplicado todavía:** probar 40 ms y 80 ms con los propios parlantes.

- **[Reseña del Go 4 en What Hi-Fi](https://www.whathifi.com/reviews/jbl-go-4)**
  El estéreo Auracast de JBL tiene un desfase audible entre los dos parlantes.

  **Qué contradice:** que dentro de un BIG la alineación esté garantizada. La
  hipótesis es que el parlante transmisor reproduce por otro camino. Hay que
  medirlo con un emisor externo.

- **Fichas técnicas de JBL
  ([Go 4](https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dwec5ad861/pdfs/JBL%20Go%204_%20Specsheet_EN.pdf),
  [Charge 6](https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dw6197d6e0/pdfs/JBL_Charge_6_Specsheet_EN.pdf))**
  Solo declaran A2DP 1.4 y AVRCP 1.6. El estéreo es solo entre dos parlantes del
  mismo modelo. El Charge 6 tiene entrada de audio por USB-C.

  **No aplicado todavía:** el USB-C del Charge 6 como canal cableado.

- **Proyectos de ingeniería inversa de JBL
  ([openjbl](https://github.com/NiceDayZc/openjbl),
  [jbl-aura-play-together](https://github.com/SongJunguo/jbl-aura-play-together))**
  - openjbl lee el protocolo GATT/SPP de JBL Portable; el Charge 6 está confirmado
    en hardware.
  - jbl-aura-play-together documenta `SET_AURACAST_BROADCAST` y el servicio
    `DFFD`, que coincide con el sufijo de los datos de fabricante.

  **No aplicado todavía:** leer el firmware en cada medición (openjbl) y poner los
  parlantes en modo receptor por comando (E2).

- **[auracast-hackers-toolkit](https://github.com/auracast-research/auracast-hackers-toolkit)**
  Captura BIS con un nRF52840.

  **No aplicado todavía:** ver qué transmite un par estéreo JBL (E2).

## LE Audio y Auracast en Linux

- **[Collabora: Auracast con BlueZ en Genio 700 (2026-05)](https://www.collabora.com/news-and-blog/blog/2026/05/05/bluez-powered-auracast-broadcasting-on-genio-700/)**
  Configuración que funciona: MT7921, kernel 6.18.5, BlueZ 5.86, PipeWire 1.6.0 y
  WirePlumber 0.5.13, con un **JBL Go 4 como receptor**.

  **Qué confirma:** el camino A es posible con software libre.

  **No aplicado todavía:** usarla como receta de referencia para el hardware.

- **[Collabora: LE Audio y Auracast en Linux (2025-11)](https://www.collabora.com/news-and-blog/blog/2025/11/24/implementing-bluetooth-le-audio-and-auracast-on-linux-systems/)**
  Qué controladores soportan qué roles, y cómo revisarlo con `bluetoothctl` →
  `menu mgmt`.

  **No aplicado todavía:** es el primer paso de la prueba de factibilidad.

- **[Parámetros de PipeWire (pipewire-props)](https://docs.pipewire.org/page_man_pipewire-props_7.html)**
  Explica `bluez5.bcast_source.config`, que acepta varios BIS por BIG, cada uno
  con su `audio_channel_allocation` y con un nodo propio. Documenta también
  `channelmix.upmix`.

  **No aplicado todavía:** transmitir varios BIS desde PipeWire como alternativa a
  Bumble.

- **[SIG, "How to design Auracast earbuds"](https://www.bluetooth.com/wp-content/uploads/2024/05/2403_Auracast_Earbuds.pdf)
  y las [recomendaciones del SIG para transmisores](https://www.bluetooth.com/wp-content/uploads/2022/10/Auracast-Transmitter_Recommendations.pdf)**
  Un receptor elige su BIS por su Sink Audio Location o por un `BIS_Sync` que le
  escribe un asistente por BASS. El Presentation Delay es común para todo el BIG.
  Se recomienda incluir un BIS mono aparte.

  **Qué confirma:** la sincronización multicanal es parte del estándar.

- **[Presets LC3 de Zephyr](https://github.com/zephyrproject-rtos/zephyr/blob/main/include/zephyr/bluetooth/audio/bap_lc3_preset.h)**
  Tamaño y bitrate de cada preset. Caben unos 3 BIS de 48 kHz por intervalo
  (INFERIDO).

  **No aplicado todavía:** dimensionar 4 canales.

- **[nRF5340 Audio](https://nrfconnectdocs.nordicsemi.com/ncs/2.8.0/nrf/applications/nrf5340_audio/broadcast_source/README.html)
  y [nRF Auraconfig](https://nrfconnectdocs.nordicsemi.com/ncs/2.9.0-nRF54H20-1-rc2/nrf/samples/bluetooth/nrf_auraconfig/README.html)**
  Emisores de hardware dedicado. El primero recibe audio USB y emite L/R; el
  segundo permite hasta 2×4 BIS con ubicación propia.

  **No aplicado todavía:** son la alternativa si ningún controlador del PC tiene
  `iso-broadcaster`.

- **[apps/auracast.py de Bumble](https://github.com/google/bumble/blob/main/apps/auracast.py)**
  Junta todas las fuentes en un BIG. Los índices de BIS empiezan de nuevo en 1 en
  cada subgrupo, y las fuentes mono quedan fijas en FRONT_LEFT.

  **Qué confirma:** Bumble respeta d-7c8794-203de2 (un solo BIG).

  **No aplicado todavía:** el parche para 4 canales, si se elige Bumble.

## Bluetooth clásico y sincronización por software

- **[PipeWire combine-stream](https://docs.pipewire.org/page_module_combine_stream.html)
  y su [código fuente](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/src/modules/module-combine-stream.c)**
  Asigna canales por salida y compensa la latencia de forma estática. **No corrige
  el drift.**

  **Qué confirma:** el camino B es posible sin escribir código.

- **[Sink bluez5 de PipeWire](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/bluez5/media-sink.c)**
  Ajusta la tasa al reloj de la radio solo en ISO y ASHA; en A2DP no. Si el
  parlante no envía delay report, asume 125 ms fijos.

  **Qué confirma:** la sincronización por A2DP es de mejor esfuerzo.

- **[Snapcast](https://github.com/badaix/snapcast)**
  Multiroom por red con menos de 0.2 ms de desviación. No trae selección de canal
  por cliente ([#747](https://github.com/snapcast/snapcast/issues/747)).

  **No aplicado todavía:** es la base del camino C.

- **[Sendspin BT Bridge: adaptadores](https://trudenboy.github.io/sendspin-bt-bridge/bluetooth-adapters/)**
  De 1 a 3 parlantes A2DP por adaptador. Recomienda el RTL8761B. El BT de la
  Raspberry Pi maneja un solo stream.

  **No aplicado todavía:** para elegir dongles si se va por el camino B.

- **[FAQ de SoundSeeder](https://soundseeder.com/help/)**
  Los parlantes BT agregan entre 20 y 70 ms de buffer, y el valor cambia en cada
  inicio de reproducción.

  **Qué contradice:** que un offset fijo baste en el camino B.

- **[HyperBoom duo stereo](https://github.com/Zigazou/hyperboom-duo-stereo)**
  Ejemplo funcional de L/R en dos parlantes BT con PipeWire.

## Software de audio en el PC e integración

- **[channelmix de PipeWire 1.6.9](https://github.com/PipeWire/pipewire/blob/1.6.9/spa/plugins/audioconvert/channelmix-ops-c.c)**
  El modo `psd` manda a los dos traseros la misma señal L−R en contrafase, y con
  la configuración por defecto (`upmix-method=none`) no genera ningún canal.

  **Qué contradice:** que el upmix de la Fase 2 sea "activar `psd`"
  (i-7c8794-c7ccb9). Hay que comparar métodos
  ([07](research/07-software-de-audio-en-el-pc.md) §4.2).

- **[Gadget USB `f_uac2` de Linux](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_uac2.c)**
  Tarjeta de sonido USB con feedback asíncrono (`Capture Pitch 1000000`): el PC
  entrega al ritmo que le pide el dispositivo.

  **Qué confirma:** que un emisor dedicado en una Pi puede evitar el remuestreo,
  y no requiere instalar nada en el PC (Fase 3, i-7c8794-80f3ac;
  [08](research/08-integracion-y-plan.md) §3).

- **[`IsoPacketStream` en `bumble/device.py`](https://github.com/google/bumble/blob/main/bumble/device.py)**
  Da contrapresión por paquetes completados. `apps/auracast.py` la usa con una
  cola de 64 SDU (hasta 640 ms) y sin control de drift.

  **Qué confirma:** que el MVP tiene que traer su propio lazo de reloj (P2,
  i-7c8794-cb208f; [08](research/08-integracion-y-plan.md) §4).

- **[Core Audio taps (`CATapDescription`)](https://developer.apple.com/documentation/coreaudio/catapdescription)**
  Captura del audio del sistema en macOS 14.2+ sin drivers. PyObjC 12.2.2 lo
  expone.

  **Qué confirma:** que en el Mac no hace falta BlackHole (P1, i-7c8794-fd5f03).

## Qué leer primero

| Si vas a tocar… | Lee | Y cuidado con |
|---|---|---|
| La transmisión Auracast hacia los JBL | [02](research/02-le-audio-auracast-linux.md) §1 y §3, [01](research/01-parlantes-jbl.md) §3 | Sin los datos de fabricante de Harman, el JBL ignora la transmisión |
| La asignación de canales | [02](research/02-le-audio-auracast-linux.md) §4 | Dos BIGs separados no quedan sincronizados entre sí |
| El hardware que comprar | [02](research/02-le-audio-auracast-linux.md) §2 | Ningún dongle USB está confirmado; el MT7921 es el único con prueba contra un Go 4 |
| A2DP con varios parlantes | [03](research/03-bluetooth-clasico-y-sync-por-software.md) §1 | combine-stream no corrige el drift |
| La captura del audio o el upmix | [07](research/07-software-de-audio-en-el-pc.md) §2 y §4 | `4.0` no es cuadrafonía; `psd` no da traseros estéreo |
| La herramienta del MVP | [08](research/08-integracion-y-plan.md) §2 y §6 | `wpctl set-default` deja historial; el lazo de reloj no existe en Bumble |
