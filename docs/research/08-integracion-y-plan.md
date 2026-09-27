# Integración: herramientas, sistema operativo, stack y plan de I+D

Análisis del 2026-09-26. Toma lo investigado en [02](02-le-audio-auracast-linux.md)
a [07](07-software-de-audio-en-el-pc.md) y responde **cómo se juntaría todo (audio
y Bluetooth) en una o varias herramientas**, con esta condición del usuario:
**modificar lo menos posible el sistema**. Compara alternativas de sistema
operativo, stack e integraciones, y termina en un plan de investigación y
desarrollo.

**Qué significa cada marca:**
- **VERIFICADO**: fuente primaria (código fuente, documentación oficial,
  especificación).
- **REPORTADO**: foro, blog, issue, README de terceros o página de un fabricante.
- **INFERIDO**: deducción que nadie comprobó. **Todo el diseño de este documento
  es INFERIDO**, salvo las piezas marcadas.

**Este documento no es código ni autoriza a escribirlo.** El código de producto
sigue bloqueado hasta la decisión de seguir o no (d-7c8794-346170,
i-7c8794-0d129c). Lo que el plan propone programar antes de esa decisión son
probes en `probes/` (d-7c8794-3208b7).

Dos agentes de investigación reunieron las fuentes nuevas, sobre Bumble `745d607`
(v0.0.235), PipeWire 1.6.9 y master, WirePlumber 0.5.17, sdk-nrf `5d1f559`, Linux
y Zephyr en main, y PyObjC 12.2.2 ejecutado en macOS 27. Esta sesión volvió a
revisar en el código una sola afirmación, la del upmix por defecto de PipeWire
([07](07-software-de-audio-en-el-pc.md) §4.2).

## Resumen

- **Recomendación: una sola herramienta CLI en Python sobre Bumble.** Tiene un
  **núcleo común** (DSP, reloj, LC3, BIG) y **backends intercambiables**: uno de
  captura por sistema operativo y uno de emisor. Nombre provisional: `jblsync`.
- **Configuración que menos toca el sistema, y que es la misma que ya se compró:**
  Bumble con una **SuperMini nRF52840 por `serial:`** (CDC-ACM).
  - BlueZ ni se entera, no hay que activar el modo experimental, no hay root y
    no hay drivers.
  - En macOS y Windows el driver serial viene con el sistema.
  - En Linux basta con que el usuario esté en el grupo `dialout` (`uucp` en
    Arch). Es el único cambio que queda, y se hace una sola vez. VERIFICADO/REPORTADO (§2).
- **Captura sin instalar drivers, que desaparece al terminar el proceso:**
  - **Linux:** un sink virtual que crea el propio proceso (`pw_stream` con
    `media.class=Audio/Sink`, o `pw-record -P` por subprocess). Muere con el
    proceso.
  - **macOS 14.2+:** un **Core Audio process tap** privado, que pide un permiso
    de privacidad pero no instala nada.
  - **Windows:** WASAPI loopback.
  - **No hacen falta BlackHole, VB-CABLE ni archivos en `/etc`.** VERIFICADO/REPORTADO (§2).
- **El único cambio con residuo en Linux es la salida por defecto.** `wpctl
  set-default` deja un historial. Hay una vía sin persistencia, INFERIDA pero con
  cada paso verificado (§2.1).
- **El reloj es el problema de fondo.** El audio del PC va al reloj del PC y el
  BIG va al del controlador, y hay que remuestrear de forma adaptativa.
  - Bumble no lo hace, y su app usa una cola de 64 SDU (hasta 640 ms).
  - Existen las piezas: `samplerate` con razón variable, la contrapresión de
    `IsoPacketStream`, y `HCI_LE_Read_ISO_TX_Sync`. VERIFICADO (§4).
- **Evolución natural: la misma herramienta corriendo en una Raspberry Pi que el
  PC ve como tarjeta de sonido USB de 4 canales** (gadget UAC2 de Linux).
  - En el PC no se instala nada, y funciona igual en Windows, macOS y Linux.
  - El gadget asíncrono hace que **el PC entregue el audio al ritmo del BIG**,
    así que no hay que remuestrear. VERIFICADO el mecanismo, INFERIDA su
    aplicación (§3).
  - Pasar del PC a la Pi es **cambiar el backend de captura**, no reescribir.
- **La Pico 2 W que ya tiene el usuario no transmite Auracast** (§3.1). Su radio
  CYW43439 ni siquiera hace advertising extendido. Pero **el RP2350 sí puede ser
  el cerebro de la Fase 3**, sin comprar nada:
  - tarjeta USB de 4 canales con TinyUSB;
  - LC3;
  - host BTstack, con una SuperMini como controlador por UART.
  Lo que decide es si caben 4 codificadores LC3 en la CPU, y eso se mide con un
  benchmark de unas horas (P3).
- **Descartados:**
  - los transmisores Auracast comerciales, porque los JBL no reciben de ellos
    (REPORTADO por Avantree; coincide con los datos de fabricante de Harman,
    [01](01-parlantes-jbl.md));
  - BlueZ con PipeWire nativo como vía de *menor huella*, porque exige root,
    `Experimental` en `main.conf` y el UUID de ISO en el kernel. Sigue siendo
    válido como alternativa técnica.
- **El plan:** 3 probes de software que se pueden hacer ya, sin la respuesta de E4
  (P1–P3); el MVP en 6 hitos si la decisión es seguir (M0–M5); y una Fase 3 con el
  emisor dedicado (§7).
- **Decisiones que le tocan al usuario** (§8):
  - el sistema operativo de referencia (se recomienda Linux, con el Mac como
    estación de trabajo);
  - para la Fase 3, si seguir con la Pico 2 W (C bare-metal, sin compra) o
    comprar una Pi Zero 2 W (~US$15, que reutiliza el código Python);
  - el nombre de la herramienta.

## 1. Qué tiene que hacer el sistema

