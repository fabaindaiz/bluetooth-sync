# Plan de desarrollo

Aquí están las ideas aceptadas que todavía no se construyen. **No es una promesa ni
una orden de trabajo.** Para cada una se anota ahora, mientras está claro, **con
qué choca**, qué la favorece y qué hay que decidir antes.

**Cómo mantenerlo:**
- Cada entrada tiene un estado: **Planificado**, **A medias**, **Hecho**, **Cerrado
  por medición** o **Bloqueado por algo externo**.
- Ninguna entrada se borra.
- Una entrada se cierra en el mismo cambio que la termina.
- Los ids se generan con `bundle.py id i "<idea>"` y no cambian aunque cambie el
  texto.
- El título de cada entrada tiene la forma `### <Idea> · i-7c8794-…`, con el id
  después del punto medio: es lo que `bundle.py ids` lee como definición.

La base de todo es [docs/research/README.md](research/README.md).

## Plan desde el 2026-10-02

**El foco** (d-7c8794-a5b83b): Linux completo es el camino principal y el repositorio es sobre
audio —la cadena, el sonido envolvente, efectos y mejoras de software, cada uno con su
experimento—. Los otros dispositivos se suman después, por iteraciones. Cada etapa se mide en
`PC-Ryzen5` con los 3 Go 4; nada se da por bueno sin su número repetido (`CLAUDE.md`).

**Etapa 1 · Medir lo construido** (lo primero en `PC-Ryzen5`):
1. **Microcortes** ([experimentos/12](research/experimentos/12-microcortes-con-3-go-4.md),
   i-7c8794-7d4aec): el paso 0 (el journal, el reloj de la captura y la salida con `pw-top`),
   la línea base C2 —que es también el antes y después del arreglo de la lectura sinc— y C3,
   una variable a la vez.
2. **Calidad con micrófono** (experimentos/15, i-7c8794-50c1b3): si el Go 4 suma L+R, la
   respuesta por parlante con coherencia, y "directo contra motor" con la sonoridad igualada
   (i-7c8794-10ccb4).
3. **Escuchar la cadena tal como quedó**, 20 minutos con la pantalla Cadena y la franja de
   calidad abiertas.

**Etapa 2 · Mejorar el sonido, una cosa por vez** (cada una con su A/B ciego con la
sonoridad igualada, y se enciende por defecto solo si gana; d-7c8794-d1118c):
1. Graves y volumen (experimentos/14, i-7c8794-765571): la curva AVRCP de cada Go 4, el
   pasa-altos de protección, el bajo psicoacústico, el volumen por AVRCP y el limitador
   true-peak.
2. El presupuesto de realce y el techo de agudos del EQ.
3. Envolvimiento: la cola difusa (i-7c8794-bf63c2), y la sonda enmascarada para que el lazo
   funcione con música (i-7c8794-e3e40d, paso 2).
4. Después, en este orden: transitorios coherentes con cola decorrelada (HPSS), el banco del
   códec (i-7c8794-342646), el sink 5.1 y el loopback para el lip-sync (i-7c8794-ff1ce9), y el
   upmix tipo DirAC (i-7c8794-59cb30).

**Etapa 3 · Tiempo real** (i-7c8794-fd9732): la prueba de concepto de E/S nativa en Rust
([experimentos/13](research/experimentos/13-e-s-nativa-en-rust.md)); si cumple, el motor en
Rust etapa por etapa contra el oráculo numpy ([research/12](research/12-motor-de-audio-en-rust.md) §4).

**Etapa 4 · Más de 3 parlantes:** la Pico 2 W como emisor A2DP con BTstack (control directo
del protocolo), varios adaptadores de Bluetooth clásico, o Auracast con las SuperMini (E1–E5).
Se comparan en [research/13](research/13-dispositivos-pi-pico-y-panel-independiente.md).

**En paralelo con las etapas 1 y 2 · El panel como PWA en GitHub Pages** (i-7c8794-b10884,
d-7c8794-37f9bc): reutilizable, offline como app instalada, conectado por la red local al
servicio por HTTPS con un certificado propio y un token por cliente. Se construye en el Mac
con el servicio simulado; la app nativa queda para el final.

**Etapa 5 · Otros dispositivos:** la Raspberry Pi Zero 2 W como equipo independiente y la
Pico como puente (research/13). La app nativa de Android, al final de todo.

## Dónde estamos

**Fase 0 terminada (2026-09-25).** La investigación está en `docs/research/`.

El sistema se apoya en una **invariante central**: todos los parlantes reproducen
desde una **referencia de tiempo común**, con un canal asignado a cada parlante.

**Qué se sabe:**
- Bluetooth clásico (A2DP) no puede garantizar esa invariante.
- Auracast sí la garantiza, siempre que todos los canales vayan en un solo BIG
  (d-7c8794-203de2, provisional).
- Ya está demostrado que un JBL Go 4 recibe una transmisión Auracast desde Linux si
  lleva los datos de fabricante de Harman.

**La pregunta abierta que decide todo:** si los JBL reproducen solo el BIS que les
corresponde.

**Lo que cambió el 2026-09-28, midiendo en el equipo Linux:**
- **E1 está cerrado y salió negativo.** El Intel AX210 no puede transmitir Auracast,
  y tampoco puede sincronizarse a anuncios periódicos, así que **ni E2 ni E3–E5 se
  pueden hacer con el hardware que hay hoy**. Todo eso espera a las SuperMini
  (d-7c8794-b82ee9). **No hay que comprar nada nuevo:** la compra que ya se hizo es
  justo la que resuelve esto.
- **Primera evidencia sobre la pregunta que decide todo, y va en contra:** los
  parlantes no muestran ni BASS ni PACS, los dos únicos mecanismos que el estándar
  define para asignar un canal
  ([experimentos/02](research/experimentos/02-servicios-de-los-jbl-linux.md)). No es
  concluyente: falta conectarse por LE con los parlantes encendidos.
- **Lo que sí se puede hacer hoy en este equipo:** E8 (unicast con los Tune 770NC,
  que valida el camino ISO entero), el GATT de los parlantes, E6, E7 y P1.

**Hardware disponible:**
- 3× JBL Go 4 y 1× JBL Charge 6.
- Un PC con Linux (`PC-Ryzen5`, CachyOS, kernel 7.2.7) con un **Intel AX210**
  (2026-09-28, [experimentos/00-inventario-linux.md](research/experimentos/00-inventario-linux.md)).
  La tarjeta va por PCIe, pero **su Bluetooth es USB**. PipeWire 1.6.9 ya trae el
  códec LC3. **No sirve para transmitir Auracast: MEDIDO** (E1,
  [experimentos/03](research/experimentos/03-e1-iso-en-el-ax210.md)). Le faltan los
  bits 30, 31 y 13, así que no transmite ni escucha un BIS. **Sí sirve para unicast
  (CIS central y peripheral) y para A2DP**, o sea para E8, E6, E7 y P1.
- Un portátil con Linux (`HP-O16`, HP OMEN 16, i5-11400H, CachyOS, kernel 7.2.8),
  agregado el 2026-09-29 ([experimentos/00-inventario-hp-o16.md](research/experimentos/00-inventario-hp-o16.md)).
  **El mismo Intel AX210 con el mismo firmware** que `PC-Ryzen5` y las mismas versiones
  de BlueZ y PipeWire: no transmite Auracast (MEDIDO, sin `iso-broadcaster`), sirve para
  A2DP y CIS. Ningún JBL emparejado todavía, el Bluetooth está bloqueado por `rfkill` y
  no hay micrófono USB. Lo que aporta es que **se puede llevar al centro de la pieza**.
- Este Mac (Apple Silicon, macOS 27), que es la estación de trabajo.
- **5× SuperMini nRF52840** (clon de nice!nano), compradas el 2026-09-26 y aún no
  recibidas (d-7c8794-b82ee9). Van con `hci_uart` como controlador para Bumble; se flashean con
  el target `promicro_nrf52840`. En cada medición hay que anotar qué unidad y qué
  fuente de reloj de 32 kHz se usó ([06](research/06-opcion-c-nrf5340.md) §1).
- **1× Raspberry Pi Pico 2 W** (RP2350 + CYW43439), que el usuario ya tenía
  (anotada el 2026-09-26). **Su radio no sirve para Auracast**, porque no tiene
  advertising extendido. **El RP2350 sí puede ser el cerebro de la Fase 3**:
  tarjeta USB + LC3 + BTstack, con una SuperMini por UART. También sirve de
  sonda SWD para recuperar una SuperMini (debugprobe). Todo está en
  [08](research/08-integracion-y-plan.md) §3.1.

**Stack en estudio (2026-09-26):** el usuario eligió explorar las opciones **A**
(Python + Bumble en el PC) y **C** (nRF5340 como emisor dedicado), además de la
**combinada** (Bumble con un nRF por `hci_uart`). La comparación y el orden sugerido
están en [research/README.md](research/README.md) §"Opciones A y C comparadas":
primero A con el chip interno; si no sirve, la combinada o C.

**Estructura (2026-09-26):**
- **Stack:** Python + Bumble con hatch (d-7c8794-c23c20).
- **Repositorio:** monorepo con `host/` y `firmware/` (d-7c8794-5c014a,
  [08](research/08-integracion-y-plan.md) §6.1).
- **Esqueleto** del paquete `aurasync` (i-7c8794-f7f5b2), permitido por
  d-7c8794-f619c4.

**Siguiente paso (2026-09-28, después de cerrar E1):** todo lo que no necesita
transmitir, en este orden por relación información/costo:
1. **GATT de los parlantes encendidos**
   ([probes/02-gatt-jbl/enumerar.sh](../probes/02-gatt-jbl/enumerar.sh)): cierra
   `experimentos/02` y adelanta E4. No necesita root.
2. **E8**, unicast con los Tune 770NC: valida el camino ISO de Linux.
3. **P1** (captura sin huella), **E6** (línea base A2DP) y **E7** (USB-C del
   Charge 6), que no necesitan LE Audio.

Lo que espera a las SuperMini: E2, E3, E4 y E5.

Etapas del plan:

```
Fase 1 · Factibilidad (probes, sin producto)            ← en curso
  inventario ✔ → E1 ✔ (NO: el AX210 no transmite)
                   └─ E2 → E3 → E4 ──► decisión de seguir o no
                      ↑ los cuatro esperan las SuperMini nRF52840
  se puede hacer ya, sin transmitir:
    GATT de los parlantes · E8 (unicast, Tune 770NC) · E6 (A2DP) · E7 (USB-C) · P1 (captura)
  después de E1: P2 (drift) · P3 (Pico 2 W: LC3 y USB)
Fase 2 · MVP, camino A (Auracast, un BIG)                ← solo si E4 sale bien
  herramienta CLI: M0 → M1 emisor → M2 captura → M3 upmix → M4 calibración → M5 BASS
Camino alternativo · A2DP                                ← solo si E4 sale mal
  mismo núcleo, otro backend emisor
Fase 3 · Emisor dedicado (Pico 2 W o Pi como tarjeta USB) ← después de M2 y P3
```

El análisis de integración que ordena P1–P3, los hitos del MVP y la Fase 3 está en
[research/08-integracion-y-plan.md](research/08-integracion-y-plan.md).

## Fase 1: factibilidad

Todo en esta fase es un **probe**. El código va en `probes/<nombre>/` y se borra
cuando su resultado queda anotado en `docs/research/experimentos/`
(d-7c8794-3208b7). Cada resultado lleva la marca **MEDIDO**, con el número, el
equipo, las versiones (kernel, BlueZ, PipeWire, Bumble y el firmware de los JBL) y
la fecha.

### Inventario del equipo Linux · i-7c8794-d9c834
**Estado: Hecho (2026-09-28).** Las dos mitades están medidas.
- **Hecha la mitad del Mac** (2026-09-26, en
  [experimentos/00-inventario-mac.md](research/experimentos/00-inventario-mac.md)):
  el chip es un MediaTek MT7932 por PCIe con LE Audio, pero Bumble no puede llegar
  a él en macOS. El Mac sirve como estación de desarrollo para A y C.
- **Hecho el equipo Linux** (2026-09-28, en
  [experimentos/00-inventario-linux.md](research/experimentos/00-inventario-linux.md)):
  Intel AX210 (firmware BT `202-5.26`), BlueZ 5.87, PipeWire 1.6.9 con LC3,
  WirePlumber 0.5.17, kernel 7.2.7. `scripts/check.sh` pasa acá con
  `PY=python3.14`, lo que cierra el pendiente de i-7c8794-f7f5b2.
- **Hecho el portátil `HP-O16`** (2026-09-29, en
  [experimentos/00-inventario-hp-o16.md](research/experimentos/00-inventario-hp-o16.md),
  con la tabla de los tres equipos): mismo AX210 y mismo firmware (SHA1 idéntico), sin
  `iso-broadcaster`. `scripts/check.sh` pasa. `btmgmt info` se lee **sin root**; el probe
  del inventario ya no usa sudo y sirve en cualquier equipo Linux.
- **Capacidades del controlador: medidas** y con su propio experimento, porque el
  resultado cierra E1 ([experimentos/03](research/experimentos/03-e1-iso-en-el-ax210.md)).
  La regla de sudo acotada está instalada y se quita con
  `sudo rm /etc/sudoers.d/bluetooth-sync`.

**Qué es:** identificar qué tiene el equipo, con estos comandos:
- el chip Bluetooth: `lspci -nn`, `lsusb`, `dmesg | grep -i bluetooth`;
- las versiones: `uname -r`, `bluetoothctl --version`, `pipewire --version`,
  `wireplumber --version`;
- las capacidades: `bluetoothctl` → `menu mgmt`, donde se busca
  `iso-broadcaster` y `cis-central`.

