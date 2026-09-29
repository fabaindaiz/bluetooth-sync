# Bluetooth clásico (A2DP) con varios parlantes y sincronización por software

Investigación del 2026-09-25. Es una de las tres líneas de la fase de investigación
(ver [README](README.md)).

**Qué significa cada marca:**
- **VERIFICADO**: leído en una fuente primaria (documentación oficial o código fuente).
- **REPORTADO**: foro, blog, issue o FAQ de un fabricante.
- **INFERIDO**: deducción que nadie comprobó.

Nada de esto se ha medido todavía con los parlantes propios.

## Resumen

- A2DP es **punto a punto y no tiene un reloj compartido** entre parlantes. Cada
  parlante tiene su propio buffer y su propio reloj. La alineación depende del
  software y no está garantizada.
- Desde Linux se puede mandar **un canal distinto a cada parlante** con
  `libpipewire-module-combine-stream`, que asigna canales por salida. La latencia
  se compensa de forma estática, pero **el drift entre parlantes no se corrige**.
- La opción más robusta de este grupo es **una Raspberry Pi por parlante con
  Snapcast**. La sincronización por red queda bajo 1 ms. Lo que sigue fallando es
  el último salto Bluetooth, porque el buffer del parlante varía y nadie lo corrige.
- Bluetooth clásico no tiene un mecanismo estándar para sincronizar varios
  receptores al que se pueda llegar desde BlueZ o PipeWire. Los mecanismos que
  existen son propietarios de cada chipset (CSB, Qualcomm TWS, JBL PartyBoost).

## 1. Varios receptores A2DP en un solo equipo Linux

### Límites de conexión
- BlueZ y PipeWire no tienen una regla de "un solo parlante". PipeWire crea un
  sink `bluez_output.*` por dispositivo (INFERIDO a partir del diseño del plugin
  bluez5). Los límites prácticos los pone el controlador Bluetooth.
- **Sendspin BT Bridge**, un proyecto hecho para esto:
  - un adaptador admite hasta 7 enlaces ACL activos, pero A2DP consume mucho
    ancho de banda;
  - recomienda **1 a 3 parlantes por adaptador** y **2 adaptadores para 4 o 5
    parlantes**;
  - el adaptador integrado de la Raspberry Pi maneja **un solo stream A2DP
    concurrente**;
  - los dongles mejor probados son los **RTL8761B** (TP-Link UB500 y otros).

  REPORTADO: https://trudenboy.github.io/sendspin-bt-bridge/bluetooth-adapters/
- Un usuario de BlueALSA dice que los dongles chinos baratos soportan como máximo
  un stream A2DP. Él terminó usando un dongle por parlante. REPORTADO:
  https://github.com/arkq/bluez-alsa/issues/79