| # | Componente | Qué hace | Dónde está investigado |
|---|---|---|---|
| C1 | Captura | Recibe el audio de las apps: todo el sistema, una app, o un archivo | [07](07-software-de-audio-en-el-pc.md) §2 |
| C2 | DSP | Upmix a quad o LCRS, retardo y ganancia por canal, paso-bajo de traseros | [07](07-software-de-audio-en-el-pc.md) §4 |
| C3 | Reloj | Iguala el ritmo de la fuente con el del BIG, sin cortes | §4 |
| C4 | Códec | 4 codificadores LC3 mono, con tramas de 10 ms | [05](05-opcion-a-bumble.md) |
| C5 | Emisor | Anuncio con los datos de fabricante de Harman, BASE con una ubicación por BIS, un BIG con N BIS | [05](05-opcion-a-bumble.md), d-7c8794-203de2 |
| C6 | Parlantes | Descubrirlos, ponerlos en modo receptor y asignarles un BIS (BASS `BIS_Sync`, si hace falta) | [01](01-parlantes-jbl.md), [04](04-implementaciones-y-stacks.md) §6 |
| C7 | Calibración | Un pulso por canal, grabado con el micrófono, para calcular el desfase y el nivel de cada parlante | [03](03-bluetooth-clasico-y-sync-por-software.md) §2 |
| C8 | Perfil | Qué parlante (dirección) va a qué canal, con qué retardo y qué ganancia | — |
| C9 | Supervisión | Dejar el sistema como estaba aunque el proceso se caiga | §2, tarjeta *cleanup-belongs-to-the-supervisor* |

## 2. Cuánto toca el sistema cada mecanismo

**Niveles de huella** (definidos aquí para comparar):

| Nivel | Qué significa |
|---|---|
| **N0** | Nada: un dispositivo USB estándar (class-compliant) o un protocolo que el sistema ya trae |
| **N1** | Solo mientras corre el proceso. Nada queda al terminar, ni siquiera tras una caída |
| **N2** | Un permiso que se da una vez, persistente pero trivial y reversible: un grupo, o un permiso de privacidad a la terminal |
| **N3** | Archivos de configuración en el home, que se revierten borrando un archivo |
| **N4** | Cambios de sistema con root o admin: `/etc`, drivers, NVRAM, módulos del kernel |

### 2.1 Linux