**Con qué choca:** con nada. Solo lee.

**Qué la favorece:** Collabora documenta cómo leer las capacidades del controlador
([02](research/02-le-audio-auracast-linux.md) §2).

**Qué hay que decidir antes:** nada.

**El resultado va a:** `docs/research/experimentos/00-inventario-linux.md`.

### E1: ¿el controlador puede transmitir por ISO? · i-7c8794-3f730a
**Estado: Hecho (2026-09-28). La respuesta es NO**, medida por dos caminos
independientes: los bits de LE Features (`ff 59 01 3c ae 00 00 00`) y la lista de
Supported Commands en una traza de `btmon`. El resultado está en
[experimentos/03](research/experimentos/03-e1-iso-en-el-ax210.md).

**Qué salió, en una línea:** el Intel AX210 con firmware `202-5.26` **no tiene los
bits 30 (Isochronous Broadcaster), 31 (Synchronized Receiver) ni 13 (LE Periodic
Advertising)**. Sí tiene **CIS central y peripheral**, y el socket ISO del kernel
funciona con la bandera experimental.

**Las tres consecuencias:**
1. **No se puede transmitir desde este equipo.** E3, E4 y E5 quedan bloqueados hasta
   que lleguen las SuperMini (d-7c8794-b82ee9). No hay que comprar nada más: la
   opción que ya se compró es justo la que resuelve esto.
2. **Peor de lo esperado:** sin el bit 13 ni el 31, este adaptador **tampoco puede
   leer la BASE ni el BIGInfo** de los propios JBL. E2 también necesita otro
   controlador; se creía que al menos podría escuchar.
3. **Mejor de lo esperado:** con CIS se puede probar **LE Audio unicast** contra los
   JBL Tune 770NC, que exponen PACS y ASCS. Eso valida el camino ISO completo
   (bandera, socket, LC3, PipeWire) sin depender de transmitir. Nueva entrada: E8.

**Trampa que costó un intento:** en `main.conf`, un comentario al final de la línea
de `KernelExperimental` se lee como parte del UUID y BlueZ descarta el valor sin
avisar (`Invalid KernelExperimental UUID`). El servicio arranca igual.

**La lectura definitiva no es `btmgmt info`, son los bits de LE Features** del
controlador, en `/sys/kernel/debug/bluetooth/hci0/features`: el bit 30 es
Isochronous Broadcaster (crear un BIG), el 31 Synchronized Receiver (recibir un BIS,
lo que falta de E2), el 28 CIS Central (unicast) y el 13 Periodic Advertising (leer
la BASE). `btmgmt info` muestra lo que ve BlueZ, que es una capa más arriba.

**Qué es:** confirmar que el controlador puede crear un BIG, con dos intentos:
- activar `Experimental` y `KernelExperimental` en BlueZ y crear un BIG de prueba;
- si eso falla, probar Bumble directamente sobre el dispositivo HCI.

**Con qué choca:** con que los modos experimentales de BlueZ cambian la
configuración del sistema. Hay que anotar cómo revertirlo antes de tocar
`/etc/bluetooth/main.conf`.

**Qué la favorece:** MT7921 e Intel BE200 están reportados como compatibles. El
AX210 solo soporta CIS según reportes. Realtek RTL8852BE falla.

**Cómo probarlo con Bumble:** `bumble-controller-info hci-socket:0`, con BlueZ
detenido ([05](research/05-opcion-a-bumble.md) §7). No requiere hardware nuevo.

**Qué hay que decidir antes:** **si el chip no sirve, qué comprar.** Las opciones,
comparadas en [research/README.md](research/README.md) §"Opciones A y C
comparadas":
1. un **dongle nRF52840 (US$11.69) con `hci_uart`** como controlador para Bumble.
   Es la opción combinada: la más barata, pero sin reportes con un JBL;
2. un **nRF5340 Audio DK (US$172.57) con `nrf_auraconfig`** como emisor dedicado.
   Es el camino más confiable a la prueba decisiva
   ([06](research/06-opcion-c-nrf5340.md) §10);
3. una tarjeta M.2 con MT7921 o BE200, si el equipo tiene ranura.

`hci_usb` de Zephyr **no** sirve como controlador, porque no transporta ISO.

Es una decisión con costo en dinero, así que es tuya.

**Decidido (2026-09-26, d-7c8794-b82ee9):** se compró una variante de la opción 1, **5
SuperMini nRF52840** en vez del dongle. Con ellas, E1 también se puede hacer desde el
Mac: flashear `hci_uart` (copiando la configuración del dongle), y luego correr
`bumble-controller-info serial:/dev/cu.usbmodem…` y crear un BIG de prueba. Las
opciones 2 y 3 quedan abiertas si esta no alcanza.

### E2: leer el anuncio y la BASE de los propios JBL · i-7c8794-a999d3
**Estado:** A medias.
- **Hecho desde el Mac con CoreBluetooth** (2026-09-26,
  [experimentos/01](research/experimentos/01-e2-anuncios-jbl-mac.md)): los datos de
  fabricante son iguales para el Go 4 y el Charge 6 (`87:…dffd`). La transmisión
  del Charge 6 va sin cifrar (PBP `04`).
- **Par estéreo de Go 4 (rojo y azul), reproduciendo:** no se ve ningún 0x1852 en
  30 s ni en 45 s. En reposo, el byte 2 parece indicar el color y el `60` del
  byte 9, el modo estéreo.
- **Siguiente paso barato, desde el Mac:** escanear 60 s *mientras* se forma el par
  estéreo, por si el anuncio aparece solo en ese momento.
- **Falta** la BASE y el BIGInfo, que requieren sincronizarse a los anuncios
  periódicos (Bumble `scan` con un controlador accesible, o
  `auracast-hackers-toolkit`). **Con las SuperMini (d-7c8794-b82ee9) se puede hacer desde el
  Mac** con `bumble-auracast scan` sobre `serial:`, apenas lleguen.

**Bloqueado por hardware (2026-09-28).** E1 midió que el **AX210 no tiene LE
Periodic Advertising (bit 13) ni Synchronized Receiver (bit 31)**, así que este
equipo **no puede sincronizarse a anuncios periódicos**: ni la BASE ni el BIGInfo se
pueden leer acá. Falta un controlador que sí pueda — las SuperMini
(d-7c8794-b82ee9). Lo que sí se puede hacer en Linux es lo mismo que se hizo en el
Mac: ver los datos de fabricante con un `scan le`, que funciona sin root.

**Qué es:** escanear un Go 4 en modo transmisor, un par estéreo de Go 4 y el Charge
6, para anotar:
- sus datos de fabricante (el sufijo `dffd` de cada modelo);
- la BASE: cuántos BIS, qué asignación de canales y qué presentation delay;
- si la transmisión va cifrada.

**Con qué choca:** con nada.

**Qué la favorece:** el comando `bumble-auracast scan` ya decodificó un Go 4
([01](research/01-parlantes-jbl.md) §3). También existen `auracast-hackers-toolkit`
(dongle nRF52840) para capturar BIS, y openjbl para leer el PID y el firmware de
cada parlante ([04](research/04-implementaciones-y-stacks.md) §6).

**Qué hay que decidir antes:** nada. Es el experimento más barato con más
información: dice cómo transporta L/R el propio JBL, y eso es lo que hay que imitar.

### E3: transmisión mono a un Go 4 y al Charge 6 · i-7c8794-e4255c
**Estado:** Planificado. Depende de E1 y E2.

**Qué es:** repetir el resultado conocido, un BIS mono con los datos de fabricante
de Harman desde Bumble o PipeWire, y confirmar que el **Charge 6** también acepta.
Hay que probar con 40 y 80 ms de presentation delay.

**Con qué choca:** con nada.

**Qué la favorece:** la receta de Collabora (PipeWire) y la de Bumble.

**Qué hay que decidir antes:** si el prototipo se basa en **Bumble** (Python, usa
el controlador directamente sin BlueZ) o en **PipeWire** (integrado al audio del
sistema). E3 debe probar los dos si el controlador lo permite, y anotar cuál fue
menos frágil. Las cuatro opciones de stack, con sus costos, están en
[04](research/04-implementaciones-y-stacks.md). Si se elige Bumble, hay que saber
que para 4 canales `auracast.py` necesita un parche (índices de BIS por subgrupo y
fuentes mono fijas en FRONT_LEFT). Si se elige PipeWire, los datos de fabricante
se ponen desde BlueZ, porque PipeWire no tiene una clave para eso.

### E4: estéreo en 2 BIS y selección de canal (la prueba decisiva) · i-7c8794-eeac13
**Estado:** Planificado. Depende de E3.

**Qué es:** transmitir un BIG con 2 BIS (FL y FR) y observar qué reproduce cada Go
4 en tres casos:
- **(a)** sin intervenir, para ver si mezcla, toma el primero o elige según su
  ubicación;
- **(b)** actuando como Broadcast Assistant y escribiendo `BIS_Sync` por BASS en
  cada parlante;
- **(c)** agregando un BIS mono aparte, como recomienda el SIG.

**Con qué choca:** si hay que escribir `BIS_Sync`, los JBL tienen que exponer
BASS. **Primera evidencia propia, y va en contra** (2026-09-28,
[experimentos/02](research/experimentos/02-servicios-de-los-jbl-linux.md)): en la
caché de BlueZ del equipo Linux, los 2 Go 4 y el Charge 6 **no exponen ni BASS
(0x184F) ni PACS (0x1850)**, mientras que los JBL Tune 770NC del usuario sí exponen
PACS, ASCS, VCS, MICS, CAS y TMAS. Si se confirma conectando por LE, **las dos vías
del estándar quedan cerradas** y la asignación de canal dependería de algo
propietario de JBL. No es concluyente todavía: es caché de un emparejamiento BR/EDR,
con los parlantes apagados.

**Qué la favorece:** el estándar define los dos mecanismos
([02](research/02-le-audio-auracast-linux.md) §4), y Bumble ya transmite 2 BIS
marcados FL y FR.

**Qué hay que decidir antes:** qué fuente de audio de prueba usar. Una señal
distinta por canal (un tono o una voz "izquierda" y "derecha") permite reconocer de
oído qué BIS reproduce cada parlante.

### E5: 4 BIS en un BIG, con la alineación medida · i-7c8794-2cf5e1
**Estado:** Planificado. Depende de que E4 salga bien.

**Qué es:** un BIG con 4 BIS, uno por parlante (3 Go 4 y el Charge 6). Se mide con
un micrófono la diferencia de tiempo entre parlantes, al inicio y después de 30
minutos. **Este experimento confirma o corrige d-7c8794-203de2.**

**Con qué choca:** con el ancho de banda. Unos 3 BIS de `48_2_x` caben por
intervalo (INFERIDO), así que puede hacer falta un preset de 24 kHz. También con el
límite de BIS que tenga el controlador.

**Qué la favorece:** `bluez5.bcast_source.config` acepta `bis[]` con su propia
`audio_channel_allocation`.

**Qué hay que decidir antes:**
- qué calidad es aceptable si hay que bajar a 24 kHz;
- **qué desfase entre parlantes se considera tolerable** (propuesta: menos de 5 ms
  para una imagen estéreo y menos de 20 ms para los traseros).

### E8: LE Audio unicast con los Tune 770NC, para validar el camino ISO · i-7c8794-ef8389
**Estado: A medias (2026-09-28). Salió bien:** el camino ISO de Linux funciona de
punta a punta ([experimentos/04](research/experimentos/04-e8-unicast-le-audio-tune-770nc.md)).
Los Tune se conectan en perfil **`bap-duplex` con LC3**, se estableció un CIG con **2
CIS** (uno por canal), ISO interval **7,5 ms**, PHY **LE 2M**, SDU **60 B**, CIG
Synchronization Delay **4632 µs**, y salieron 2214 paquetes `LE-CIS`.

**Tres cosas que esto cambia:**
1. **Baja el riesgo del plan.** Cuando lleguen las SuperMini, lo único nuevo a
   depurar es el emisor: socket ISO, LC3 en software, QoS y el reparto en streams ya
   están medidos funcionando.
2. **Mueve la disputa de PipeWire.** `pw-dump` muestra **un nodo interno por stream
   isócrono**, agrupados en un *device set* y expuestos con un **combine-sink** como
   un solo sink estéreo. El mecanismo de repartir un estéreo en varios streams de un
   mismo grupo existe y funciona (medido en unicast; con `bis[]` sigue INFERIDO
   hasta E5).
3. **Aviso para E3 y E5:** con estos audífonos se negociaron **32 kHz y 7,5 ms**, no
   los `48_2_x` que asume [02](research/02-le-audio-auracast-linux.md) §4. **La
   negociación la manda el receptor**, así que lo que acepten los JBL puede no ser lo
   que el proyecto planea pedir.

**Qué falta para cerrarlo:** confirmar de oído que suena; capturar desde el cambio de
perfil para tener **frecuencia de muestreo y presentation delay medidos** y no
inferidos; y leer el firmware de los Tune.

**De dónde salió:** de dos mediciones del 2026-09-28 que se cruzan. E1 midió que el
AX210 **sí** tiene CIS central y que el socket ISO del kernel funciona
([experimentos/03](research/experimentos/03-e1-iso-en-el-ax210.md)), y al revisar los
emparejamientos apareció que los **JBL Tune 770NC exponen PACS, ASCS, VCS, MICS, CAS
y TMAS** ([experimentos/02](research/experimentos/02-servicios-de-los-jbl-linux.md)).
O sea que hay un emisor y un receptor unicast en la misma casa.

