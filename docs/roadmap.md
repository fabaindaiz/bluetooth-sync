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

La base de todo es [docs/research/README.md](research/README.md).

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

**Hardware disponible:**
- 3× JBL Go 4 y 1× JBL Charge 6.
- Un equipo con Linux cuyo chip Bluetooth aún no está identificado (i-7c8794-d9c834).
- **5× SuperMini nRF52840** (clon de nice!nano), compradas el 2026-09-26 y aún no
  recibidas (d-7c8794-b82ee9). Van con `hci_uart` como controlador para Bumble; se flashean con
  el target `promicro_nrf52840`. En cada medición hay que anotar qué unidad y qué
  fuente de reloj de 32 kHz se usó ([06](research/06-opcion-c-nrf5340.md) §1).

**Stack en estudio (2026-09-26):** el usuario eligió explorar las opciones **A**
(Python + Bumble en el PC) y **C** (nRF5340 como emisor dedicado), además de la
**combinada** (Bumble con un nRF por `hci_uart`). La comparación y el orden sugerido
están en [research/README.md](research/README.md) §"Opciones A y C comparadas":
primero A con el chip interno; si no sirve, la combinada o C.

**Siguiente paso:** Fase 1, empezando por el inventario del equipo.

Etapas del plan:

```
Fase 1 · Factibilidad (probes, sin producto)            ← estamos por empezar
  inventario → E1 → E2 → E3 → E4 ──► decisión de seguir o no
                             └─ E6, E7 en paralelo (línea base y USB-C)
Fase 2 · Prototipo, camino A (Auracast, un BIG)          ← solo si E4 sale bien
  emisor multicanal → asistente BASS → upmix 4.0 → calibración
Camino alternativo · A2DP                                ← solo si E4 sale mal
```

## Fase 1: factibilidad

Todo en esta fase es un **probe**. El código va en `probes/<nombre>/` y se borra
cuando su resultado queda anotado en `docs/research/experimentos/`
(d-7c8794-3208b7). Cada resultado lleva la marca **MEDIDO**, con el número, el
equipo, las versiones (kernel, BlueZ, PipeWire, Bumble y el firmware de los JBL) y
la fecha.

### Inventario del equipo Linux · i-7c8794-d9c834
**Estado:** A medias.
- **Hecha la mitad del Mac** (2026-09-26, en
  [experimentos/00-inventario-mac.md](research/experimentos/00-inventario-mac.md)):
  el chip es un MediaTek MT7932 por PCIe con LE Audio, pero Bumble no puede llegar
  a él en macOS. El Mac sirve como estación de desarrollo para A y C.
- **Falta el equipo Linux.** Es la mitad que decide si A se prueba sin comprar
  nada.

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
**Estado:** Planificado. Depende del inventario.

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

Depende de E1, o por lo menos de un controlador que pueda escanear anuncios
periódicos.

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
BASS. No se sabe si lo hacen.

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

### E6: línea base con A2DP y combine-stream · i-7c8794-24ea65
**Estado:** Planificado. Puede hacerse en paralelo desde el inventario.

**Qué es:** 2 Go 4 por A2DP clásico, con FL y FR usando
`libpipewire-module-combine-stream`. Se miden el desfase con un micrófono, la
variación entre inicios de reproducción y lo que reporta `pw-dump`, para saber si
llega un delay report o si PipeWire usa los 125 ms fijos.

**Con qué choca:** probablemente se necesite un segundo dongle (RTL8761B) si el
chip integrado no aguanta dos streams.

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
código de producto.

**Qué hay que decidir antes:** el umbral de desfase tolerable (ver E5).

## Fase 2: prototipo por el camino A (solo si la decisión es seguir)

La arquitectura prevista, **sujeta a lo que muestren E3 y E4**:

```
reproductor ──► sink virtual de PipeWire "jbl-multicanal" (estéreo o 4.0)
                   │ upmix (channelmix psd) si la entrada es estéreo
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

### Upmix de estéreo a 4.0 · i-7c8794-c7ccb9
**Estado:** Planificado. Depende del emisor.

**Qué es:** convertir fuentes estéreo a 4.0 (o a 3.1 usando el Charge 6) con
`channelmix.upmix` en modo `psd`, más un retardo trasero configurable.

**Con qué choca:** con nada, porque ocurre antes del emisor.

**Qué la favorece:** PipeWire ya lo trae ([03](research/03-bluetooth-clasico-y-sync-por-software.md) §2).

**Qué hay que decidir antes:** qué distribución de canales usar con 3 Go 4 y 1
Charge 6: 4.0 (FL, FR, RL, RR) o 3.1 (FL, FR, C y el Charge 6 como LFE).

### Calibración de la alineación con micrófono · i-7c8794-1ab281
**Estado:** Planificado.

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

## Proceso y herramientas

### Primer commit de la preparación · i-7c8794-c28668
**Estado:** Hecho (2026-09-25).

**En qué quedó:** un primer commit en `main` que incluye `.agents/` (con su nuevo
`carrier.toml`), `docs/`, `CLAUDE.md`, `probes/`, `scripts/check.sh` y
`.claude/logs/`. `scripts/check.sh` pasó antes del commit.

**Qué falta:** no hay remoto. Queda por decidir si habrá uno y dónde. **Si alguna vez
se publica**, antes hay que limpiar los datos crudos de los dispositivos
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
| Camino A2DP | **Sí**: no hay reloj común; es la mejor alineación posible |
| Dos transmisiones independientes | **Sí**: descartado por d-7c8794-203de2 |