| Mecanismo | Nivel | Detalle | Marca |
|---|---|---|---|
| **Sink propio en tiempo de ejecución**: `pw_stream` con `media.class=Audio/Sink` (C o Rust) | **N1** | `pw_stream` acepta ese `media.class`, y `module-loopback` lo usa así. El nodo vive en el proceso y muere con él. Hay que poner `state.restore-props=false` y `state.restore-target=false`: si no, WirePlumber guarda su volumen en `~/.local/state/wireplumber/stream-properties` | VERIFICADO (`src/pipewire/stream.c`, `module-loopback.c`, `state-stream.lua` de WirePlumber) |
| **`pw-record -P '{ media.class=Audio/Sink … }' --raw -`** por subprocess (desde Python) | **N1** | `-P` fusiona las propiedades del stream y `--raw -` escribe PCM a stdout. Da un sink virtual que vuelca el audio y desaparece al matar el proceso. Nadie lo documenta y falta probarlo | INFERIDO del código (`src/tools/pw-cat.c`) |
| `pw-loopback` | N1 | Vive mientras vive el proceso, pero reenruta hacia otro sink y no entrega el audio a un proceso | VERIFICADO (`src/tools/pw-loopback.c`) |
| `pactl load-module module-null-sink` | N1 débil | Lo crea pipewire-pulse y no depende del cliente. **Si la herramienta se cae, el sink queda huérfano** hasta un `unload-module` o un reinicio | VERIFICADO (`pulse-module-null-sink.c`) |
| Drop-in en `~/.config/pipewire/pipewire.conf.d/` | N3 | Es la receta de [07](07-software-de-audio-en-el-pc.md) §2.1 | VERIFICADO |
| **Salida por defecto con `wpctl set-default` o `pactl set-default-sink`** | **N3 con residuo** | Escriben `default.configured.audio.sink`, y WirePlumber lo guarda como un **historial** en `~/.local/state/wireplumber/default-nodes`. Aunque se restaure al salir, el nodo queda en el historial | VERIFICADO (`state-default-nodes.lua`) |
| `priority.session` alto en el nodo propio | — | **No sirve casi nunca.** El nodo elegido por el usuario recibe 30000 + prioridad, y los del historial 20001 − i. Solo gana si el usuario nunca eligió una salida | VERIFICADO (`find-selected-default-node.lua`, `find-best-default-node.lua`) |
| **Salida por defecto sin persistir** | **N1** | Cuatro pasos: `wpctl settings node.restore-default-targets false` sin `--save` (solo en tiempo de ejecución); poner `default.configured.audio.sink`; restaurarlo al salir; y volver el setting a true. Si la herramienta se cae, el setting queda en false hasta que se reinicie WirePlumber | INFERIDO; cada paso VERIFICADO en `wpctl.c` |
| No tocar la salida por defecto y capturar el monitor del sink actual | N1 | Sin ningún cambio, pero **los parlantes del PC siguen sonando** | VERIFICADO (`stream.capture.sink`) |
| **Bumble `serial:/dev/ttyACM0`** (Zephyr `hci_uart` por CDC-ACM) | **N2** | Hace falta estar en el grupo `dialout` (en Arch, `uucp`) o una regla udev con `uaccess`. BlueZ no ve el controlador: solo carga `cdc_acm`. ModemManager 1.14+ no debería sondearlo, porque el CDC de Zephyr declara `bInterfaceProtocol=0` | REPORTADO (grupos); VERIFICADO (`usbd_cdc_acm.c`); INFERIDO (BlueZ y ModemManager) |
| Bumble `usb:` (Zephyr `hci_usb`) | N2–N4 | libusb desengancha btusb y lo vuelve a enganchar al cerrar, pero escribir en `/dev/bus/usb` requiere `sudo chmod` o una regla udev | VERIFICADO (`bumble/transport/usb.py`, [documentación de Bumble](https://google.github.io/bumble/platforms/linux.html)) |
| Bumble `hci-socket` (el chip interno) | N4 | CAP_NET_ADMIN, el adaptador en down y bluetoothd detenido | VERIFICADO (documentación de Bumble) |
| BlueZ + PipeWire `bap_bcast_source` | **N4** | `Experimental=true` en `/etc/bluetooth/main.conf`, UUID de ISO en `KernelExperimental`, kernel ≥6.4 | VERIFICADO ([02](02-le-audio-auracast-linux.md)) |

### 2.2 macOS

| Mecanismo | Nivel | Detalle | Marca |
|---|---|---|---|
| **Core Audio process tap** privado (14.2+) | **N1 + N2** | PyObjC 12.2.2 expone `AudioHardwareCreateProcessTap`, `CATapDescription` y el aggregate device; se comprobó ejecutándolo en macOS 27. `isPrivate` hace el tap visible solo para el proceso. `muteBehavior = CATapMuted` silencia los parlantes del Mac mientras se captura. El permiso ("Grabación de audio del sistema") queda concedido a la **app de terminal** | VERIFICADO (PyObjC, [CATapDescription](https://developer.apple.com/documentation/coreaudio/catapdescription)); REPORTADO (TCC, mute) |
| audiotee (CLI en Swift, MIT) | N1 + N2 | Vuelca los taps a stdout, en mono o estéreo (`--stereo`, `--mute`, `--include-processes`). **Sin permiso entrega silencio en vez de un error** | REPORTADO (https://github.com/makeusabrew/audiotee, issue #7) |
| BlackHole | N4 | Driver HAL en `/Library/Audio/Plug-Ins/HAL`, con admin | VERIFICADO ([07](07-software-de-audio-en-el-pc.md) §2.2) |
| **Bumble `serial:/dev/cu.usbmodem*`** | **N0** | Usa el driver CDC-ACM que trae el sistema, y los ejemplos de Bumble usan esa ruta | VERIFICADO (documentación de Bumble) / REPORTADO (driver) |
| Bumble `usb:` | N4 | `sudo nvram bluetoothHostControllerSwitchBehavior="never"`, que es persistente | VERIFICADO (documentación de Bumble) |

**Límite de los taps:** un tap global es mono o estéreo (`isMixdown`). Para
recibir 4 canales hay que pedir el tap de un dispositivo concreto sin mixdown, y
eso está sin probar ([07](07-software-de-audio-en-el-pc.md) §2.2). **En la
práctica no importa (INFERIDO):** casi todas las fuentes son estéreo, y el upmix se
hace en la herramienta. Los juegos y las fuentes 5.1 son los que quedan sin
multicanal real en Mac.

### 2.3 Windows

| Mecanismo | Nivel | Detalle | Marca |
|---|---|---|---|
| WASAPI loopback (PyAudioWPatch 0.2.12.8 o `soundcard`) | N1 | Funciona solo en modo shared y sin admin. Si el endpoint está en 5.1 o 7.1, entrega 6 u 8 canales | VERIFICADO (Microsoft) / REPORTADO (bibliotecas) |
| Process loopback (build 20348+) | N1 | Captura por proceso, pero no hay binding de Python mantenido | VERIFICADO / INFERIDO |
| VB-CABLE, Voicemeeter | N4 | Instalan un driver | VERIFICADO |
| **Bumble `serial:COMx`** | **N0** | `usbser.sys` se carga solo con CDC-ACM desde Windows 10. pyserial-asyncio funciona por polling en Windows, lo que puede agregar jitter | VERIFICADO (Microsoft, documentación de pyserial-asyncio) |
| Bumble `usb:` + WinUSB (Zadig) | N4 | Instala un driver | VERIFICADO |

**Windows como anfitrión de Bumble no contradice d-7c8794-1b2706** (INFERIDO).
Esa decisión descarta el stack LE Audio *propio* de Windows, que no pone los datos
de Harman. Bumble con un controlador externo no usa ese stack.

### 2.4 Qué se hereda de la base de conocimiento

Tarjetas de `.agents/knowledge/` aplicadas a este diseño:

- **cleanup-belongs-to-the-supervisor.** El estado que la herramienta ensucia (la
  salida por defecto, el setting de WirePlumber) no se restaura desde el propio
  proceso, porque una caída se salta la limpieza.
  - Se prefieren mecanismos N1 donde **el supervisor es el sistema**: el nodo
    muere con el cliente, el tap privado muere con el proceso.
  - Lo que no sea N1 lo restaura un proceso padre con timeout.
  - **Chequeo:** matar la herramienta con `kill -9` a mitad de la reproducción y
    comparar `~/.local/state/wireplumber/` y la salida por defecto, byte a byte,
    con cómo estaban antes.
- **detect-by-observation-not-build-flag.** Si el controlador soporta ISO se sabe
  preguntándole: comandos y features locales, como hace `bumble-controller-info`.
  No se deduce del modelo de la placa ni del firmware que dice tener.
  - Una capacidad que no se pudo observar **solo puede apagar** una función,
    nunca encenderla.
  - **Chequeo:** `jblsync doctor` muestra la señal observada.
- **derive-state-from-one-clock.** El número de SDU y el instante de
  presentación se **derivan del reloj del BIG**, no de sumar deltas en el host. El
  reloj del PC es una medición que hay que acotar, no la referencia.
- **close-the-loop-in-the-actuators-frame.** El lazo de drift mide el error en el
  nivel de la cola hacia el controlador, que es lo que el remuestreador mueve, y
  tiene histéresis, porque si no el lazo oscila en el umbral.
  - **Chequeo:** con una entrada estable, contar los cambios de modo del lazo.
    Tienen que ser cero.
- **fail-closed-defaults.** Un parlante que no está en el perfil no recibe ningún
  BIS por defecto, y un perfil que falta hace que `play` se niegue a arrancar en
  lugar de mandar todo a FL.

## 3. Dónde vive el emisor

| | H1 · PC con Bumble + nRF52840 | H2 · Raspberry Pi como tarjeta USB (gadget UAC2) + Bumble + nRF52840 | H3 · nRF5340 Audio DK como tarjeta USB | H4 · Transmisor comercial | H5 · Pi con entrada por red |
|---|---|---|---|---|---|
| Software en el PC | La herramienta | **Ninguno**: el PC ve una tarjeta de 4 canales | **Ninguno** | Ninguno | Mac: ninguno (AirPlay 2). Linux: PipeWire RTP. Windows: de terceros |
| Huella en el PC | N1 + N2 | **N0** | **N0** | N0 | N0 a N3 |
| Canales | Los que se quieran | 4 (`c_chmask=0x33`, FL FR BL BR) | Hoy 2; 4 con cambios de firmware | 1–2 | AirPlay 2 da 5.1/7.1 solo desde apps de Apple; RTP y Snapcast, N |
| Reloj | Remuestreo adaptativo en la herramienta | **Feedback asíncrono: el PC entrega al ritmo que pide la Pi, y la Pi lo ajusta al BIG** | Hoy se sincroniza con el SOF del USB y **descarta o rellena tramas de 10 ms**; con `feedback_cb`, igual que H2 | Interno | Dos etapas: la red a la Pi y la Pi al controlador |
| Latencia (INFERIDO) | ~100–150 ms | ~100–200 ms | ~80–150 ms | ~30–100 ms | AirPlay ~2 s; RTP ≥100 ms más el tramo BT |
| Lip-sync | Declarable en PipeWire (por probar) | **No**: el PC cree que es una tarjeta USB de latencia baja | No | No | Solo AirPlay: el Mac retrasa el video en Safari y la app TV |
| Hardware extra | Ya comprado (SuperMini) | Pi Zero 2 W ~US$15, o Pi 4/5 | US$172.57 | US$40–100 | Pi |
| Esfuerzo | Medio | Medio-alto | Alto (firmware en C, licencia Nordic) | — | Medio |
| Riesgo principal | Drift y jitter en Python | Windows con 4 canales sin probar; alimentación de la Pi 4/5 | Firmware propio con licencia Nordic-5-Clause | **Los JBL no lo aceptan** | Latencia; Windows sin entrada nativa |
| Veredicto | **MVP** | **Fase 3** | Referencia de medición (ya en [06](06-opcion-c-nrf5340.md)) | **Descartado** | Complemento de H2 (AirPlay, Spotify Connect) |

**Datos de H2** (VERIFICADO salvo donde se indica):
- **Qué placas sirven como dispositivo USB:**
  - la Zero 2 W, por el puerto micro-USB rotulado `USB`;
  - la Pi 4 y la Pi 5, por el USB-C de alimentación, con `dtoverlay=dwc2,dr_mode=peripheral`;
  - la Pi 3 y anteriores **no sirven**.
  - Fuente: [whitepaper "Using OTG mode"](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009276-WP/Using-OTG-mode-on-Raspberry-Pi-SBCs).
- **`f_uac2` recibe lo que el PC reproduce como *captura* del gadget.**
  - `c_chmask` admite hasta 27 posiciones.
  - `c_sync=async` es el valor por defecto, con un endpoint de feedback explícito.
  - El control ALSA **`Capture Pitch 1000000`** le dice al PC cuántas muestras
    mandar. CamillaDSP usa ese mismo control para su "rate adjust".
  - Fuentes: [`f_uac2.c`](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_uac2.c) y el [README de CamillaDSP](https://github.com/HEnquist/camilladsp).
- **Qué sistemas lo reconocen:**
  - Windows 10 1703+ trae `usbaudio2.sys`: acepta async con feedback explícito
    y hasta 8 canales en modo compartido ([Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/usb-2-0-audio-drivers)).
  - **Nadie reporta un gadget de 4 canales probado en Windows 11** (REPORTADO:
    hay 8 y 96 canales en macOS y Linux, [RASPIAUDIO](https://github.com/RASPIAUDIO/CamillaDSP/blob/main/docs/usb_audio_gadget_pi5.md)).
- **En la Zero 2 W** (INFERIDO):
  - el único USB de datos queda para el gadget, así que la SuperMini tiene que ir
    por el **UART del GPIO** con `hci_uart`;
  - 4 BIS son ~40 kB/s, y un UART a 1 Mbaud da ~100 kB/s;
  - el Bluetooth integrado de la Pi no tiene ISO.
- **Datos de H3** (VERIFICADO, [06](06-opcion-c-nrf5340.md) y sdk-nrf
  `applications/nrf_audio`): `audio_usb.h` tiene `#error USB only supports 48kHz
  stereo`, y el reloj va `sof-synchronized`.
  - Zephyr `usbd_uac2` sí admite 4 canales y feedback explícito (sample
    `uac2_explicit_feedback`).

### 3.1 La Raspberry Pi Pico 2 W (RP2350 + CYW43439)

El usuario ya tiene una (2026-09-26). No corre Linux, así que no hay `f_uac2`,
pero cubre el mismo papel por otro camino.

**La radio no sirve para Auracast:**
- La hoja de datos del CYW43439 se contradice: el título dice Bluetooth 5.2, la
  lista de funciones dice "Qualified for … 5.0". No menciona isócronos, advertising
  extendido ni periódico, ni LE Audio. VERIFICADO:
  [hoja de datos Infineon 002-30348](https://storage.googleapis.com/media.amperka.com/products/raspberry-pi-pico-2-w-with-headers/media/infineon-cyw43439-datasheet.pdf).
- En la Pico 2 W, `LE_Set_Extended_Advertising_Enable` responde "Unknown HCI
  Command" (hay una traza HCI).
  - Matthias Ringwald (BTstack): *"Periodic Advertising is supported by BTstack -
    but not the CYW43439"*.
  - Raspberry Pi: *"It's hard to get any changes made to the firmware"*.
  - REPORTADO: https://github.com/raspberrypi/pico-sdk/issues/2313
  - El firmware BT que se distribuye no cambia en su contenido desde el
    2023-09-28. VERIFICADO:
    [`cyw43_btfw_43439.h`](https://github.com/georgerobotics/cyw43-driver/blob/main/firmware/cyw43_btfw_43439.h).
- Sin advertising periódico no hay BIG, y por lo mismo tampoco sirve como sniffer
  de Auracast: solo vería anuncios legacy. INFERIDO, con confianza alta.

**El RP2350 sí sirve de cerebro para la Fase 3**, en la variante **H2b**: la Pico
2 W como tarjeta USB + LC3 + host BTstack, con una SuperMini como controlador
por UART H4.

- **USB:**
  - El RP2350 es USB 1.1 full-speed. 4 canales × 16 bit × 48 kHz = 384 B por
    trama de 1 ms, y el tope isócrono es 1023 B, así que cabe (INFERIDO,
    aritmética).
  - TinyUSB trae `uac2_speaker_fb` (estéreo con feedback asíncrono). 4 canales
    saldrían cambiando `CFG_TUD_AUDIO_FUNC_1_N_CHANNELS_RX` y el descriptor. Los
    ejemplos están VERIFICADOS; que 4 canales funcionen en el RP2xxx es INFERIDO,
    porque nadie lo reporta.
  - **En full-speed, TinyUSB ahora recomienda UAC1** para OUT asíncrono, porque
    macOS y Windows esperan formatos de feedback distintos. Está probado en
    Windows 10, macOS 13 y Arch, pero en STM32 y en estéreo. REPORTADO:
    https://github.com/hathach/tinyusb/pull/3270
  - Los isócronos del RP2xxx usan un solo buffer en DPRAM, así que conviene
    dejar la USB sola en un núcleo. VERIFICADO: `rp2040_usb.c`, `force_single =
    is_iso`.
  - El Pico SDK 2.3.1 trae TinyUSB 0.18.0, anterior a ese cambio. Hay que
    actualizar el submódulo. VERIFICADO.
- **Host BTstack con un controlador externo: ya existe un port oficial casi igual.**
  - `port/rp2040-vela-if820` es un RP2040 con un controlador externo por UART H4
    (DMA, RTS/CTS, 3 Mbaud) y una tarjeta de sonido TinyUSB UAC2. VERIFICADO:
    https://github.com/bluekitchen/btstack/tree/master/port/rp2040-vela-if820
  - Para ISO hay que activar `ENABLE_LE_ISOCHRONOUS_STREAMS`, como en el port
    `stm32-f4discovery-cyw55310`.
- **El emisor en BTstack hay que armarlo:**
  - El repo público solo trae `le_audio_broadcast_source_lite.c` (`MAX_NUM_BIS 4`,
    LC3 de Google). **No es Auracast estándar**: publica un campo de fabricante
    de BlueKitchen y no tiene BASE, así que un JBL no lo reconocería.
  - La v1.8.2 quitó BASS.
  - Sí quedan `le_audio_base_builder_add_bis()`, que acepta una configuración por
    BIS, y el ejemplo completo con BASE en el tag `v1.8`.
  - `gap_extended_advertising_set_adv_data()` acepta AD crudo, así que ahí van
    los datos de Harman.
  - Todo VERIFICADO en el código de BTstack `e385539` (2026-09-15).
- **Licencia:**
  - `LICENSE.RP` del Pico SDK cubre BTstack en las placas Pico W y Pico 2 W, y no
    restringe el controlador. VERIFICADO el texto; su aplicación a un controlador
    externo es INFERIDA.
  - Para uso personal basta la licencia pública de BTstack. VERIFICADO.
- **CPU (la condición que decide):**
  - Un codificador LC3 ocupa 5412 B de RAM a 48 kHz y 10 ms, así que 4 son
    ~22 kB de 520 kB. El agente lo midió compilando liblc3 en el host.
  - No hay cifras publicadas de liblc3 en Cortex-M. Las referencias son 18 MHz
    por canal con el LC3 optimizado de Packetcraft en un M33, y ~30 % de un
    nRF5340 a 128 MHz con el LC3 de Nordic. Las dos son REPORTADAS.
  - **Estimación:** 4 canales costarían entre ~72 y ≥150 MHz, contra 150 MHz por
    núcleo. **Podría caber justo, con un núcleo para LC3 y otro para USB y
    BTstack.** INFERIDO; hay que medirlo (P3).
- **Enlace UART:** 4 BIS de ~100–120 B cada 10 ms ocupan el 44–52 % de un UART a
  1 Mbaud. Cabe, con poco margen. INFERIDO, aritmética.

**Otros usos de la Pico 2 W:**

| Rol | ¿Sirve? | Marca |
|---|---|---|
| Emisor Auracast con su propia radio | **No** | REPORTADO + INFERIDO |
| Sniffer de Auracast | **No** (solo anuncios legacy) | INFERIDO |
| **Cerebro de la Fase 3 (H2b)** | **Con condiciones**: que la CPU alcance para LC3, que el speaker de 4 canales con feedback funcione, y armar BASE y Harman en BTstack | Piezas VERIFICADAS; el conjunto INFERIDO |
| Solo tarjeta USB de 4 canales (probe del lado USB) | Muy probable | INFERIDO |
| **Sonda SWD para la SuperMini** (`debugprobe_on_pico2.uf2` v2.3.1, o yapicoprobe) | Sí, sin LED con debugprobe. Sirve para recuperar una SuperMini si se pierde el bootloader. Con APPROTECT activo, recuperarla borra el bootloader UF2 | REPORTADO (https://github.com/raspberrypi/debugprobe, [foro](https://forums.raspberrypi.com/viewtopic.php?t=384415)) |
| Medir tiempos (pulsos con precisión de µs) | Sí; aporta poco frente al micrófono del PC | INFERIDO |

**Pico 2 W (H2b) frente a Pi Zero 2 W (H2)** (INFERIDO):

| | H2b · Pico 2 W | H2 · Pi Zero 2 W |
|---|---|---|
| Costo | Cero (ya está) | ~US$15 |
| Arranque | Instantáneo | Linux, ~20–30 s |
| Código | **C bare-metal**: TinyUSB + BTstack + liblc3. **No reutiliza** el núcleo Python del MVP | **El mismo núcleo Python** del MVP, con el backend `gadget` |
| Reloj | Feedback USB atado al consumo del BIG, igual que H2 | `Capture Pitch` |
| Riesgo principal | CPU para 4× LC3; emisor BTstack sin ejemplo público completo | 4 canales en Windows 11 sin probar; CPU de Python |

## 4. El reloj: el problema que cruza todas las alternativas

**Qué pasa hoy** (VERIFICADO en el código de Bumble, `bumble/host.py`,
`device.py`, `apps/auracast.py`, `audio/io.py`):
- `IsoPacketStream(link, max_queue_size)` bloquea `write()` hasta que el
  controlador devuelve paquetes completados (`HCI_Number_Of_Completed_Packets`).
  Esa es la **contrapresión**.
- `apps/auracast.py` usa `max_queue_size=64`, es decir, **hasta 640 ms en cola**.
- La entrada `device:` solo avisa "input overflow". **No hay remuestreo ni control
  de drift.**
- `IsoLink.get_tx_time_stamp()` (`HCI_LE_Read_ISO_TX_Sync`) existe y serviría para
  estimar la razón entre el reloj del controlador y el del host. Falta saber si el
  `hci_uart` de Zephyr en el nRF52840 lo implementa para BIS.
- **Hay un firmware hecho para esto: `bluekitchen/hci_uart_iso_timesync`.** Es un
  fork de `hci_uart` para NCS ≥3.2.1 con la SoftDevice Controller, y trae:
  - `CONFIG_BT_ISO_BROADCASTER=y`, H4 con RTS/CTS a 1 Mbaud, y overlays para el
    dongle y el DK del nRF52840;
  - un comando propio, `LE Read ISO Clock` (OGF 0x3f, OCF 0x200), que devuelve el
    reloj ISO en µs y a la vez levanta un GPIO.
  - VERIFICADO: https://github.com/bluekitchen/hci_uart_iso_timesync
  - Sirve igual para Bumble (H1) que para BTstack (H2b). **Es el candidato para
    flashear las SuperMini en P2** (INFERIDO: la SuperMini usa el mismo chip que
    el dongle). Su `CONFIG_BT_ISO_TX_BUF_COUNT=5` probablemente hay que subirlo
    para 4 BIS.

**Incluso PipeWire se sincroniza con CLOCK_MONOTONIC y no con el controlador.**
Según los comentarios de `iso-io.c`, *"Core v6.1 no da forma de sincronizar los
relojes del Controller y del Host"*. Detecta el drift por el llenado de las colas
y hace un "resync" con flush si la latencia pasa de 50 ms. VERIFICADO:
`spa/plugins/bluez5/iso-io.c`.

**Diseño propuesto para H1** (INFERIDO; las piezas están VERIFICADAS):
- Cola corta hacia el controlador: 2–3 SDU por BIS.
- Un **DLL** mide el nivel de esa cola (y, si existe, `TX_Sync`) y da la razón
  entre relojes. Es el patrón de PipeWire (`spa/utils/dll.h`) y de zita-ajbridge.
- Un **remuestreador con razón variable** entre la captura y el DSP:
  - `samplerate` 0.2.4 (libsamplerate): `process(input, ratio)` acepta una razón
    nueva en cada llamada y hace la transición suave; tiene ruedas para aarch64;
  - la alternativa es `soxr` 1.1.0, cuyo `ResampleStream(vr=True)` está marcado
    experimental.
  - VERIFICADO: [python-samplerate](https://github.com/tuxu/python-samplerate), [python-soxr](https://github.com/dofuuz/python-soxr).
- **Los 4 canales pasan por el mismo remuestreador**, así que el drift no
  desalinea los BIS entre sí.

**Alternativa en Linux sin remuestrear en la herramienta** (VERIFICADO el
mecanismo):
- Un `pw_stream` con `PW_STREAM_FLAG_DRIVER` hace de **driver del grafo** y
  escribe `clock.rate_diff`. Así las apps van al ritmo del BIG. Es el patrón de
  `module-pipe-tunnel.c`.
- Si otro nodo manda (por ejemplo, un micrófono ALSA en una llamada), el stream
  pasa a ser follower, y `pw_stream_set_rate()` (1.4.0+) activa el remuestreador
  adaptativo del propio PipeWire.
- **Esto exige C o Rust** (pipewire-rs 0.10.1 lo expone, pero advierte *"expect
  frequent breakage"*). Desde Python con `pw-record` por subprocess no se puede.

**En H2 el problema desaparece** (INFERIDO): la Pi mueve `Capture Pitch` según el
nivel de la cola, y el PC entrega exactamente al ritmo del BIG.

**En H3 hoy** (VERIFICADO): el firmware manda un SDU vacío o descarta 10 ms. Con
50 ppm de diferencia, eso es un corte cada ~200 s (INFERIDO).

## 5. Stack: lenguaje e integraciones

### 5.1 Opciones de lenguaje

| | Python + Bumble | Rust (audio) + Python (Bumble) | C: módulo de PipeWire + BlueZ | Firmware (Zephyr/NCS) |
|---|---|---|---|---|
| Emisor LE Audio | **Bumble como biblioteca**: `create_advertising_set`, `bap.BasicAudioAnnouncement` con una ubicación por BIS, `ManufacturerSpecificData`, `create_big`, `IsoPacketStream`. VERIFICADO | Bumble en un proceso aparte, unido por una tubería o un socket | `bap_bcast_source` de PipeWire; los datos de fabricante van por BlueZ | `nrf_auraconfig` / `nrf_audio` |
| Captura | Subprocess (`pw-record`, audiotee) o bibliotecas (PyObjC, PyAudioWPatch) | `pipewire-rs`: sink y driver nativos | Nativa | USB UAC2 |
| Reloj | Remuestreador + DLL | Driver del grafo en Linux; `rubato` 5.0 en los demás | Nativo de PipeWire | Feedback USB |
| Multiplataforma | **Linux, macOS, Windows y la Pi** con el mismo código | Linux nativo; el resto con más trabajo | Solo Linux | Cualquier PC por USB |
| Huella | N1 + N2 | N1 + N2 | **N4** | N0 |
| Código estimado | **350–600 líneas** para el emisor propio (§5.2) + captura + DSP + CLI | Doble: dos lenguajes y dos procesos | Poco código, mucha configuración | Semanas ([06](06-opcion-c-nrf5340.md)) |
| Riesgos | Jitter del GIL y de asyncio con tramas de 10 ms; `lc3py` sin ruedas para aarch64 (hay que compilarlo en la Pi) | Complejidad | Root; versiones mínimas; multi-BIS a JBL no demostrado | Licencias Nordic |

**Recomendación (INFERIDO): Python + Bumble para el MVP.**
- Es el único stack que corre igual en el Mac actual, en Linux y en la Pi.
- Es donde ya viven las herramientas de Auracast y de JBL ([04](04-implementaciones-y-stacks.md)).
- Hace innecesario el parche de `auracast.py` (§5.2).

**Rust queda como mejora puntual** para el backend de Linux (driver del grafo) si
el remuestreo en Python resulta insuficiente. Se decide con la medición de P2, no
antes.

### 5.2 Emisor propio en vez de parchear `apps/auracast.py`

- `run_transmit` son ~285 líneas y trae dos problemas:
  - asume 1–2 canales por fuente;
  - repite `index=1` en cada subgrupo ([05](05-opcion-a-bumble.md)).
  VERIFICADO.
- Un emisor propio que importe Bumble costaría **350–600 líneas** (INFERIDO), e
  incluiría:
  - el transporte;
  - el anuncio con los datos de Harman;
  - una BASE con 1 subgrupo y 4 BIS, cada uno con su ubicación;
  - el BIG;
  - 4 codificadores LC3 mono;
  - la captura de 4 canales;
  - el lazo de drift;
  - los logs.
- Como probe, **el parche sigue siendo más rápido para E4** ([05](05-opcion-a-bumble.md)
  §6). El emisor propio es código de producto y espera la decisión de seguir.

### 5.3 Integraciones

| Integración | Cómo | Huella | Marca |
|---|---|---|---|
| PipeWire (Linux) | Sink propio en tiempo de ejecución; salida por defecto sin persistir (§2.1); latencia declarada en el nodo para el lip-sync ([07](07-software-de-audio-en-el-pc.md) §7) | N1 | VERIFICADO / INFERIDO |
| Core Audio (macOS) | Tap privado por PyObjC, o audiotee por subprocess | N1 + N2 | VERIFICADO / REPORTADO |
| WASAPI (Windows) | Loopback con PyAudioWPatch | N1 | REPORTADO |
| Bumble | `serial:` hacia la SuperMini | N0–N2 | VERIFICADO |
| BASS (asignar un BIS a cada parlante) | Bumble ya trae un cliente (`auracast assist --command add-source`) y `SubgroupInfo(bis_sync=…)` acepta una máscara por BIS. **Solo sirve si los JBL exponen BASS** (lo decide E4) | N1 | VERIFICADO (código) |
| Control de JBL (modo receptor sin tocar el botón) | `SET_AURACAST_BROADCAST` y el servicio `DFFD` de jbl-aura-play-together; firmware y PID con openjbl ([04](04-implementaciones-y-stacks.md) §6) | N1 | VERIFICADO (en otros modelos) / INFERIDO (Go 4, Charge 6) |
| Calibración | **Chirp propio + correlación cruzada**, grabando por la misma captura. REW tiene una API REST, pero las mediciones automáticas requieren la licencia Pro. `jack_delay` está pensado para cable | N1 | REPORTADO (REW, `jack_delay`) / INFERIDO |
| Upmix | Matriz en NumPy (`simple`, `psd`, LCRS) y FFmpeg `surround` por subprocess como opción de calidad ([07](07-software-de-audio-en-el-pc.md) §4) | — | VERIFICADO (algoritmos) |
| Perfil | TOML en `~/.config/jblsync/` (N3), o con `--profile` explícito (N1) | N1 / N3 | — |
| Fuentes de red (Fase 3) | shairport-sync (AirPlay 2) y librespot en la Pi | N0 en el PC | VERIFICADO (documentación) |

## 6. Arquitectura propuesta (INFERIDA)

```
                    jblsync (un proceso Python, asyncio)
 ┌───────────────────────────────────────────────────────────────────────┐
 │ backend de captura          núcleo                     backend emisor │
 │ ─────────────────           ──────                     ────────────── │
 │ linux:  pw-record -P       ┌─► remuestreador (razón ◄─ DLL)           │
 │         Audio/Sink (N1)    │   variable, 4 canales       ▲            │
 │ macos:  process tap (N1)   │        ▼                    │ nivel de   │
 │ win:    WASAPI loopback ───┤   DSP: upmix / LCRS,        │ cola, TX_Sync
 │ file:   WAV / stdin        │   retardo y ganancia        │            │
 │ gadget: ALSA UAC2 +        │        ▼                    │            │
 │         Capture Pitch (H2) │   4× LC3 mono ──► 4× IsoPacketStream ──► Bumble
 │                            │                    (cola corta)  serial:  │
 │                            └─ perfil TOML: dirección → canal,          │
 │                               retardo, ganancia (fail-closed)          │
 └───────────────────────────────────────────────────────────────────────┘
                                                        ▼ SuperMini nRF52840
                                          1 BIG · 4 BIS · datos de Harman
                                  Go 4 (FL)  Go 4 (FR)  Go 4 (RL)  Charge 6 (RR o C)
```

**Subcomandos del MVP:**

| Subcomando | Qué hace | Toca el sistema |
|---|---|---|
| `jblsync doctor` | Muestra la señal observada: controlador por `serial:`, comandos ISO, versión de PipeWire o de macOS, permisos (grupo, TCC). No cambia nada | No |
| `jblsync scan` | Lista los JBL con datos de Harman, en modo transmisor o esperando, con su firmware si openjbl lo lee | No |
| `jblsync tone --channel FL` | Manda un tono por un solo BIS, para identificar qué parlante suena y armar el perfil | No |
| `jblsync play --input file:x.wav\|system\|app:<nombre> --layout quad\|lcrs --upmix simple\|psd\|surround` | El emisor completo | N1 (captura del sistema) |
| `jblsync calibrate` | Chirp por BIS, micrófono, y retardo y ganancia por canal, que se guardan en el perfil | N3 (solo el perfil) |
| `jblsync assign` | Escribe `BIS_Sync` por BASS, si E4 muestra que hace falta | No (solo en los parlantes) |

**Supervisión (C9):** `jblsync` corre la captura en un proceso hijo con timeout.
Lo único que no es N1, la salida por defecto de WirePlumber, lo restaura el proceso
padre. El chequeo con `kill -9` está en §2.4.

## 7. Plan de investigación y desarrollo

La Fase 1 del roadmap (E1–E7 y la decisión i-7c8794-0d129c) **no cambia**. Este
plan agrega probes de software que corren **en paralelo**, porque no dependen de
que los JBL respeten la selección de BIS, y ordena la Fase 2 en hitos.

### 7.1 Probes de software (Fase 1, en paralelo, sin código de producto)

| Id | Probe | Pregunta | Depende de | Hardware |
|---|---|---|---|---|
| **P1** (i-7c8794-fd5f03) | Captura sin huella | En Mac: ¿un process tap por PyObjC o audiotee entrega PCM estéreo continuo, y `CATapMuted` silencia los parlantes? En Linux: ¿`pw-record -P media.class=Audio/Sink` aparece como sink elegible y desaparece al morir el proceso? ¿La salida por defecto sin persistir deja `~/.local/state/wireplumber/` intacto tras un `kill -9`? | Nada | Ninguno |
| **P2** (i-7c8794-cb208f) | Drift entre el PC y el controlador | ¿Cuántos ppm hay entre el reloj del PC y el del controlador, medidos con el nivel de la cola de `IsoPacketStream` y con `TX_Sync`, si existe? ¿Un lazo DLL con `samplerate` lo absorbe sin cortes durante 1 hora? | E1 (una SuperMini creando un BIG) | SuperMini |
| **P3** (i-7c8794-346d45) | Emisor dedicado: tarjeta USB de 4 canales | **Con la Pico 2 W, que ya está**, en dos pasos: **(a)** benchmark de liblc3 en el M33 (4 canales, 48 kHz, 10 ms, contando ciclos con DWT CYCCNT); **(b)** speaker TinyUSB de 4 canales con feedback, que enumere sin driver en Mac, Linux y Windows 11, con el nivel de FIFO registrado durante horas. Si (a) no cabe, la Fase 3 pasa a la Pi Zero 2 W (gadget `f_uac2`) | Nada | Pico 2 W (ya está); la Zero 2 W solo si (a) falla |

Cada probe va en `probes/<nombre>/` y deja su resultado en
`docs/research/experimentos/` antes de borrarse (d-7c8794-3208b7).

### 7.2 MVP (Fase 2, solo si la decisión es seguir)

Una entrada paraguas en el roadmap (i-7c8794-2fe665). Los hitos van en orden, y
cada uno cierra algo que ya está en el roadmap:

| Hito | Qué entrega | Cierra o usa | Criterio de terminado (MEDIDO) |
|---|---|---|---|
| **M0** | Esqueleto: `doctor` y `scan`, que solo leen | — | `doctor` muestra en la SuperMini la señal ISO observada |
| **M1** | `play --input file:` y `tone`: el emisor propio con 4 BIS, una ubicación por BIS y datos de Harman | Prototipo del emisor multicanal (i-7c8794-5f25b0) | Cada parlante reproduce su canal; desfase entre BIS medido con micrófono |
| **M2** | `play --input system` en Linux y Mac, con el lazo de drift | P1, P2 | 1 hora sin cortes y sin crecer la latencia; `kill -9` no deja rastro |
| **M3** | Upmix (`simple`, `psd`, `surround`), las distribuciones quad y LCRS, retardo y ganancia por canal | Upmix (i-7c8794-c7ccb9) | Comparación de oído anotada ([07](07-software-de-audio-en-el-pc.md), experimento 3) |
| **M4** | `calibrate` con el micrófono | Calibración (i-7c8794-1ab281) | Par frontal alineado a <1 ms, medido ([07](07-software-de-audio-en-el-pc.md) §7.2) |
| **M5** | `assign` por BASS, y el modo receptor automático de los JBL | Asistente BASS (i-7c8794-365524) | Los 4 parlantes quedan asignados sin tocar ningún botón |

**Ramas según lo que muestren las mediciones:**
- **Si E4 falla** (los JBL no eligen su BIS): la captura, el DSP y la calibración
  se reutilizan, y el backend emisor pasa a ser combine-stream sobre A2DP en Linux
  (i-7c8794-f6edc9). Es otro backend, no otra herramienta.
- **Si P2 muestra que el remuestreo en Python no alcanza**: el backend de Linux se
  reescribe como driver del grafo en Rust o C (§4). El resto sigue en Python.

### 7.3 Fase 3: emisor dedicado (i-7c8794-80f3ac)

La misma herramienta corriendo en una Raspberry Pi:
- con el backend `gadget` (ALSA UAC2 + `Capture Pitch`);
- la SuperMini por el UART;
- opcionalmente, shairport-sync y librespot como entradas de red.

En el PC no se instala nada, y funciona en los tres sistemas.

**Depende de** P3 y de M2.

**Variante H2b** (§3.1): la Pico 2 W en lugar de la Pi. Usa TinyUSB, BTstack y
liblc3 en C, con la SuperMini por UART. No reutiliza el núcleo Python, pero no
cuesta nada y arranca al instante.

**Lo que se pierde:** la latencia declarada para el lip-sync, porque el PC cree
que es una tarjeta USB rápida. En video queda el ajuste manual del reproductor.

### 7.4 Cronograma orientativo (INFERIDO)

```
ahora ──► P1 (Mac y Linux, sin hardware)          P3 (Pico 2 W: LC3 y USB)
llegan las SuperMini ──► E1 ──► P2 ──► E2 … E4 ──► decisión de seguir
                                                    │
                          sí ◄──────────────────────┴──────────► no: camino A2DP
                          M0 → M1 → M2 → M3 → M4 → M5            (mismo núcleo)
                                      └──► Fase 3 (Pi, con P3)
```

## 8. Decisiones que quedan abiertas (le tocan al usuario)

| Decisión | Opciones | Recomendación (INFERIDA) | Cuándo |
|---|---|---|---|
| Sistema operativo de referencia | Linux / macOS / los dos | **Linux como destino, macOS como estación de trabajo.** Los dos con el mismo código | Antes de M0 |
| Stack | Python + Bumble / Rust + Python / C con BlueZ y PipeWire | **Python + Bumble**; Rust solo si P2 lo exige | Antes de M0 |
| Dónde vive el emisor | H1 (PC) / H2 (Pi) / H3 (nRF5340) | **H1 para el MVP, H2 como Fase 3** | H2 después de P3 |
| Con qué hacer la Fase 3 | Pico 2 W (ya está; C bare-metal) / Pi Zero 2 W (~US$15; reutiliza el Python) | **Probar primero la Pico** con el benchmark de P3(a), que dura horas. Comprar la Zero 2 W solo si la CPU no alcanza, o si se prefiere mantener un solo lenguaje | Después de P3(a) |
| Nombre de la herramienta | `jblsync` es provisional | — | Antes de M0 |

Ninguna de estas decisiones está registrada en [decisions.md](../decisions.md).
Se registran cuando el usuario las tome.

## Lo que no se pudo determinar

- Si `pw-record` con `media.class=Audio/Sink` funciona con WirePlumber 0.5.x
  (autoconexión, si aparece en los paneles).
- Si el tap privado y el aggregate privado de macOS se destruyen siempre al morir
  el proceso, también tras una caída, y si el permiso de audio del sistema se
  vuelve a pedir cada cierto tiempo.
- Si el `hci_uart` de Zephyr en el nRF52840 implementa `HCI_LE_Read_ISO_TX_Sync`
  para BIS, y con qué precisión.
- El jitter de pyserial-asyncio por polling en Windows con tramas ISO de 10 ms.
- Si Windows 11 usa bien un gadget UAC2 de 4 canales en modo asíncrono.
- Si la CPU de una Zero 2 W alcanza para Bumble, 4 LC3 y el remuestreo en Python.
- Si los JBL exponen BASS, y si `SET_AURACAST_BROADCAST` funciona en el Go 4 y el
  Charge 6. Esa es la pregunta central (E4) y no se responde leyendo.