**Qué es:** reproducir audio por LE Audio unicast (CIS) desde este equipo a los Tune
770NC, con PipeWire en rol `bap_sink`… en realidad el PC es el cliente: `bluez5.roles`
tiene que incluir el rol de central. Se mide:
- si se establece el CIS y se oye audio;
- qué presentation delay se negocia, y con qué preset LC3;
- si `Transparent` como único códec del controlador sobre LE CIS confirma que LC3 va
  en software (E1 §5 lo dice: INFERIDO hasta verlo funcionar).

**Qué valida, y por eso vale la pena:** el camino ISO completo en Linux —bandera
experimental, socket ISO, liblc3, PipeWire y la negociación de QoS— **sin depender de
poder transmitir**. Cuando lleguen las SuperMini, lo único nuevo a depurar sería el
emisor, no el stack entero. Es el único experimento LE Audio que este equipo puede
hacer hoy.

**Con qué choca:** con nada del sistema más de lo que ya cambió E1
(`Experimental = true` y el socket ISO en `main.conf`, que se revierte con
`probes/e1-iso/03-revertir.sh`).

**Qué no responde:** nada sobre los parlantes ni sobre la asignación de canal. Los
Tune son audífonos unicast; no hay BIG ni BIS acá.

**Qué hay que decidir antes:** nada.

**El resultado va a:** `docs/research/experimentos/`.

### Calibración rápida y recalibración continua · i-7c8794-33c4bd
**Estado: el lazo está construido y probado en simulación (2026-09-29); falta validarlo con
parlantes.** El diseño de la calibración está en
[experimentos/06](research/experimentos/06-calibracion-rapida-y-recalibracion.md) y el lazo
cerrado en
[experimentos/08](research/experimentos/08-lazo-de-recalibracion-en-simulacion.md).

**Qué es:** medir retardo y ganancia de cada parlante correlacionando lo que capta el
micrófono contra **la señal que se le mandó a cada uno**. Funciona porque el sistema ya le
manda a cada parlante una versión **decorrelacionada** del material, que es lo que produce
el envolvimiento: **la condición del efecto es la condición de la medición**.

**Lo que dice la comparación** (SIMULADO, retardos conocidos, 12 repeticiones por celda):
- **2 segundos de ruido de banda ancha ya dan toda la precisión.** Alargar no mejora.
- Es unas **170 veces más preciso que las ráfagas tonales** (0,00 contra 1,70 ms), porque
  la resolución va como 1/ancho de banda.
- **Con música anda igual de bien que con ruido**, que es lo que habilita recalibrar
  **sin interrumpir la reproducción ni reiniciar los streams**. *Matizado el 2026-09-29
  ([experimentos/08](research/experimentos/08-lazo-de-recalibracion-en-simulacion.md)):
  anda igual de bien **con segmentos de 10 s**. Con 4 s, dos de cada tres mediciones que
  pasan el filtro de validez están mal por más de 1 ms, y el filtro no lo dice.*
- Las ráfagas tonales **se rompen con ruido de conversación de fondo**; el ruido de banda
  ancha aguanta ruido de sala tan fuerte como la señal.

**La receta:** 10 segundos en **5 ventanas de 2**. Los 8 segundos extra no compran
exactitud: compran una **mediana** (descarta una ventana arruinada) y una **dispersión**,
que es la única forma de saber si la calibración sirvió sin conocer la respuesta correcta.

**Es autónoma:** no hay que escribir ningún número ni medir distancias con cinta. El retardo
medido ya incluye el vuelo por el aire, y el nivel sale de la misma grabación por mínimos
cuadrados. Las coordenadas quedan opcionales, solo para DBAP.

**El lazo cerrado (2026-09-29), en `host/src/aurasync/sincronia.py`:** recibe una
calibración, decide si vale y la escribe **sin cortar el sonido**. Cinco filtros, cada uno
contra un modo de falla del propio estimador: estabilidad, zona muerta (0,5 ms, por encima
del MAD de 0,12 a 0,35 ms que midió E6), salto máximo, confirmación y ganancia de lazo de
0,5. El `Controlador` no mide ni reproduce, así que se prueba entero sin parlantes: 23 tests.
Escribir un retardo nuevo sin que se oiga es la otra mitad, y está en
`host/src/aurasync/dsp/retardo.py` (retardo fraccionario con rampa de velocidad limitada,
0,05 % de cambio de tono, 12 tests).

**Un error encontrado y arreglado en el camino (2026-09-29):** `gcc_phat` buscaba el pico
solo entre retardos no negativos, pero la medición fina corre contra referencias ya corridas
por el desfase grueso, que es la mediana: el parlante que llega antes que la mediana tiene
residuo negativo. Con tres parlantes a 0, 3,4 y 7,1 ms el error era de **7,10 ms informando
estabilidad de 0,01 ms**, o sea pasando el filtro de validez. Está en
[experimentos/08](research/experimentos/08-lazo-de-recalibracion-en-simulacion.md) §1.

**Conectado a `aurasync run --recalibrar` el 2026-09-29.** Lo que faltaba era la captura
continua del micrófono (`sonido.MicrofonoContinuo`, un anillo en memoria) y el buffer de lo
emitido (`sincronia.VentanaDeEmision`). La medición corre en un hilo aparte
(`sincronia.MedicionEnSegundoPlano`) porque cuesta **1,00 s de CPU** y en el hilo de audio
eso vaciaría el buffer de los parlantes: un underrun de A2DP los resincroniza con otro
desfase, o sea que medir habría destruido lo que se quería medir. La aritmética de las dos
ventanas está probada de punta a punta con un micrófono simulado, y tolera hasta **1 s** de
latencia de reproducción (a 900 ms alinea, a 1600 ms no).

**Probado con parlantes el 2026-09-29, y el resultado es mixto**
([experimentos/09](research/experimentos/09-primera-escucha-con-3-go-4.md) §5). En 644 s con
música: 2 ajustes aplicados, 20 mediciones descartadas y 5 que no alinearon.

- **Los filtros funcionan.** Las propuestas para el parlante de ambiente fueron +6,7 · +11,3 ·
  +6,6 · +4,0 · +4,8 ms, todas distintas, y la confirmación las rechazó a todas. Sin ese
  filtro se habrían escrito hasta **11 ms** de corrección equivocada.
- **Pero el lazo no converge.** Los dos ajustes que se aplicaron fueron en la misma dirección
  y las propuestas siguientes **crecieron**, cuando con ganancia 0,5 tendrían que encogerse.
  Después el filtro de estabilidad rechazó todo lo que quedaba de sesión.
- **Y el patrón señala al parlante de `ambiente` alto**, que es el más decorrelacionado y el
  que menos se parece a un frente directo. O sea: **el parlante que más aporta al
  envolvimiento es el que peor se mide.** La idea de que *la condición del efecto es la
  condición de la medición* se rompe cuando el ambiente sube, y eso hay que entenderlo antes
  de subir `ambiente`, que es lo que la escucha pide.

**Y algo que esta entrada afirmaba y quedó refutado:** la variación entre arranques **existe y
es grande**. Tres `calibrate` seguidos sin tocar nada dieron **15 ms** de diferencia, con
estabilidad informada de 0,00 ms en las tres
([experimentos/09](research/experimentos/09-primera-escucha-con-3-go-4.md) §6). No era el
error de `gcc_phat`. Consecuencia: **la calibración de `calibrate` muere con su stream** y
guardarla para la sesión siguiente no sirve; `calibrate` queda como diagnóstico.

**Qué falta:** entender por qué no converge en el canal de ambiente, y repetir la escucha a
nivel normal. Con eso se revisa `ESTABILIDAD_MAXIMA_MS`, hoy fijado en simulación.

### E9: el par estéreo de JBL como relé · i-7c8794-3e42af
**Estado:** Planificado. **Se puede hacer ya**, con lo que hay. Idea del usuario
(2026-09-28).

**Qué es:** poner dos Go 4 en par estéreo de JBL, conectar el **primario** al PC por
A2DP, mandarle un tono distinto por canal y medir con el micrófono: (a) qué parlante
reproduce qué canal, y (b) el desfase **dentro** del par.

**Las dos razones para hacerlo, y la segunda vale más que la primera:**

1. **Multiplicar parlantes por encima del techo de 3 streams.** El Auracast de JBL es un
   **relé**: entra A2DP clásico a un parlante y sale por BIS a los demás
   ([01](research/01-parlantes-jbl.md) §2). Medido de rebote en esta sesión: con Blue y
   Red en par estéreo, **el Red no llega a tener tarjeta en PipeWire**, o sea que el host
   ve **un solo dispositivo**. Si el par reparte L/R, **1 stream da 2 canales**. Con el
   techo de 3 streams eso sería hasta 5 parlantes (par de Go 4 + par de Charge 6 + un Go 4
   suelto), porque JBL solo hace estéreo **entre dos parlantes del mismo modelo**.
2. **Evidencia indirecta de E4, que es la pregunta que decide el proyecto.** Si el par
   estéreo funciona relevando Auracast con L y R separados, entonces **un Go 4 sí puede
   reproducir solo el canal que le corresponde** de una transmisión. E4 pregunta
   exactamente eso. **Y esto se puede medir sin las SuperMini**, acústicamente, porque no
   hace falta leer la BASE: basta oír qué sale de cada parlante.

**Con qué choca, y es serio:** **What Hi-Fi lista como defecto del Go 4 "Poor sound
synchronisation of Auracast in stereo mode"** y un retardo notable
([01](research/01-parlantes-jbl.md) §"El estéreo sincroniza mal"). O sea que lo que se
delegaría a JBL —la sincronización— es justamente su punto débil reportado. Y esta sesión
midió que **dos Go 4 manejados de forma independiente desde el PC se alinean a 0,1–0,35 ms**
([experimentos/05](research/experimentos/05-e6-a2dp-un-canal-por-parlante.md)). **Es
bastante probable que el camino propio ya le gane al par estéreo de JBL en sincronía**, y
esta medición lo resolvería con un número. INFERIDO hasta medirlo.

**Qué la favorece:** el instrumento de medición ya existe y está validado
(`probes/e6-a2dp/`), y los parlantes están a mano. Es una medición de ~20 minutos.

**Qué se pierde si se adopta:**
- el control de retardo y nivel **por parlante** dentro del par: solo queda el
  preprocesado de L y R, que igual mapea a un parlante cada uno;
- saber **qué parlante físico es L y cuál es R** (lo decide la app de JBL). Se resuelve
  midiendo;
- la independencia de la app de JBL, que es lo que el proyecto quería evitar.

**Dos cosas que el usuario pidió verificar (2026-09-28), y lo que salió:**