- El Wi-Fi en 2.4 GHz comparte banda y provoca cortes (sube el contador "Tx
  excessive retries"). REPORTADO: misma página de Sendspin.
- SBC en alta calidad usa unos 328–345 kbps por stream. VERIFICADO (Wikipedia):
  https://en.wikipedia.org/wiki/SBC_(codec)

### Combinar salidas y asignar canales
- `libpipewire-module-combine-stream` acepta un `audio.position` por stream y un
  `combine.audio.position`, así que a cada salida se le puede mandar los canales
  que uno elija. La documentación trae un ejemplo que arma un sink 5.1 con tres
  dispositivos estéreo (FL/FR, FC/LFE, SL/SR). Lo mismo sirve para mandar "solo
  FL al parlante A". VERIFICADO:
  https://docs.pipewire.org/page_module_combine_stream.html
- El proyecto **HyperBoom** divide L y R entre dos parlantes Bluetooth
  exactamente así, con PipeWire. REPORTADO:
  https://github.com/Zigazou/hyperboom-duo-stereo

### Compensación de latencia
- `combine.latency-compensate` agrega un buffer de retardo a cada stream, calculado
  a partir de la latencia que ese stream reporta. **Es una alineación estática y no
  corrige el drift.** VERIFICADO en el código fuente (`update_delay`,
  `get_stream_delay`):
  https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/src/modules/module-combine-stream.c
- La latencia que reporta cada nodo Bluetooth se suma así: retardo de paquete +
  retardo del códec + retardo de transporte + `latencyOffsetNsec` (ajuste manual).
  El retardo de transporte sale del **AVDTP delay report**. Si el parlante no
  reporta nada, PipeWire usa un **valor fijo de 125 ms** para SBC, AAC, aptX y
  LDAC. VERIFICADO:
  - https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/bluez5/media-sink.c
  - https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/plugins/bluez5/bluez5-dbus.c
    (`spa_bt_transport_get_delay_nsec`)
- El ajuste por dispositivo se hace con esa misma propiedad. Que se exponga en
  pavucontrol como "latency offset" y en `pactl set-port-latency-offset` es
  INFERIDO; la propiedad sí está VERIFICADA en el código.

### Drift de reloj
- El sink bluez5 de PipeWire solo ajusta la tasa al reloj de la radio en **ISO (LE
  Audio) y ASHA** (`media_iso_rate_match`). En A2DP solo empuja datos al ritmo del
  reloj del grafo. VERIFICADO: media-sink.c (enlace de arriba).
- A2DP no tiene reloj compartido. Cada parlante absorbe su desajuste con el emisor
  (del orden de 20 ppm) vigilando el nivel de su buffer y descartando o duplicando
  muestras. VERIFICADO con un ejemplo de sink en BTstack:
  https://bluekitchen-gmbh.com/a2dp-sink-and-source-on-stm32-f4-discovery-board/
- En consecuencia, el drift no crece sin límite, pero la latencia de cada parlante
  oscila dentro de su propia ventana de buffer, y cada modelo se comporta distinto.
  INFERIDO.

### Qué tan bien sincroniza en la práctica
- La FAQ de **SoundSeeder** dice que la mayoría de los parlantes Bluetooth agregan
  entre 20 y 70 ms de buffer, y que ese valor cambia en cada inicio de
  reproducción. Por eso no se puede corregir con un offset constante. REPORTADO:
  https://soundseeder.com/help/
- Usuarios de Arch notan desfase en sinks combinados y terminan ajustando offsets
  a mano. No dan cifras. REPORTADO: https://bbs.archlinux.org/viewtopic.php?id=291413
- **Chorus** (Windows con VoiceMeeter y calibración por micrófono) dice lograr una
  diferencia residual de unos 6 ms entre 2 parlantes Bluetooth con un adaptador
  Intel. No probó con 3 o más parlantes y no trata el drift. REPORTADO:
  https://github.com/AbhiCollegeWork/chorus-speaker-sync

## 2. Proyectos y herramientas que ya existen

| Proyecto | Qué hace | Relevancia | Estado |
|---|---|---|---|
| [Snapcast](https://github.com/badaix/snapcast) | Audio multiroom por red. Desviación típica bajo 0.2 ms. Corrige el drift quitando o duplicando muestras sueltas | Alta: base de la opción (c) | VERIFICADO |
| [Sendspin BT Bridge](https://trudenboy.github.io/sendspin-bt-bridge/bluetooth-adapters/) | De un equipo a varios parlantes A2DP | Alta: el mismo problema que el nuestro | REPORTADO |
| [HyperBoom duo stereo](https://github.com/Zigazou/hyperboom-duo-stereo) | L/R en dos parlantes BT con PipeWire | Alta: base de la opción (a) | REPORTADO |
| [BlueALSA](https://github.com/arkq/bluez-alsa) | Stack alternativo a PipeWire. Cada parlante es un PCM de ALSA | Media | REPORTADO |
| [pi-jukebox](https://github.com/nodomain/pi-jukebox) | Pi → snapclient → PipeWire → A2DP (un parlante) | Media: receta ya armada | REPORTADO |
| [SoundSeeder](https://soundseeder.com/help/) | App con modo L/R/mono por dispositivo | Baja: el propio fabricante desaconseja parlantes BT | REPORTADO |
| Samsung Dual Audio | Dos dispositivos BT a la vez desde un teléfono | Baja: se desincroniza. Se mitiga con AVRCP 1.6 y el control de retardo de Sound Assistant ([hilo](https://eu.community.samsung.com/t5/other-galaxy-s-series/dual-audio-not-in-sync/td-p/3350639)) | REPORTADO |
| VoiceMeeter (Windows) | Varias salidas BT con retardo por bus | Baja: la sincronización no está garantizada ([hilo](https://learn.microsoft.com/en-us/answers/questions/5657664/multiple-blue-tooth-speakers-to-play-in-sync-windo)) | REPORTADO |
| [CamillaDSP](https://github.com/HEnquist/camilladsp) | Retardo y filtros por canal | Media: afinar a mano | VERIFICADO |
| [google/audio-sync-kit](https://github.com/google/audio-sync-kit) | Mide la latencia entre dos señales (hecho para multiroom de Chromecast) | Media: calibración | REPORTADO |
| [delay-detector](https://github.com/DhakadG/delay-detector) | Detecta retardos | Baja | REPORTADO |
| [Chorus](https://github.com/AbhiCollegeWork/chorus-speaker-sync) | Calibración con micrófono de 2 parlantes BT | Media: idea que se puede copiar | REPORTADO |

### Snapcast en detalle
- snapclient tiene `--latency` ("Latency of the PCM device"). Además lee la
  latencia de la salida: en el backend pulse con `pa_stream_get_latency` y en el
  backend ALSA con `snd_pcm_avail_delay`. Así compensa automáticamente el retardo
  que reporte un sink Bluetooth. VERIFICADO:
  https://github.com/snapcast/snapcast/blob/develop/client/snapclient.cpp y
  `client/player/pulse_player.cpp`
- **No tiene selección nativa de canal L/R por cliente.** El pedido #747 se cerró
  sin una solución documentada. Hay que hacer el downmix en el cliente, por
  ejemplo con un remap sink de PipeWire. VERIFICADO/REPORTADO:
  https://github.com/snapcast/snapcast/issues/747
- Un usuario reporta que la salida BT "se desincroniza rápido". REPORTADO:
  https://github.com/snapcast/snapcast/issues/50

### Upmix, sonido envolvente simulado
- PipeWire trae:
  - `channelmix.upmix` con `upmix-method` en `simple` o `psd` (arma los canales
    traseros a partir del ambiente);
  - `channelmix.rear-delay`, 12 ms por defecto;
  - `channelmix.lfe-cutoff` y `channelmix.fc-cutoff`.

  VERIFICADO: https://docs.pipewire.org/page_man_pipewire-props_7.html
- **Corrección (2026-09-26):** estas propiedades **vienen apagadas** por defecto
  desde 0.3.68 (hay que activar `20-upmix.conf`), y en 1.6.9 `psd` genera
  traseros en contrafase con la misma señal L−R, no traseros estéreo. El detalle
  está en [07](07-software-de-audio-en-el-pc.md) §4.2.

### Calibración con micrófono
- **No encontré ninguna herramienta para Linux o PipeWire que calibre
  automáticamente varios sinks Bluetooth con un micrófono.** Existen piezas
  sueltas (Chorus, audio-sync-kit, delay-detector). Es un posible aporte original
  si se decide desarrollar.

## 3. Mecanismos de sincronización dentro de Bluetooth clásico

- **A2DP**: estrictamente punto a punto, sin sincronización entre receptores.
  REPORTADO/INFERIDO, respaldado por la ausencia de reloj compartido en el código
  de PipeWire.
- **AVDTP 1.3 Delay Reporting**: existe y PipeWire lo usa para calcular la latencia
  (VERIFICADO, bluez5-dbus.c). Es una cifra estática, no un reloj.
  - BlueZ 5.83 tuvo una regresión en los delay reports cuando BlueZ actúa como
    receptor. No afecta al caso de un PC que emite. REPORTADO:
    https://github.com/bluez/bluez/issues/1541
  - **Los Go 4 y el Charge 6 NO envían delay reports: MEDIDO** (E6,
    [experimentos/05](experimentos/05-e6-a2dp-un-canal-por-parlante.md)). Es lo que dejó
    a la calibración con micrófono como único mecanismo disponible, no como una mejora
    opcional.
- **Connectionless Slave Broadcast (CSB, Core 4.1, usado por el 3D Synchronization
  Profile)**: un emisor transmite a muchos receptores. Qualcomm/CSR construyó
  encima su "Broadcast Audio". La capa de audio es propietaria y no se puede usar
  desde BlueZ ni PipeWire. REPORTADO:
  - https://esp32.com/viewtopic.php?t=19491
  - https://www.freepatentsonline.com/y2016/0191181.html (patente de Qualcomm)
- **Qualcomm TWS / TWS+**: el audífono secundario escucha el enlace del primario,
  o en TWS+ el teléfono mantiene dos enlaces. Ambos son propietarios del chipset.
  REPORTADO:
  - https://audioxpress.com/article/getting-to-true-and-non-captive-wireless-stereo
  - https://www.qualcomm.com/products/features/truewireless
- **JBL PartyBoost**: enlace propietario entre parlantes. En el Go 4 y el Charge 6
  lo reemplazó la implementación de Auracast de JBL (modos fiesta y estéreo desde
  la app JBL Portable). REPORTADO:
  - https://www.soundguys.com/goodbye-jbl-partyboost-hello-auracast-134004/
  - https://www.whathifi.com/speakers/wireless-speakers/what-is-jbl-partyboost-is-it-the-same-as-connect-and-auracast

## 3.1 Calibrar el retardo sin un micrófono central (i-7c8794-4745b4)

**La pregunta, planteada por el usuario el 2026-09-29:** ¿hay forma de medir el desfase entre
parlantes **sin depender de un micrófono en un punto**, usando lo que permitan los códecs o el
propio stack?

**Por qué importa, y no es solo comodidad.** La calibración con micrófono tiene dos problemas
estructurales, los dos medidos:

1. **alinea en el punto del micrófono y desalinea el resto de la pieza**, porque mide el
   retardo total, que incluye el vuelo por el aire —34 cm son 1 ms—. Eso es lo contrario del
   objetivo del proyecto ([09](09-efecto-ambiental-y-diseno-de-la-experiencia.md) §4, y
   [experimentos/09](experimentos/09-primera-escucha-con-3-go-4.md) §7);
2. **el parlante que más aporta al envolvimiento es el que peor se mide**, porque su señal es
   la más decorrelacionada ([experimentos/09](experimentos/09-primera-escucha-con-3-go-4.md)
   §5).

Lo que se quiere medir es el desfase **electrónico**: el buffer de A2DP más el códec. Es igual
en toda la pieza, y es lo único que tiene sentido corregir para un oyente que se mueve.

### Las opciones, con lo que se sabe de cada una

| Camino | Estado | Qué haría falta |
|---|---|---|
| **AVDTP Delay Report** | **cerrado: MEDIDO.** Estos JBL no lo mandan | nada; no hay por dónde |
| **La contabilidad de latencia de PipeWire** | **sin probar, y es lo más barato** | leer `pw-dump` con los parlantes conectados |
| **Códecs con latencia declarada** (aptX Adaptive, LC3plus) | **inaplicable acá: INFERIDO** | otros parlantes; los Go 4 dan SBC y AAC |
| **LE Audio / Auracast** | **lo resuelve por construcción** | las SuperMini (E1 cerró el AX210) |
| **Micrófono no central, en el teléfono** | viable, pero no elimina el micrófono | la interfaz web de i-7c8794-bdb678 |

**El primero a probar es la contabilidad de PipeWire, porque no cuesta nada.** PipeWire calcula
la latencia de cada sink Bluetooth —para eso usa el delay report cuando existe, y una
estimación cuando no— y la expone en las propiedades del nodo. Si esa cifra difiere entre
parlantes y es estable, **da el desfase electrónico sin emitir un solo sonido**. Es lo que hay
que mirar primero:

```bash
pw-dump | grep -E "node.name|latency|delay"   # con los parlantes conectados
```

**Lo que hay que desconfiar, y conviene anotarlo antes de ilusionarse:** esa cifra puede ser el
valor **nominal** del buffer configurado y no el real del enlace, en cuyo caso sería idéntica
para los tres parlantes y no serviría para nada. **No se pudo comprobar el 2026-09-29** porque
los parlantes estaban apagados. Es el primer paso de i-7c8794-4745b4, y se resuelve en un
minuto.

**Y una forma de validar cualquiera de estos caminos sin confiar en él:** compararlo contra la
calibración con micrófono, que ya existe y ya mide. Si las dos coinciden, el camino sin
micrófono sirve; si no, el micrófono sigue siendo la referencia. Esa comparación es gratis
porque las dos piezas ya están construidas.

## 4. Diferencia con LE Audio / Auracast (en breve)

LE Audio usa canales isócronos (CIS/BIS) que comparten una referencia de tiempo de
presentación. Por eso el estándar contempla "múltiples streams de audio
independientes y sincronizados". VERIFICADO:
https://www.bluetooth.com/blog/10-frequently-asked-questions-on-le-isochronous-channels/

PipeWire ajusta activamente la tasa de los streams ISO al reloj de la radio
(VERIFICADO, media-sink.c). A2DP no ofrece nada de esto. El detalle está en
[02-le-audio-auracast-linux.md](02-le-audio-auracast-linux.md).

## 5. Viabilidad según esta línea

| Escenario | Veredicto | Detalle |
|---|---|---|
| (a) 2 parlantes como L/R desde Linux | **Viable, con límites** | combine-stream con FL al parlante A y FR al B, un dongle USB por parlante y offsets ajustados a mano. Se espera un desfase residual de ~5–20 ms que puede cambiar en cada inicio de reproducción o tras un corte (INFERIDO). Sirve para música en una fiesta; es justo para una imagen estéreo exigente y malo para sincronía labial con video si el reproductor no usa la latencia reportada. Dos Go 4 idénticos ayudan (INFERIDO) |
| (b) 4 parlantes como cuadrafonía o surround | **Técnicamente posible, frágil en la práctica** | 2 o más adaptadores, combine-stream FL/FR/RL/RR y upmix `psd`. Cuatro buffers independientes que varían decenas de ms, más una flota mixta (3 Go 4 + 1 Charge 6), borran la imagen espacial. Los traseros toleran más desalineación que un par L/R (INFERIDO). No hay corrección de drift |
| (c) Una Raspberry Pi por parlante con Snapcast | **La más robusta de esta línea** | Sincronización por red bajo 1 ms y cada enlace BT es uno a uno, sin competencia por la radio. La separación de canales se configura en cada cliente. El salto BT sigue siendo el punto débil: la Pi solo conoce el retardo reportado (o el fijo de 125 ms) y no corrige la variación del buffer del parlante. Hay que calibrar con `--latency` y volver a revisar tras cada reconexión |

## Lo que no se pudo determinar

- Cifras medidas de desfase (ms) o de drift (ppm) en parlantes JBL, o en sinks
  A2DP independientes durante horas.
- Si el Go 4 y el Charge 6 soportan AVDTP delay reports y AAC.
- Cuántos streams SBC simultáneos aguanta cada chipset (Intel, RTL8761B). Solo hay
  recomendaciones de proyectos y foros.
- Si el modo "Stereo" de Auracast de JBL acepta una transmisión Auracast de un PC o
  un teléfono, o solo el enlace entre parlantes JBL. Lo cubre la línea
  [01](01-parlantes-jbl.md).

## Experimentos que esto sugiere (sin ejecutar)

1. Conectar 2 Go 4 por A2DP a un Linux con 2 dongles RTL8761B, usar combine-stream
   FL/FR y **medir con micrófono** el desfase al inicio y después de 30 y 60 min.
2. Leer con `pw-dump` la latencia que reporta cada `bluez_output.*`, para ver si
   JBL envía delay report o si PipeWire cae en el valor fijo de 125 ms.
3. Repetir el paso 2 entre reinicios de reproducción, para ver cuánto varía el
   buffer (lo que advierte SoundSeeder).