1. **"Usar Auracast desactiva el Stereo Group".** **Probablemente cierto, pero no
   confirmado.** La app llama al modo "Stereo Group" dentro de la sección
   *PartyTogether*, y ofrece **elegir** entre Stereo y Party/Auracast. Ninguna fuente dice
   explícitamente que sean excluyentes; la estructura de la app lo sugiere. REPORTADO
   ([Android Authority](https://www.androidauthority.com/how-to-connect-jbl-speakers-together-3301073/);
   las páginas de JBL devolvieron 403).

   **No invalida el plan**, porque el plan no necesita estéreo y party a la vez. Lo que
   necesita es **dos Stereo Groups independientes** (un par de Go 4 y un par de Charge 6),
   cada uno conectado por separado al PC. **Eso es otra pregunta, y es el riesgo real.**
   Según cuál sea la respuesta, el techo cambia:

   | Si la app permite… | Parlantes con 3 streams |
   |---|---|
   | dos grupos estéreo | **5** (par + par + uno suelto) |
   | un solo grupo estéreo | **4** (par + dos sueltos) |
   | ninguno usable desde el PC | 3, como hoy |

2. **"Ese parlante soporta menos códecs".** **Cierto, y es el Go 4: MEDIDO.** El Go 4
   ofrece **solo SBC**; el Charge 6 ofrece **SBC y AAC**
   ([01](research/01-parlantes-jbl.md) §"Códecs A2DP por modelo"). Dentro de un par del
   mismo modelo no molesta, porque el códec es uniforme. **Entre grupos sí:** hay que
   seguir forzando SBC, que ya se hace.

   **Y hay algo más profundo que esto destapa.** En el relé, la cadena es
   **A2DP (SBC) → decodificar → recodificar a LC3 → BIS → decodificar**: dos etapas con
   pérdida, y el Go 4 entra con el techo de SBC. Además **el primario tiene que retrasarse
   a sí mismo** para esperar el camino más largo del secundario. Eso es inherente al relé, y
   es la explicación más probable del defecto que reporta What Hi-Fi. INFERIDO.

**Qué se puede capturar de la fase de sincronización, y qué no.** El probe
[probes/e9-stereo-group/observar.py](../probes/e9-stereo-group/observar.py) registra cada
cambio con marca de tiempo mientras se forma el grupo. **Ve:** qué parlante deja de exponer
A2DP (o sea quién es el secundario), si aparece o desaparece el anuncio de Auracast
(0x1852), los bytes que marcan el modo estéreo, y los cambios en los datos de fabricante.
**No ve el mecanismo:** el AX210 no tiene los bits 13 ni 31, así que **no puede
sincronizarse a los anuncios periódicos ni recibir el BIS** (E1,
[experimentos/03](research/experimentos/03-e1-iso-en-el-ax210.md)). La BASE, los parámetros
del BIG y el reparto de canales por BIS **necesitan las SuperMini**.

**Qué hay que decidir antes:** nada. Pero hace falta que el usuario **forme el par estéreo
desde los parlantes o la app** con el probe corriendo, y que diga qué parlante quedó
primario.

**El resultado va a:** `docs/research/experimentos/`.

### E6: línea base con A2DP y combine-stream · i-7c8794-24ea65
**Estado: A medias (2026-09-28)**, en
[experimentos/05](research/experimentos/05-e6-a2dp-un-canal-por-parlante.md).

**Lo que ya está medido:**
- **El AX210 sostiene los dos parlantes a la vez. No hace falta un segundo dongle**,
  que era la duda anotada más abajo.
- **`combine-stream` reparte un canal a cada parlante** y se oye por el parlante
  correcto. El sink vive en un proceso `pw-cli -m` y no deja nada al morir.
- **El instrumento de medición está escrito y validado**
  (`probes/e6-a2dp/`): 11 casos sintéticos entre −6 y +40 ms, con y sin
  reverberación, error máximo **0,04 ms**. El micrófono es el **fifine USB**, que
  resuelve el "qué micrófono usar" que E5 dejaba pendiente.
- **El desfase, con los 4 parlantes en standalone y todos en SBC.** Lo que manda es
  **cuántos streams suenan a la vez**, no el códec:

  | parlantes | Δt entre ellos | MAD | variación entre reproducciones |
  |---|---|---|---|
  | **2 Go 4** | −0,34 / +0,11 / +0,26 ms | 0,12–0,35 ms | **0,6 ms** |
  | **3 Go 4** | Red −2,7 · Blue +1,8 ms | 0,25–0,68 ms | **0,1 ms** |
  | **4 (3 Go 4 + Charge 6)** | de −8 a +113 ms, salta | 2–30 ms | no converge |

  **Con 2 o 3 parlantes iguales el desfase es de pocos ms y repetible a 0,1 ms**, o sea
  dentro del umbral de 5 ms para estéreo **sin recalibrar en cada arranque**. Con 4 se
  cae, con cualquier códec: el límite es la cantidad de streams A2DP simultáneos.
  Y los ±2–3 ms son compatibles con geometría de la sala (2,7 ms = 92 cm).
- **Códecs distintos cuestan 45–150 ms.** Se arregla con `bluez5.codecs = [ sbc ]` en
  **WirePlumber** (en PipeWire no tiene efecto; se probó).
- **El protocolo no aporta nada:** no hay ni un `AVDTP Delay Report` en la traza, y los
  nodos informan latencia 0. **La calibración con micrófono es el único mecanismo**, no
  una mejora opcional.
- **Con los 4 parlantes más el Tune conectado, un transporte A2DP no se pudo levantar**
  (`Acquire … returned error: org.bluez.Error.Failed`).

**Dos factores nuevos que este plan no había previsto:**
1. **Forzar el mismo códec en caliente falla** (`endpoint /MediaEndpoint/A2DPSource/sbc
   in use`), aunque varios dispositivos **sí** comparten un códec si lo negocian al
   conectarse (medido: los 3 Go 4 los tres en SBC). Hay que limitarlo por configuración.
2. **El par estéreo de JBL bloquea el camino A2DP igual que el Auracast.** Con los Go 4
   Blue y Red en par estéreo, el Red **no llega a tener tarjeta en PipeWire** aunque
   BlueZ lo reporte conectado. Se deshace desde los parlantes.

**La corrida de 30 minutos se hizo y NO fue concluyente** (2026-09-28). Salieron 180
ráfagas, pero con **25 ms de dispersión** contra los 0,25–0,68 ms de las corridas cortas. Y
hay una prueba de que no es física: los valores saltan ±50 ms en 10 s, que serían 5000 ppm
de error de reloj. **La culpa fue del estímulo:** una ráfaga tonal tiene su información de
tiempo solo en la envolvente (~1,25 ms de resolución), y la cama de ruido que hacía falta
para que el stream no se suspendiera la degradó.

**Qué falta:** repetir el drift con **ruido decorrelado de banda ancha**, que es unas 170
veces más preciso y además es continuo por sí mismo, así que no necesita cama de ruido
aparte ([experimentos/06](research/experimentos/06-calibracion-rapida-y-recalibracion.md)).

**Techo medido del camino A2DP: tres parlantes.** Las tres formas de pasar de ahí, en
orden de costo ([09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §8):
1. **El Charge 6 por USB-C** (E7, i-7c8794-b30648): **no usa ancho de banda Bluetooth**,
   así que da un cuarto canal dentro del techo medido. Es lo más barato y conviene
   probarlo primero. Si se compra un segundo Charge 6, uno va por cable y otro por radio.
2. **Un segundo adaptador USB** (~US$10): reparte 2+2. **INFERIDO que mantiene la
   alineación**, porque el ritmo lo marca el grafo de PipeWire y no el adaptador, pero hay
   que medirlo con el instrumento que ya existe.
3. **Auracast con las SuperMini**, que lo resuelve de raíz pero depende de E4.

**Bajar el bitrate de SBC no es una opción:** WirePlumber no expone control de bitpool
(VERIFICADO en su documentación, [09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §8).

**Qué es:** 2 Go 4 por A2DP clásico, con FL y FR usando
`libpipewire-module-combine-stream`. Se miden el desfase con un micrófono, la
variación entre inicios de reproducción y lo que reporta `pw-dump`, para saber si
llega un delay report o si PipeWire usa los 125 ms fijos.

**Con qué choca:** ~~probablemente se necesite un segundo dongle (RTL8761B) si el
chip integrado no aguanta dos streams~~ — **descartado por medición (2026-09-28):** el
AX210 aguanta los dos.

**Qué la favorece:** no requiere hardware LE Audio, y HyperBoom ya lo hizo.

**Qué hay que decidir antes:** nada. Es el punto de comparación que hace falta
para decidir y también el plan B.

### E7: entrada de audio USB-C del Charge 6 · i-7c8794-b30648
**Estado:** Planificado. Puede hacerse en paralelo.

**Qué es:** conectar el Charge 6 por USB-C y ver si aparece como tarjeta de sonido
en Linux. Si aparece, medir su latencia. Podría ser un canal cableado (centro o
subwoofer).

**Con qué choca:** hay que alinear un canal cableado con canales Auracast, y la
diferencia de latencia es de unos 40 a 80 ms (INFERIDO). Se resuelve con un
retardo fijo.

**Qué la favorece:** la ficha técnica de JBL lo declara ([01](research/01-parlantes-jbl.md) §1).

**Qué hay que decidir antes:** nada.

### P1: captura del audio del sistema sin huella en Mac y Linux · i-7c8794-fd5f03
**Estado: la mitad de Linux, hecha (2026-09-28).** Está en
`host/src/aurasync/sonido.SinkVirtual` y se usa con `aurasync run`.

**Qué quedó comprobado**, con `pw-record -P '{ media.class=Audio/Sink … }'`:
- **el nodo aparece como una salida más del sistema**, con el nombre y la descripción que
  se le den, y cualquier aplicación puede rutearse ahí sin instalar ni configurar nada;
- **al terminar el proceso desaparece**: `pactl list sinks` no lo muestra más y la
  configuración del sistema queda como estaba. Es el criterio de *no dejar huella*.

**Un detalle que salió de otra medición:** cuando no hay nada reproduciéndose, el nodo se
suspende y no emite datos. Por eso `aurasync run` **le manda silencio a los parlantes** en
ese caso: si sus streams A2DP se suspendieran, al volver traerían un desfase distinto del
que acaba de medir la calibración, que es lo que se vio en
[experimentos/05](research/experimentos/05-e6-a2dp-un-canal-por-parlante.md).

**El criterio de terminado, cumplido (2026-09-29)**, en
[experimentos/07](research/experimentos/07-p1-la-captura-no-deja-huella.md): tras un
`kill -9` en plena captura, el dispositivo desaparece solo, la salida por defecto no se
mueve y los 5 archivos de estado de WirePlumber quedan **byte a byte iguales**.

**Qué falta de P1:** la mitad del Mac (el process tap, que no se puede hacer desde este
equipo), y repetir la comprobación **con audio fluyendo**: en la que se hizo, el nodo estuvo
suspendido porque no había ninguna aplicación ruteada, y con un stream activo WirePlumber
tiene más razones para tocar su estado.

**Qué es:** comprobar que la captura del audio del sistema funciona sin instalar
drivers y sin dejar nada al terminar
([08](research/08-integracion-y-plan.md) §2):
- **en el Mac:** un process tap privado (PyObjC o audiotee) que entregue PCM
  estéreo continuo, y ver si `CATapMuted` silencia los parlantes;
- **en Linux:**
  - `pw-record -P '{ media.class=Audio/Sink … }' --raw -` como sink virtual que
    desaparece al matar el proceso;
  - la salida por defecto sin persistir (`node.restore-default-targets false`
    sin `--save`).

**Con qué choca:** con nada. Es solo lectura, salvo el setting de WirePlumber,
que es solo en tiempo de ejecución.

**Cómo se revierte:** el nodo y el tap mueren con el proceso. Si el setting de
WirePlumber queda en false tras una caída, se revierte con `wpctl settings
node.restore-default-targets true`, o reiniciando WirePlumber (`systemctl --user
restart wireplumber`).

**Criterio de terminado:** tras un `kill -9` en plena captura,
`~/.local/state/wireplumber/` y la salida por defecto quedan byte a byte como
estaban (tarjeta *cleanup-belongs-to-the-supervisor*).

**El resultado va a:** `docs/research/experimentos/`.

### P2: drift entre el reloj del PC y el del controlador · i-7c8794-cb208f
**Estado:** Planificado. Depende de E1.

**Qué es:** medir cuántos ppm hay entre el reloj del PC y el del controlador
(una SuperMini creando un BIG), leyendo el nivel de la cola de `IsoPacketStream`
y `HCI_LE_Read_ISO_TX_Sync` si el `hci_uart` lo implementa. Después, probar un
lazo DLL con `samplerate` de razón variable durante 1 hora
([08](research/08-integracion-y-plan.md) §4). El firmware candidato para la
SuperMini es `bluekitchen/hci_uart_iso_timesync`, que agrega el comando `LE Read
ISO Clock` para leer el reloj ISO del controlador.

**Con qué choca:** con nada.

**Qué decide:** si el remuestreo en Python alcanza, o si el backend de Linux tiene
que ser un driver del grafo de PipeWire en Rust o C.

**Criterio de terminado:** el drift medido en ppm, con la unidad de SuperMini y la
fuente de 32 kHz anotadas, y el lazo sin cortes ni crecimiento de la latencia
durante 1 hora.

### P3: Raspberry Pi como tarjeta de sonido USB de 4 canales (gadget UAC2) · i-7c8794-346d45
**Estado:** Planificado. **Se puede hacer ya con la Pico 2 W**, sin comprar nada
(2026-09-26).

**Qué es:** probar el lado USB del emisor dedicado
([08](research/08-integracion-y-plan.md) §3.1), en dos pasos:
- **(a) benchmark de liblc3 en el Cortex-M33 del RP2350:** 4 canales, 48 kHz,
  tramas de 10 ms y 100–120 B, contando ciclos con DWT CYCCNT. Dura horas y
  decide si la Pico sirve;
- **(b) speaker TinyUSB de 4 canales con feedback asíncrono** (TinyUSB master;
  UAC1 en full-speed y UAC2 como alternativa): ver si enumera sin driver en
  macOS, Linux y **Windows 11**, y registrar el nivel de FIFO durante horas.

Si (a) muestra que no caben 4 codificadores, **la alternativa es una Pi Zero 2 W
(~US$15, hay que preguntar antes de comprarla)** con un gadget `f_uac2`
(`c_chmask=0x33`, `c_sync=async`, `Capture Pitch 1000000`).

**Con qué choca:** con nada en el PC, que no se modifica. El título dice
"Raspberry Pi" porque el id se generó antes de saber de la Pico.

**Qué la favorece:**
- el port oficial `rp2040-vela-if820` de BTstack ya junta un controlador externo
  por UART y una tarjeta de sonido TinyUSB en un RP2040;
- CamillaDSP usa el gadget `f_uac2` con 2 y 8 canales.

**El resultado va a:** `docs/research/experimentos/`, y decide si la Fase 3 es
viable.

### Estructura base del repositorio · i-7c8794-f7f5b2
**Estado:** Hecho (2026-09-26).

**En qué quedó:**
- `host/`: el paquete `aurasync`, con hatch y Python 3.12. Trae una CLI que solo
  responde `--version` y 9 tests de humo: CLI, API de Bumble, LC3 con 1 y 4
  canales, y el retardo de 2,5 ms.
- `firmware/supermini/` y `firmware/pico/`, con su plan y sin código.
- `scripts/check.sh` corre, además:
  - `hatch fmt --check` y `hatch test`;
  - una lista cerrada de los archivos permitidos en `host/src/aurasync/` hasta la
    decisión de seguir.
- La estructura prevista está en [08](research/08-integracion-y-plan.md) §6.1.

**Decisiones:** d-7c8794-f619c4 (la enmienda a d-7c8794-346170), d-7c8794-c23c20
(el stack), d-7c8794-5c014a (el monorepo), d-7c8794-92aa04 (el nombre).

**Qué falta:** probarlo en el equipo Linux. Hay que instalar hatch en el home y
correr `scripts/check.sh` con `PY` apuntando a un Python ≥3.11.

### Decisión de seguir o no · i-7c8794-0d129c
**Estado:** Planificado. Depende de E4, y además de E5 y E6 si se sigue.

**Qué es:** registrar en `docs/decisions.md` el camino elegido, con los números
medidos:
- **seguir con el camino A** si E4 muestra que cada JBL reproduce el BIS asignado
  y E5 muestra un desfase dentro de lo tolerable;
- **pasar al camino alternativo** si E4 falla pero E6 da un desfase aceptable para
  estéreo o para una fiesta;
- **no seguir con el surround** si fallan los dos.

**Con qué choca:** con d-7c8794-346170. Hasta que esta decisión exista, no hay
código de producto; solo la estructura base que permite d-7c8794-f619c4.

**Cuando se registre:** hay que sacar de `scripts/check.sh` la lista cerrada de
archivos de `host/src/aurasync/`.

**Qué hay que decidir antes:** el umbral de desfase tolerable (ver E5).

## Fase 2: prototipo por el camino A (solo si la decisión es seguir)

La arquitectura prevista, **sujeta a lo que muestren E3 y E4**:

```
reproductor ──► sink virtual de PipeWire "jbl-multicanal" (estéreo o quad)
                   │ upmix a quad si la entrada es estéreo (método por elegir, 07 §4)
                   ▼
            codificador LC3 por canal
                   ▼
            emisor: 1 BIG, 1 BIS por canal, datos de fabricante de Harman
                   │       (PipeWire bcast_source, o Bumble modificado)
                   ▼
   Go 4 (FL)   Go 4 (FR)   Go 4 (RL)   Charge 6 (RR, C o LFE)
        ▲ asistente BASS: le dice a cada parlante qué BIS reproducir
        └ configuración: dirección del parlante → canal
```

### Herramienta CLI del MVP: un núcleo con backends de captura y de emisor intercambiables · i-7c8794-2fe665
**Estado: A medias (2026-09-28). Desbloqueada** por d-7c8794-9afee2, y con el núcleo ya
construido: **92 tests**, lint y formato en verde.

**Lo que existe y funciona:**

| Módulo | Qué hace |
|---|---|
| `config.py` | la instalación, por coordenadas **opcionales** y no por etiquetas de canal |
| `dsp/decorrelate.py` | todo-paso de fase aleatoria (Potard y Burnett) |
| `dsp/ambience.py` | extracción de ambiente por coherencia, con versión **para flujos** |
| `motor.py` | la cadena completa: estéreo → una señal por parlante |
| `estimulos.py`, `medicion.py` | la calibración autónoma con micrófono |
| `sonido.py` | la capa de PipeWire: descubrir, reproducir a N parlantes, grabar |
| `cli.py` | `doctor`, `sinks`, `init`, `calibrate`, `play` |

**Tres decisiones de diseño que vale la pena no reabrir sin motivo:**

1. **Un proceso `pw-play` por parlante**, y no un sink combinado. Con `combine-stream` el
   reparto lo decide PipeWire y el retardo y la ganancia por parlante quedan fuera de
   nuestro alcance; los probes lo usaron, el producto no.
2. **La corrección de sincronía es una etapa enchufable** (`retardo_ms` y `ganancia_db` por
   parlante). Según lo que resulte el drift, el mismo código se escribe una vez desde la
   calibración o lo actualiza un lazo. El resultado de la medición cambia un parámetro, no
   la arquitectura.
3. **La extracción de ambiente tiene latencia propia** (43 ms) y el motor **retrasa el
   camino directo lo mismo**. Sin eso el ambiente llegaría adelantado y el efecto de
   precedencia haría lo contrario de lo buscado.

**Qué falta:** el `play` todavía lee de un archivo WAV y no captura el audio del sistema
(eso es P1); no hay modo de ajuste en vivo (i-7c8794-bdb678); y no hay lazo de
recalibración continua, que depende de medir el drift.

**Qué es:** una sola herramienta en Python sobre Bumble (nombre provisional
`aurasync`) con subcomandos `doctor`, `scan`, `tone`, `play`, `calibrate` y
`assign`.
- Tiene un núcleo común: remuestreador, DSP, LC3 y BIG.
- Tiene backends de captura por sistema (PipeWire, Core Audio tap, WASAPI,
  archivo y, en la Fase 3, gadget UAC2).
- Tiene un backend emisor: Bumble por `serial:`, o combine-stream si se toma el
  camino A2DP.
- El diseño está en [08](research/08-integracion-y-plan.md) §6.

**Hitos**, cada uno con un criterio MEDIDO ([08](research/08-integracion-y-plan.md) §7.2):
- **M0:** `doctor` y `scan`, que solo leen.
- **M1:** el emisor con 4 BIS (cierra el prototipo del emisor multicanal).
- **M2:** la captura del sistema con el lazo de drift (usa P1 y P2).
- **M3:** upmix y distribuciones (cierra el upmix).
- **M4:** calibración (cierra la calibración).
- **M5:** BASS y el modo receptor automático (cierra el asistente BASS).

**Con qué choca:** con d-7c8794-346170 hasta la decisión de seguir. Con la
invariante central solo si el backend emisor rompe el BIG único, y no lo hace.

**Qué la favorece:** Bumble ya expone como biblioteca todo lo que hace falta
para el emisor (VERIFICADO). Un emisor propio evita el parche de `auracast.py`.

**Qué hay que decidir antes:** el sistema operativo de referencia. La
recomendación está en [08](research/08-integracion-y-plan.md) §8. El stack
(d-7c8794-c23c20), el nombre (`aurasync`, d-7c8794-92aa04) y la estructura
(d-7c8794-5c014a, 08 §6.1) ya están decididos.

### Prototipo del emisor multicanal · i-7c8794-5f25b0
**Estado:** Planificado. Bloqueado por la decisión de seguir.

**Qué es:** el emisor de un BIG con N BIS, cada uno con su Audio Location,
alimentado desde un sink virtual de PipeWire.

**Con qué choca:** con la invariante central, porque tiene que ser **un solo BIG**
(d-7c8794-203de2). Con Bumble, las fuentes mono quedan fijas en FRONT_LEFT y el
retardo en 40 ms, así que haría falta un parche o un fork. En una Raspberry Pi hay
que compilar liblc3.

**Qué la favorece:** PipeWire ya crea un nodo por BIS. Bumble ya transmite L/R.

**Qué hay que decidir antes:** Bumble o PipeWire (lo resuelve E3); el lenguaje del
proyecto; y si el emisor es el PC o un nRF5340 independiente.

### Asistente BASS que asigna canales · i-7c8794-365524
**Estado:** Planificado. Bloqueado por E4(b).

**Qué es:** un componente que se conecta a cada JBL y le escribe `BIS_Sync` según
la configuración (qué parlante va a qué canal). Solo hace falta si E4 muestra que
los JBL no eligen su BIS por sí mismos.

**Con qué choca:** hay que mantener una conexión LE con cada parlante mientras se
transmite, y eso compite por tiempo de radio con el BIG.

**Qué la favorece:** BlueZ 5.84 y 5.87 mejoraron BASS, y Bumble implementa BASS.

**Qué hay que decidir antes:** si los JBL exponen BASS (lo resuelve E4).

### Decorrelación por parlante, para producir envolvimiento · i-7c8794-250043
**Estado: Hecho (2026-09-28)**, en `host/src/aurasync/dsp/decorrelate.py`, con 11 tests que
comprueban las tres propiedades que importan: magnitud plana (no colorea), mismo espectro a
la salida (suena igual) y salidas decorrelacionadas. Se expone en la CLI con
`aurasync play --sin-decorrelar`, que es el A/B para mostrar el efecto.

**De dónde salió:** de la investigación del 2026-09-28
([09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md)), pedida por el
usuario para entender el efecto que busca.

**Qué es:** mandar a cada parlante una versión del mismo material con forma de onda
distinta pero que suena igual, con filtros todo-paso de fase aleatoria distintos por
canal.

**Por qué es lo más importante del MVP, y no el cuarto canal:** lo que produce la
sensación de estar rodeado (**listener envelopment**) es la energía lateral tardía
**decorrelacionada**, no la cantidad de canales. Y además la decorrelación **debilita el
efecto de precedencia**, o sea que hace al sistema **menos sensible al desfase**, que es
la debilidad del camino A2DP. Las dos cosas están REPORTADAS en
[09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §1 y §2.

**Con qué choca:** con nada. Ocurre antes del emisor, igual que el upmix.

**Qué hay que decidir antes:** nada. Conviene que se pueda **prender y apagar en vivo**,
porque es el A/B que muestra el efecto.

### Modelo de posiciones en coordenadas y panning DBAP · i-7c8794-26c302
**Estado: A medias (2026-09-28).** El modelo de datos ya está: `config.Parlante` lleva
coordenadas, y son **opcionales**, porque la calibración con micrófono mide el retardo
total —que ya incluye el vuelo por el aire— y no las necesita. **Falta el panning DBAP en
sí**: hoy el reparto se hace con un `pan` simple por parlante.

**Qué es:** describir la instalación como **coordenadas de cada parlante en la pieza**, en
vez de etiquetas de canal (FL/FR/RL/RR), y calcular las ganancias con **DBAP**
(Distance-Based Amplitude Panning).

**Por qué:** VBAP y ambisonics suponen un oyente en el *sweet spot*. DBAP se diseñó sin
suposiciones sobre las posiciones de los parlantes ni sobre dónde está el oyente
(VERIFICADO en el paper de Lossius,
[09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §4). El caso de uso
—parlantes en los bordes, el oyente caminando— es literalmente el que DBAP resuelve.

**Beneficio extra:** con las coordenadas, el retardo acústico por distancia se calcula en
vez de medirse, y la calibración con micrófono solo tiene que corregir lo electrónico.

**Con qué choca:** con la configuración por distribución de canales que asumía
[07](research/07-software-de-audio-en-el-pc.md) §4.5 (quad, 3/1). Las distribuciones
quedan como presets sobre el modelo de coordenadas, no como el modelo en sí.

### Lo que el usuario pidió el 2026-09-29, y cómo encaja

**1. Ubicar los parlantes en una cuadrícula, de forma aproximada, relativa a la persona o a
la pieza.** Encaja directo: es una forma de **entrar** las coordenadas que ya existen, sin
cinta métrica. Y "aproximada" es suficiente: 34 cm son 1 ms, así que una cuadrícula de medio
metro da el retardo acústico con medio milisegundo de error, que está por debajo de la zona
muerta del lazo (0,5 ms). La decisión de diseño a tomar: **el origen es la persona o la
pieza**. Conviene la pieza, con la persona como un punto más que se puede mover, porque el
objetivo es que funcione mientras el oyente camina: si el origen es la persona, moverla
obliga a reescribir todas las coordenadas.

**2. Modo surround con LF, RF, RR, LR y config por parlante.** Encaja **como preset**, que es
lo que este documento ya decía. Pero hay que decir en voz alta la tensión: `config.py` está
escrito argumentando **contra** las etiquetas de canal, porque suponen un *sweet spot*, y el
proyecto persigue lo contrario. La reconciliación honesta es que el preset LF/RF/RR/LR **fija
coordenadas, `pan` y `ambiente`** de una vez, como punto de partida cómodo y reconocible, y
después se ajusta. No reemplaza el modelo.

**Y un choque medido que hay que mirar antes de diseñar esto:** LF/RF/RR/LR son **cuatro**
parlantes, y E6 midió que **con cuatro streams A2DP el enlace se desestabiliza** (dispersión
de 2 a 30 ms, saltos dentro de una misma reproducción,
[experimentos/05](research/experimentos/05-e6-a2dp-un-canal-por-parlante.md)). Con el
hardware de hoy, un surround de cuatro por A2DP **no es alcanzable**. Las salidas posibles:
tres parlantes en una disposición triangular (que para envolvimiento puede alcanzar de sobra),
un segundo adaptador, o esperar a Auracast. Conviene que el modelo de datos soporte cuatro
desde el principio y que la herramienta **avise** en vez de dejar probar algo que se sabe
inestable — hoy `doctor` ya lo avisa.

### Modo difusión en vivo: ajuste interactivo de niveles y retardos · i-7c8794-bdb678
**Estado (2026-10-01): construido, falta validarlo con parlantes.** El usuario aprobó la
spec, con dos secciones nuevas: el panel de `panel-demo` será un cliente de `control.py`
(§13), y la v1 no tiene notificaciones, así que un cliente pregunta el estado cada tanto
(§14). Está construida la primera entrega completa: `aurasync service`, `control.py`,
`rest.py`, `session.py` (el lazo de `run`, movido sin reescribirlo), `presets.py` y
`dsp/ramps.py` conectado al motor. El micrófono ya no está fijo en el código. Hay 325 tests sin
hardware, y la API está documentada en `host/docs/control-api.md`. **MEDIDO:** el motor corre a 63
veces el tiempo real en `PC-Ryzen5`
([experimentos/10](research/experimentos/10-servicio-de-control-con-3-go-4.md) §1).

**Lo próximo:** el protocolo con los 3 Go 4 de
[experimentos/10](research/experimentos/10-servicio-de-control-con-3-go-4.md) §4, ahora con el
panel (i-7c8794-f3ddf8).

**Diseño cerrado (2026-09-29, tarde).**
La spec está en
[superpowers/specs/2026-09-29-control-service-design.md](superpowers/specs/2026-09-29-control-service-design.md)
(en inglés, d-7c8794-7b3093), y la decisión en d-7c8794-74b639. Lo que cambió respecto de
lo que sigue abajo, a pedido del usuario:
- **no es un servidor dentro de `run` sino un programa persistente** (`aurasync service`),
  que se inicia y se apaga a mano y sigue vivo aunque falle una sesión de audio. El audio
  existe solo entre `start` y `stop`;
- **el control es un contrato JSON independiente del transporte** (`control.py`). REST es
  el primer transporte; **serie** será el segundo (i-7c8794-a9f161), pensando en la Fase 3;
- **la interfaz se decide después**: esta entrega es solo la API. El A/B ciego y la
  interfaz son la segunda entrega (i-7c8794-f3ddf8);
- en la red local con **token**, guardado en `service.json` (`0600`); los ajustes viven en
  memoria y se escriben solo con `save` o `preset_save`;
- **dos hallazgos del código que el diseño resuelve:** `pan` y `ambiente` no tienen rampa
  hoy (moverlos en vivo sería un salto), y apagar el extractor o el decorrelador **corre la
  señal en el tiempo** (43 ms y ~1–2 ms). El extractor sigue corriendo con un factor de
  mezcla; el decorrelador y los presets cambian con un fundido a silencio de 80 + 80 ms;
- de paso resuelve el **micrófono fijado en el código** que encontró el inventario de
  `HP-O16`: `--microfono` → `service.json` → el default de PipeWire.

**Estado anterior (2026-09-29, mañana): era lo próximo, y ya no una comodidad.** La primera escucha con
parlantes lo dejó claro: el efecto *"se siente algo pero no tanto como esperaba"*, y sin poder
mover los parámetros mientras suena, cada prueba cuesta un reinicio y la comparación queda en
la memoria del oyente, que para diferencias sutiles no sirve
([experimentos/09](research/experimentos/09-primera-escucha-con-3-go-4.md) §1).

**La mitad difícil ya está construida.** `motor.actualizar()` relee la instalación sin cortar
el sonido, y `dsp/retardo.py` mueve los retardos con rampa para que el cambio no se oiga. Lo
que falta es **exponerlo**: hoy los parámetros se leen del JSON al arrancar y nada los vuelve
a leer.

**La forma que pidió el usuario (2026-09-29):** una **API de Python con una web de interfaz**,
en vez de teclas en la terminal. Lo importante de esa forma es la separación: **la API es la
costura y la web es un cliente**. Eso permite que la misma API la usen después la interfaz, un
script de pruebas A/B y el propio lazo, y que probar una idea no obligue a tocar la web. Y hace
la pregunta de portabilidad (i-7c8794-a848a0) más barata: la API no toca audio, así que cruza a
macOS sin cambios. Razones suyas: poder ajustar desde el
teléfono mientras camina por la pieza —que es justo donde hay que juzgar el efecto— y poder
mostrárselo a otra persona. Consecuencias de diseño a decidir antes de escribir:
- **qué expone:** `ambiente`, `pan` y `ganancia_db` por parlante, `retardo_traseros_ms`
  global, y los interruptores de decorrelación y de extracción. El `retardo_ms` por parlante
  lo escribe el lazo, así que la interfaz lo **muestra** pero no lo edita;
- **cómo llega al motor:** el proceso de `run` es el que tiene el motor, así que el servidor
  vive dentro de ese proceso o le habla por un socket. Lo primero es más simple y no agrega
  estado que sobreviva al proceso, que es la propiedad de P1 que conviene no perder;
- **sin dependencias nuevas si se puede:** `http.server` de la biblioteca estándar alcanza
  para servir una página y aceptar cambios; el proyecto evita dependencias nativas a propósito
  (`host/pyproject.toml`);
- **presets**, para poder comparar A/B de verdad: guardar dos configuraciones y alternarlas,
  que es lo que la memoria auditiva no puede hacer sola.

**Qué es:** un modo donde se ajustan nivel, retardo y decorrelación de cada parlante
**mientras suena**, y no editando un archivo.

**De dónde salió:** de la práctica de los *loudspeaker orchestras* (Acousmonium del GRM,
BEAST), donde la difusión de una obra estéreo entre muchos parlantes **es una
performance**, ajustada en vivo
([09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §6). También de que el
usuario quiere **calibrar de oído** en su pieza y después mostrarlo a otras personas.

**Con qué choca:** con nada del protocolo. Pide que el núcleo tenga los parámetros
cambiables en caliente, lo que conviene saber antes de diseñarlo.

**Qué la favorece:** la misma tradición dice algo útil para este proyecto: en el
Acousmonium los parlantes son **de timbres y tamaños distintos a propósito**. O sea que el
Charge 6 al lado de tres Go 4 no es un defecto a igualar, sino una voz distinta.

### A/B ciego e interfaz para el servicio de control · i-7c8794-f3ddf8
**Estado: construido, sin validar con parlantes (2026-10-01).** El panel completo de la rama
`panel-demo` quedó sobre el motor real como interfaz del servicio (d-7c8794-09d10f, spec
§15). Incluye presets, el A/B ciego y `aurasync service --simular`. Hay 38 tests de
navegador en Chromium y Firefox. Construirlo destapó dos errores en `medicion.niveles`, ya
corregidos ([experimentos/10](research/experimentos/10-servicio-de-control-con-3-go-4.md) §3.1).
**Lo próximo:** los pasos 8 a 10 de ese protocolo, con parlantes.

**Actualización (2026-10-01, tarde): probado con los 3 Go 4 y música.** La campaña contra el
servicio vivo destapó y corrigió cinco errores: aplicar con la música en pausa, la
calibración que no medía el residuo, ~1 s de latencia por la tubería, la alineación con 20 ms
de consenso, y WirePlumber moviendo un stream. Además encontró que **los saltos de 42,67 ms
eran de reloj** (un driver por parlante) y los eliminó con la salida combinada
(d-7c8794-a41ec9). **La calibración solo por protocolo no es posible con estos parlantes**
([experimentos/10](research/experimentos/10-servicio-de-control-con-3-go-4.md) §5). **Lo
próximo:** el lazo contra la música con un retraso inyectado (la fase E de `campana.py`),
y la curva de volumen AVRCP del Go 4.

**Estado anterior:** Planificado. Segunda entrega del servicio de control; depende de la
primera (i-7c8794-bdb678).

**Qué es:** dos cosas sobre la API que deja la primera entrega:
- **A/B ciego:** el programa sortea cuál de dos presets es X, la persona elige cuál prefiere
  sin saberlo, y al final se muestra cuántas veces eligió cada uno. Queda en el registro de
  la sesión. La primera entrega ya deja lo que esto necesita: `preset_load` hace el fundido
  **aunque no cambie nada**, así la presencia del corte no delata la respuesta;
- **una interfaz** para usarla desde el teléfono. Cuál (web servida por el mismo programa,
  una app, otra cosa) lo decide el usuario después de usar la API.

**Por qué el modo ciego:** el proyecto ya descartó tres criterios que *parecían* bien
(`CLAUDE.md`), y saber qué preset suena sesga lo que se oye. Es la misma regla aplicada a la
escucha.

**Con qué choca:** con nada; es un cliente de la API.

### La música primero: volumen, espectro completo y ecualización que solo realza · i-7c8794-10ccb4
**Estado: A medias (2026-10-01).** Lo pidió el usuario: el envolvimiento es secundario y lo
primero es oír la música bien, fuerte y con todo su espectro. Spec:
`superpowers/specs/2026-10-01-music-first-and-masked-probe-design.md` §1–2. Decisión:
d-7c8794-e8f7e3.

**Lo que ya está construido:**
- la ecualización que solo realza, y lee la medición con optimismo;
- el tipo de parlante, con la banda del fabricante (Go 4 y Charge 6);
- el limitador de pico al final de cada cadena;
- la salida combinada al 100 %, comprobada, y la salida en f32.

Pasan 352 tests unitarios y 50 de navegador.

**Agregado el 2026-10-01 (noche)** (experimentos/10 §8):
- la lectura de banda limitada en el retardo, que le quitaba hasta 3,5 dB a 12,7 kHz;
- la entrada en f32;
- la lectura de la entrada que ya no corre los canales;
- la tarjeta **Entrada** del panel: mono o estéreo, correlación, lateral/central, balance,
  ancho de banda, saturación y el formato de cada aplicación;
- el **micrófono elegible** en la tarjeta de calibración, que se guarda en `service.json`
  y reinicia el lazo con el nuevo, y su nivel en Niveles.

**Lo que falta:**
1. Un preset "música" (ambiente bajo en todos).
2. **La referencia medida:** el mismo pasaje directo a un Go 4 y a través de aurasync,
   grabado en el punto de escucha y comparado por tercio de octava y por volumen. Pasa si
   aurasync no suena más apagado que lo directo: ±2 dB entre 100 Hz y 10 kHz con la
   ecualización apagada.
3. Un A/B ciego de la ecualización con música, de 16 intentos o más.

**Con qué choca:** con el lazo que mide contra la música. Con poco ambiente, los parlantes
llevan contenido más parecido y esa medición empeora (experimentos/09 §5). Lo resuelve la
sonda enmascarada (i-7c8794-e3e40d).

### Recalibración continua como parte del protocolo · i-7c8794-b0512d
**Estado: Hecho en código (2026-10-01); falta validarlo con parlantes.** Decisión:
d-7c8794-f94c13.
- el lazo va encendido por defecto;
- se apaga en vivo desde Ajustes, como opción avanzada;
- calibrar lo pausa y lo retoma;
- aplicar lo reinicia desde los valores aplicados.

**Lo que falta:** que el lazo converja con música, que sigue pendiente desde experimentos/09
§5. Depende de la sonda. **2026-10-02:** una causa de que no convergiera está encontrada y
corregida en simulación. El lazo sumaba lo que medía, porque la referencia va después de la
línea de retardo; ahora lo maneja `arrival_loop.py` (experimentos/11 §"Paso 2"). Falta verlo
con parlantes.

### Sonda enmascarada bajo la música para el lazo de sincronía · i-7c8794-e3e40d
**Estado: A medias (2026-10-02). Los pasos 1 y 2 están hechos en simulación; faltan el 3 y el 4,
con parlantes**
([experimentos/11](research/experimentos/11-sonda-enmascarada-en-simulacion.md), SIMULADO).

**Paso 1:**
- a −20 dB con 4 s, ninguna de 240 mediciones erra por más de 1 ms;
- contra la música, en cambio, erran el 70 a 85 %, porque confunde a Black con Blue.

**Paso 2 (2026-10-02), construido en el producto y apagado por defecto** (experimentos/11
§"Paso 2"):
- **qué se construyó:**
  - `dsp/probe.py`: simultánea e independiente por parlante, no por turnos (exp. 16 §4.2);
  - `probe_measure.py`, el estimador por parlante;
  - `arrival_loop.py`, el lazo por parlante que sigue la deriva de cada uno;
  - en `session.py`, la perilla `set_probe`;
- **el estimador sobre el motor real:** p95 de 0,004–0,011 ms a −20 dB, con 3 y 8 parlantes;
  ninguna medición aceptada erró más de 1 ms;
- **el lazo:** en una hora de 22 y 50 ppm, con 3 y 8 parlantes, el desalineamiento queda en
  p95 0,25–0,40 ms;
- **el hallazgo:** el lazo anterior **sumaba lo que medía**. La referencia va después de la
  línea de retardo, así que la medición no incluye las correcciones. Es la firma del exp. 09 §5;
- **mismo cambio, del exp. 16:**
  - la calibración de más de 6 parlantes en dos grupos, que comparten tres parlantes;
  - `coherence` y `response_error_db` por tercio en cada calibración.
- **Mutaciones** (cada una pone en rojo un test, y se deshizo con reemplazo de texto):
  - la sonda 3 dB más baja (sin el factor 2);
  - la sonda sin sumarse;
  - sin la repetición entre mitades;
  - corregir con una sola medición;
  - sin seguir la deriva;
  - la zona muerta por parlante;
  - los grupos sin referirse a lo compartido;
  - promediar los tramos mudos;
  - el error previsto sin el factor de independencia;
  - siempre un grupo;
  - la medición tomada como residuo, que es el error viejo.

Siguen los pasos 3 (+5 ms inyectado, dos semillas) y 4 (A/B ciego de inaudibilidad). Antes decía: Planificado (2026-10-01). La calibración pasa a ser ante todo de **latencia y
sincronía**, no de espectro. Tiene que poder correr mientras suena la música, sin que se
note, y prenderse y apagarse sin reiniciar. Investigación: `research/03` §3.2. Diseño: spec
§4.

**El plan, en orden:**
1. **Simulación primero.** Música grabada sobre la sala simulada, con márgenes de −15, −20,
   −25 y −30 dB y ventanas de 2, 4 y 8 s. Se mide el error contra los retardos conocidos,
   con contenido correlacionado, que es el caso que hoy falla.
2. Construir `dsp/probe.py` (**hecho, 2026-10-02**, con dos cambios: va 21 ms detrás de la
   música en vez de un bloque adelante, y todos los parlantes a la vez en vez de por turnos):
   - ruido por parlante entre 300 Hz y 8 kHz, moldeado por tercio de octava bajo la música;
   - un bloque de anticipación;
   - apagado en silencio;
   - un parlante por ventana, por turnos.
   Después, el lazo se cambia para correlacionar contra la sonda, con los campos `probe` y
   `probe_margin_db` y rampas de 50 ms.
3. **Con parlantes:** un retraso inyectado de +5 ms, encontrado dos veces con semillas
   distintas.
4. **Inaudibilidad:** un A/B ciego con la sonda prendida y apagada en cada margen, con
   material crítico, de 16 intentos o más. Se adopta el margen más fuerte que no llega a
   p < 0,05.
5. El resultado va a `experimentos/11-…`.

**Con qué choca:**
- con el SBC de bitpool bajo, que puede quitarle bits a las bandas débiles (INFERIDO). El
  moldeado deja la sonda donde ya está la música;
- con el piso de ruido del micrófono. Lo mide el paso 1.

**Lo que la favorece:** no depende de A2DP. La misma sonda sirve para un backend Auracast.

### Métricas en vivo: stream de eventos del servicio al panel · i-7c8794-530882
**Estado: Hecho en código (2026-10-01); falta probarlo con parlantes** (d-7c8794-316465). Lo
que falta medir: que el pico del medidor caiga dentro de un cuadro (50 ms) del pico que
oye el micrófono. Lo pidió el usuario: que las métricas sean más en vivo, "quizás con un
stream". Spec: `superpowers/specs/2026-09-29-control-service-design.md` §17.
Es la adición que §14 dejó pendiente.

**Qué es:** `GET /v1/stream` con Server-Sent Events. Manda:
- `state` cuando cambia algo;
- los medidores a 20 Hz;
- el espectro de la entrada a 10 Hz;
- los logs a medida que ocurren.

Los medidores van **sincronizados con el oído**: cada trozo se muestra cuando suena, con la
latencia que midió la calibración (~0,5 s). Si no, se verían adelantados respecto de lo que se
oye. El panel deja de consultar cada 500 ms mientras el stream vive, y si el stream se cae
vuelve a consultar.

**Con qué choca:**
- con el motor, si un cliente lento lo frenara. El motor escribe en un anillo y nunca
  espera a nadie;
- con el número de hilos del servidor. Se permiten 8 streams como máximo.

**Lo que la favorece:** el contrato no cambia. Por serie (i-7c8794-a9f161) los mismos
eventos se piden con un `subscribe`.

### Cortes del audio: detector, causas y eliminación · i-7c8794-7d4aec
**Estado: A medias (2026-10-01).** Lo pidió el usuario: los cortes "afectan mucho a la
música". Decisión: d-7c8794-560b54. Detalle en experimentos/10 §9.

**Lo hecho:**
- el observador ya no abre `bluetoothctl` cada 3 s (eran ~3 monitores de anuncios LE por
  segundo);
- el chequeo de ruteo corre en un hilo aparte (pasar la medición del lazo a otro proceso se
  midió y era peor; sigue en un hilo, protegida para que una falla no corte el audio);
- la tubería guarda dos bloques de margen;
- cada corte queda registrado con su causa probable y se ve en Diagnóstico → Cortes.

**Agregado el 2026-10-02** (d-7c8794-0022c6, research/11 §3.2): la causa más probable de los
microcortes no estaba a la vista. **Cada `reduce bitpool` del sink Bluetooth de PipeWire es un
paquete descartado** (VERIFICADO en el código), y experimentos/10 §5.5 había contado ≥1,2 por
segundo leyéndolos como congestión inofensiva. Se construye el monitor de radio (`radio.py`),
el corte de tipo `radio` en la tarjeta, y el protocolo C2/C3 en `probes/14-microcortes/`
([experimentos/12](research/experimentos/12-microcortes-con-3-go-4.md)).

**Lo que falta, con parlantes:**
0. El protocolo de experimentos/12: línea base de 2 × 10 min con el registro de radio
   encendido, y una variable a la vez (2 parlantes, posiciones cambiadas, distancia, escaneo).
1. 20 minutos de música mirando la tarjeta Cortes.
2. Buscar dispositivos a propósito mientras suena, para confirmar o descartar el escaneo LE
   como causa.
3. Si quedan cortes en un solo parlante con el motor a tiempo, el enlace de ese parlante:
   distancia, batería y obstáculos.

### Pantalla Cadena: todas las perillas y algoritmos del motor · i-7c8794-a9189b
**Estado: A medias (2026-10-02): construido en simulación, falta usarlo con parlantes.** Lo
pidió el usuario: "más controles, que se vean y entiendan mejor; el panel debe poder tocar
todas las perillas de los algoritmos y elegir entre ellos, quizás en una pantalla dedicada".
Decisión d-7c8794-114c9c; spec `superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md`
§4 y §7.

**Qué es:** el motor como lista de etapas (ambiente, separar parlantes, difusión, alineación,
ecualización, graves, volumen, limitador), cada una con sus algoritmos y perillas descritos en
`chain.py`; el contrato `chain`/`chain_set`/`chain_reset`; y la pestaña **Cadena** del panel,
dibujada desde esa descripción, con la métrica en vivo de cada etapa.

**Con qué choca:** con que cambie cómo suena hoy. Lo cubre la prueba bit a bit contra el motor
anterior. Con `presets.json` y `instalacion.json`, cuyos lectores rechazan claves nuevas: las
elecciones van en archivos aparte.

### Graves y volumen: protección, bajo psicoacústico, crossover, AVRCP y limitador true-peak · i-7c8794-765571
**Estado: A medias (2026-10-02): construido y apagado por defecto, falta medirlo con
parlantes** (d-7c8794-d1118c, d-7c8794-0086cf). Segunda prioridad del usuario: "faltan graves
y volumen". Research/11 §2.2–2.3.

**Lo que falta, con parlantes** (`probes/15-graves-y-volumen/`):
1. La curva de volumen AVRCP → dB de cada Go 4, en dos sesiones.
2. El nivel de 125 Hz contra el volumen AVRCP, con y sin el pasa-altos de protección: ¿se
   retrasa la protección del firmware?
3. Un A/B ciego del bajo psicoacústico con la sonoridad igualada, 30 ensayos en dos sesiones.
4. El crossover, cuando el Charge 6 sea el cuarto parlante (por USB-C, E7).

**Con qué choca:** con el techo de 3 enlaces A2DP (el Charge 6 por Bluetooth sería el
cuarto). Con la deriva: a 100 Hz, 2 ms de desalineación hacen un hueco de ~2,2 dB en el cruce.

### Medidores de calidad: sonoridad, ganancia neta, true peak y PSR · i-7c8794-50c1b3
**Estado: A medias (2026-10-02): construido en simulación.** Research/11 §1.1. Sonoridad
BS.1770-5 de la entrada y de cada salida, ganancia neta en LU (habría delatado los −40 dB de
experimentos/10 §7), true peak, PSR de entrada contra salida (la métrica de "aplanada") y
actividad del limitador. También el A/B con la sonoridad igualada.

**Lo que falta, con parlantes** (`probes/16-calidad/`): la referencia medida "directo contra
motor" de i-7c8794-10ccb4 con la sonoridad igualada; la respuesta por parlante con coherencia
en 3 colocaciones; la prueba de si el Go 4 suma L+R.

### Cola difusa por parlante para envolvimiento con mezclas secas · i-7c8794-bf63c2
**Estado: A medias (2026-10-02): el algoritmo existe y está apagado.** Research/11 §2.4. Una
cola de ruido con decaimiento distinta en cada parlante genera campo difuso aunque la mezcla
no traiga ambiente, que es justo cuando Avendaño-Jot no aporta. **Falta:** una comparación
pareada de preferencia ("¿cuál te envuelve más?") en dos sesiones, y el RT60 de la pieza con
y sin ella.

### Banco del códec SBC y del tándem con PEAQ y ViSQOL · i-7c8794-342646
**Estado: Planificado.** Research/11 §1.2 y §3.2. En digital, con libsbc, en un entorno de
hatch aparte: bitpool 40/34/28, la doble compresión Vorbis/AAC/Opus → SBC, puntuado con los
MOVs de PEAQ, ViSQOL y el 2f-model. Decide si vale pedir lossless.

### Sink 5.1 nativo y loopback con latencia declarada para lip-sync · i-7c8794-ff1ce9
**Estado: Planificado.** Research/11 §3.4–3.5. Un sink `aurasync (5.1)` que no sea el por
defecto, con mapeo a quad o 3/1; y el sink de entrada como `module-loopback` con
`latencyOffsetNsec` igual a la latencia calibrada, para que los reproductores compensen el
video. **Con qué choca:** con WirePlumber, que mueve streams; se comprueba el ruteo después.

### Upmix tipo DirAC para cuatro esquinas · i-7c8794-59cb30
**Estado: Planificado.** Research/11 §3.4. Dirección y difusividad por celda
tiempo-frecuencia, lo directo paneado a las cuatro esquinas y lo difuso decorrelado. Va después
de medir lo de arriba.

### Motor de audio en Rust para el camino crítico, con Python para el resto · i-7c8794-fd9732
**Estado: A medias (2026-10-02): la prueba de concepto de E/S nativa está en preparación**
(`probes/17-e-s-nativa-rust/`, d-7c8794-36dde5). Lo pidió el usuario: explorar Rust para la parte
crítica del audio y dejar el resto en Python; eligió empezar por la prueba de concepto. Investigación y plan:
[research/12](research/12-motor-de-audio-en-rust.md). Reabre d-7c8794-c23c20 si se adopta.

**Lo que ya salió de investigarlo:** la lectura sinc del retardo tenía al motor a ~5× el tiempo
real; se corrigió en numpy (32×, experimentos/12 §1.1). Rust no arregla los descartes de radio.

**El plan:** medir experimentos/12 (compuerta) → andamio con maturin y hatch → DSP etapa por
etapa contra el oráculo numpy (golden ≤1e-9) → motor completo con la fachada `RustMotor` → E/S
nativa en PipeWire con A/B → la Pi, Auracast y macOS. Criterios en research/12 §4.

**Con qué choca:** con la velocidad de iteración (dos lenguajes); con hatch (un miembro maturin
en un workspace no está probado); con la E/S de PipeWire, que se compila en cada equipo.

**Las dudas y sus respuestas:** research/12 §5.

### Panel como PWA en GitHub Pages conectado por red local con HTTPS y token por cliente · i-7c8794-b10884
**Estado: A medias (2026-10-02): construida y probada en Chromium contra el servicio simulado;
falta publicarla y probarla en teléfonos.** El transporte con token y ticket, la pantalla de
conexión y emparejamiento, la administración de clientes, el service worker offline al estilo de
`thom-music-player` y el workflow de Pages están hechos (75 tests de navegador). **Publicar es
decisión del usuario:** Settings → Pages → Source: GitHub Actions, y push a `main`. Lo pidió el usuario: un panel reutilizable
que exista sin el dispositivo, se actualice aparte y alivie a la Pi; por ahora siempre en la
red local. Decisión d-7c8794-37f9bc; investigación en research/13.

**Qué es:** el build de `host/web/` publicado en GitHub Pages por GitHub Actions, instalable y
offline (service worker propio, al estilo de `thom-music-player`); el servicio con HTTPS por
una raíz propia, CORS para el origen de Pages, tokens por cliente y emparejamiento; el
descubrimiento por `aurasync.local`, la última IP conocida o un QR.

**Lo que falta medir:** que una página en Pages llegue al dispositivo en Chrome Android,
Chrome de escritorio, Safari del iPhone (navegador y PWA instalada) y Firefox, con la raíz
instalada y sin ella.

**Con qué choca:** con Safari, que no tiene la excepción de red local de Chrome; con Chrome,
que cambia seguido sus reglas de red local; con `EventSource`, que no manda cabeceras (el
stream necesita otro mecanismo).

### Hasta 8 parlantes: límites y capacidades del diseño · i-7c8794-1f35a1
**Estado: A medias (2026-10-02): el análisis en simulación está hecho**
([experimentos/16](research/experimentos/16-ocho-parlantes-en-simulacion.md)), y el motor ya
escala a 8 (§9 ahí). Encontró y se corrigió un error de la calibración que también afectaba a 3
parlantes (la pista del estimador de nivel). **Hecho el 2026-10-02 (§9):** el tope del
decorrelador avisa en vez de impedir arrancar (el servicio arranca con 7 y 8, también en
`--simular`); roles por ángulo con los layouts `5.0`, `hex`, `7.0`, `octagon` y `rings`; la sala
simulada con 8 parlantes distintos; la asignación de filtros por mezcla, como perilla
(`decorrelate.assignment: mix`) y no por defecto. **Descartado** (§9): elegir el banco por el peor
par por octava, porque su ventaja era un artefacto del retardo cero. Lo que falta, por prioridad:
calibrar en dos grupos con un ancla desde 7 parlantes; la sonda simultánea en vez de por turnos
(por turnos no converge desde 6 parlantes); seguir la deriva de cada parlante en el lazo; que el
estado y el mapa del panel muestren el aviso y los layouts nuevos; el panel por grupos; y oír en
el A/B ciego si `assignment: mix` cambia algo. Meta del usuario (d-7c8794-3b7793): llegar a 8
parlantes para probar los límites. Hardware de hoy: 3 Go 4 y 1 Charge 6.

**Lo que ya se sabe que no escala:** debajo de 2 kHz ningún banco de filtros fijos separa a dos
parlantes (con cualquier N; sobre 500 Hz el peor par pasa de 0,46 con 3 a ~0,50 con 8); un
adaptador A2DP sostiene 2–3 enlaces; la ganancia de la calibración con un micrófono pierde
exactitud desde 7 fuentes simultáneas; el lazo por turnos tarda más en volver a cada parlante.

**Con qué choca:** con el transporte. Para 8 hacen falta 3–4 adaptadores, 8 Picos, o Auracast
con un BIG de 8 BIS (research/13 §2.3).

### Calibrar el retardo sin un micrófono central · i-7c8794-4745b4
**Estado:** Planificado. Pedido del usuario el 2026-09-29. El detalle técnico, con el estado de
evidencia de cada camino, está en
[03](research/03-bluetooth-clasico-y-sync-por-software.md) §3.1.

**Qué es:** medir el desfase **electrónico** entre parlantes sin depender de un micrófono
puesto en un punto, usando lo que digan el stack o los códecs.

**Por qué, y son dos razones medidas, no comodidad:** la calibración con micrófono **alinea en
el punto del micrófono y desalinea el resto de la pieza** (mide el retardo total, que incluye
34 cm = 1 ms), y **el parlante que más aporta al envolvimiento es el que peor se mide**, porque
su señal es la más decorrelacionada
([experimentos/09](research/experimentos/09-primera-escucha-con-3-go-4.md) §5 y §7).

**Lo que ya está cerrado:** los Go 4 y el Charge 6 **no mandan AVDTP Delay Report** (MEDIDO en
E6). Ese era el mecanismo obvio y no está.

**El primer paso, que cuesta un minuto y decide el resto:** con los parlantes conectados,
`pw-dump | grep -E "node.name|latency|delay"`. PipeWire calcula una latencia por sink
Bluetooth; si difiere entre parlantes y es estable, **da el desfase electrónico sin emitir
sonido**. **Lo que hay que desconfiar:** que sea el valor nominal del buffer configurado y por
lo tanto idéntico para los tres, en cuyo caso no sirve. No se pudo comprobar el 2026-09-29
porque los parlantes estaban apagados.

**Cómo se valida sin confiar en él:** comparándolo contra la calibración con micrófono, que ya
existe y ya mide. Las dos piezas están construidas, así que la comparación es gratis.

**Con qué choca:** con nada; es aditivo. Si funciona, la calibración con micrófono queda como
referencia y verificación, no como el mecanismo de todos los días.

### Portabilidad del host a macOS · i-7c8794-a848a0
**Estado:** Planificado. Pedido del usuario el 2026-09-29, junto con los controles en vivo.

**Qué es:** correr `aurasync` en el Mac (Apple Silicon, macOS 27), que es la estación de
trabajo, y saber **qué parte del código sirve igual y qué hay que escribir de nuevo**.

**Lo que ya se sabe que cambia, sin tocar nada:** todo `sonido.py`. Está escrito sobre las
herramientas de PipeWire —`pw-dump`, `pw-play`, `pw-record`, más `pactl` para reparar el
ruteo— y ninguna existe en macOS. Concretamente hay que rehacer: descubrir parlantes y
micrófonos, reproducir a N destinos, grabar, el **sink virtual** y la **comprobación de
ruteo**.

**Lo que debería servir sin cambios, y es la mayor parte:** `dsp/` (decorrelador, extracción
de ambiente, línea de retardo), `motor.py`, `medicion.py`, `sincronia.py`, `config.py` y
`estimulos.py`. **No hacen E/S** —es la regla de [08](research/08-integracion-y-plan.md) §6.1—
y sus 167 tests corren sin radio ni parlantes, así que en el Mac deberían pasar tal cual. Eso
es la hipótesis a comprobar primero, y es barata: instalar y correr `hatch test`.

**Con qué choca, y conviene saberlo antes de entusiasmarse:**
- **el sink virtual es lo más difícil.** En Linux sale de una línea de `pw-record` con
  `media.class=Audio/Sink` y **no deja huella**. En macOS no hay equivalente: un dispositivo
  de audio virtual pide una extensión del sistema (`AudioServerPlugin` / CoreAudio driver),
  que se **instala**, o depender de algo de terceros como BlackHole. Eso rompe la propiedad de
  P1, y hay que decidir si se acepta. La mitad de P1 en Mac (i-7c8794-fd5f03) es justamente
  esta pregunta y sigue abierta;
- **A2DP en macOS no da un stream por parlante.** macOS no expone varios sinks Bluetooth
  independientes con el control que da BlueZ, así que el camino "un `pw-play` por parlante"
  puede no tener traducción. **Esto podría hacer que el MVP entero no sea portable por A2DP**,
  y que en Mac haya que esperar a Auracast (las SuperMini) — donde el emisor es el mismo
  código en los dos sistemas porque habla por serie con el controlador.

**Qué la favorece:** el error de ruteo de
[experimentos/09](research/experimentos/09-primera-escucha-con-3-go-4.md) §2 es un argumento
a favor de portar **después** y no antes: la capa de audio del sistema es donde están los
problemas que ningún test encuentra, y conviene tener la experiencia de una plataforma antes
de abrir la segunda. El usuario lo dijo así: *"cuando tenga el stack, desarrollar con la
experiencia obtenida sobre eso"*.

**Primer paso concreto, que no cuesta nada:** clonar en el Mac, `hatch test`, y anotar qué
falla. Eso ya separa el núcleo portable de la capa que no lo es, con un número.

### Upmix de estéreo a 4.0 · i-7c8794-c7ccb9
**Estado:** Planificado. Depende del emisor.

**Qué es:** convertir fuentes estéreo a quad (FL, FR, RL, RR), con un retardo
trasero configurable. Las fuentes que ya son multicanal (juegos, archivos 5.1) no
pasan por el upmix: van a un sink quad discreto
([07](research/07-software-de-audio-en-el-pc.md) §6).

**Con qué choca:** con nada, porque ocurre antes del emisor.

**Qué la favorece:** PipeWire ya lo trae ([03](research/03-bluetooth-clasico-y-sync-por-software.md) §2), y FFmpeg `surround` corre dentro de filter-chain desde PipeWire 1.6.0.

**Qué cambió (2026-09-26, [07](research/07-software-de-audio-en-el-pc.md) §4.2):**
- el upmix de PipeWire **viene apagado** por defecto;
- `psd` manda a los dos traseros **la misma señal L−R en contrafase**, así que no
  da traseros estéreo;
- el layout se llama `Quad`: **`4.0` no es cuadrafonía**, ni en PipeWire ni en
  FFmpeg.

Por eso ya no se asume `psd`: hay que comparar `simple`, `psd` y FFmpeg
`surround=chl_out=quad` de oído.

**Qué hay que decidir antes:** qué distribución de canales usar. Según
[07](research/07-software-de-audio-en-el-pc.md) §4.5 (INFERIDO):
- **quad** para música y juegos, con dos Go 4 adelante;
- **3/1 (L C R S)** para películas, con el Charge 6 al centro.

La opción 3.1 con el Charge 6 como LFE queda descartada en la práctica, porque
ninguno de los parlantes es subwoofer.

### Calibración de la alineación con micrófono · i-7c8794-1ab281
**Estado: construida, y con un hueco de diseño encontrado el 2026-09-29.**

**El hueco: la calibración fabrica un sweet spot, que es lo contrario del objetivo.**
`gcc_phat` mide el retardo **total**, que incluye el vuelo por el aire —**34 cm son 1 ms**—, y
la calibración escribe ese total. Así alinea en el punto donde está el micrófono y **desalinea
el resto de la pieza**, cuando el objetivo declarado es parlantes en los bordes y oyente
caminando ([09](research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §4).

Lo que conviene corregir es el desfase **electrónico**, que es igual en toda la pieza.
`config.py` ya tiene `retardo_acustico_ms` para poder restarlo, pero la calibración **no lo
usa**: solo lo usa `alinear_por_geometria`, que nadie llama. Hace falta las coordenadas de los
parlantes y del micrófono, hoy opcionales y vacías (va con i-7c8794-26c302).

**Y hay un número que lo vuelve urgente:** E6 midió 2,7 ms entre dos Go 4 y notó que son
**compatibles con 92 cm de diferencia de camino**
([experimentos/05](research/experimentos/05-e6-a2dp-un-canal-por-parlante.md)). Si el desfase
electrónico es casi nulo, corregir el total puede ser **peor que no corregir nada** para
alguien que se mueve. Está en
[experimentos/09](research/experimentos/09-primera-escucha-con-3-go-4.md) §7.

**Qué es:** reproducir un pulso por canal, grabarlo con un micrófono y calcular el
desfase de cada parlante. Con ese dato se ajusta la posición (retardo acústico por
distancia) y el Charge 6 si va por cable.

**Con qué choca:** con nada.

**Qué la favorece:** google/audio-sync-kit y Chorus sirven como referencia. No
existe una herramienta para Linux, así que es un aporte original
([03](research/03-bluetooth-clasico-y-sync-por-software.md) §2).

**Qué hay que decidir antes:** qué micrófono usar (el del laptop o el de un
teléfono).

## Camino alternativo: A2DP (solo si E4 falla)

### Camino A2DP con calibración · i-7c8794-f6edc9
**Estado:** Planificado. Bloqueado por la decisión de seguir.

**Qué es:** combine-stream con asignación de canales, 2 o más dongles, y
calibración con micrófono que se repite en cada inicio de reproducción. Snapcast
con una Raspberry Pi por parlante queda como variante si un solo equipo no aguanta.

**Con qué choca:** **no puede garantizar la invariante central.** A2DP no tiene un
reloj compartido y PipeWire no corrige el drift. Solo ofrece la mejor alineación
posible, y hay que aceptarlo de forma explícita.

**Qué la favorece:** es estable, lo soportan todos los parlantes y no requiere LE
Audio.

**Qué hay que decidir antes:** si una alineación de mejor esfuerzo (~5–20 ms
variable) alcanza para el uso que se quiere.

## Fase 3: emisor dedicado (después de M2 y P3)

### Fase 3: emisor dedicado en una Raspberry Pi que el PC ve como tarjeta USB · i-7c8794-80f3ac
**Estado:** Planificado. Depende de M2 y de P3.

**Qué es:** la misma herramienta del MVP, corriendo en una Raspberry Pi:
- con el backend `gadget` (ALSA UAC2 y `Capture Pitch`);
- con la SuperMini por el UART;
- opcionalmente, con entradas de red (shairport-sync, librespot).

El PC la ve como una tarjeta de sonido de 4 canales y no instala nada, en
Windows, macOS o Linux ([08](research/08-integracion-y-plan.md) §7.3).

**Con qué choca:** con el lip-sync, porque el PC no conoce la latencia del tramo
Auracast y en video queda el ajuste manual del reproductor. No choca con la
invariante: el BIG es el mismo.

**Qué la favorece:** el gadget asíncrono hace que el PC entregue al ritmo del
BIG, así que no hace falta remuestrear.

**Variante con la Pico 2 W** (H2b, [08](research/08-integracion-y-plan.md) §3.1):
TinyUSB, BTstack y liblc3 en C, con la SuperMini por UART. No reutiliza el núcleo
Python, pero no cuesta nada.

**Qué hay que decidir antes:** Pico 2 W o Pi Zero 2 W. Lo decide P3(a): si 4
codificadores LC3 caben en el RP2350.

### Transporte serie para el contrato de control · i-7c8794-a9f161
**Estado:** Planificado, solo en el roadmap. Depende del servicio de control
(i-7c8794-bdb678) y de la elección de la Fase 3.

**Qué es:** llevar el mismo contrato JSON del servicio de control por una línea serie: un
mensaje por línea, con `id` para emparejar respuestas. No hay nada más que diseñar: la ruta
`POST /v1/command` ya recibe el mensaje crudo tal como iría por serie, y los tests de la
primera entrega comprueban que cada ruta REST da lo mismo que ese mensaje.

**Para qué, según la variante de la Fase 3:**
- **con la Pico 2 W,** el servicio queda en el PC (la Pico es C y no corre el núcleo Python),
  y la serie es el enlace entre el servicio y el firmware, que tiene un control propio chico
  (el BIG, el volumen por canal, su estado);
- **con la Pi Zero 2 W,** el servicio entero se muda a la Pi, y la serie es otro camino para
  que una interfaz le hable, por un puerto serie USB del gadget.

**Con qué choca:** con nada del núcleo. En la Pico, parsear JSON en C cuesta RAM y código;
si no cabe, el contrato admite una codificación más compacta **con los mismos nombres y
códigos de error**.

## Proceso y herramientas

### Migrar el código existente del host al inglés · i-7c8794-f30928
**Estado:** Planificado. Pedido del usuario el 2026-09-29 (d-7c8794-7b3093).

**Qué es:** traducir al inglés los identificadores, docstrings y comentarios de lo que ya
existe en `host/` (`motor.py`, `config.py`, `medicion.py`, `sincronia.py`, `sonido.py`,
`dsp/`, `cli.py`) y los campos de `instalacion.json`, con migración del archivo que ya tiene
el usuario.

**Cómo:** en un cambio aparte, **sin mezclar funcionalidad con renombres**, para que el diff
sea revisable y los 167 tests prueben que nada cambió de comportamiento. Al terminar, el
mapa de nombres de `control.py` se vuelve la identidad y se borra.

**Con qué choca:** con cualquier rama abierta que toque esos archivos. Conviene hacerlo
entre entregas, no en medio de una. Los datos crudos de `docs/research/experimentos/datos/`
(por ejemplo el registro del lazo, con clases como `ajuste`) no se reescriben: son
históricos.

### Primer commit de la preparación · i-7c8794-c28668
**Estado:** Hecho (2026-09-25).

**En qué quedó:** un primer commit en `main` que incluye `.agents/` (con su nuevo
`carrier.toml`), `docs/`, `CLAUDE.md`, `probes/`, `scripts/check.sh` y
`.claude/logs/`. `scripts/check.sh` pasó antes del commit.

**Remoto (2026-09-26):** `origin` =
`git@github.com:fabaindaiz/bluetooth-sync.git`, **privado** (la API pública de
GitHub responde 404 sin autenticación). El primer push fue forzado, a pedido del
usuario, y reemplazó el "Initial commit" con un README de una línea que GitHub
había creado.

**Qué falta:** **si alguna vez se hace público**, antes hay que limpiar los datos crudos de los dispositivos
(d-7c8794-8374e1): direcciones y nombres en `docs/research/experimentos/datos/` y
en los documentos 00 y 01.

## Cerrado por medición

(Vacío. Cuando un experimento descarte una idea, va aquí con su número.)

## Cuánto le cuesta cada idea a la invariante

| Idea | ¿Rompe la referencia de tiempo común con un canal por parlante? |
|---|---|
| Emisor multicanal (un BIG) | No, es lo que la hace posible |
| Asistente BASS | No |
| Upmix 4.0 | No, ocurre antes del emisor |
| Charge 6 por USB-C | **Sí, en parte**: es otro camino con otra latencia. Se compensa con un retardo fijo medido |
| Calibración con micrófono | No, solo la verifica |
| Herramienta CLI (MVP) | No. Un solo BIG; los 4 canales comparten remuestreador, así que el drift no los desalinea entre sí |
| Emisor dedicado en una Pi (Fase 3) | No. Es el mismo BIG; solo cambia de dónde viene el audio |
| Camino A2DP | **Sí**: no hay reloj común; es la mejor alineación posible |
| Dos transmisiones independientes | **Sí**: descartado por d-7c8794-203de2 |
