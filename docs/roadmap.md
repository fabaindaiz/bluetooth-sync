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

**El monitor es un enlace Bluetooth más** (d-7c8794-7f1790, usuario 2026-10-09). En todos los objetivos de la
plataforma, el monitor de audífonos se trata como una conexión Bluetooth que también hay que observar y medir,
igual que los parlantes. Se miden su protocolo (códec, tasa de bits, latencia A2DP, retransmisiones y
descartes de radio, señal), sus xruns, su colchón y sus reconexiones. Tiene sus diferencias: es estéreo, no
se sincroniza con los parlantes y tiene su propia configuración (modos `mix`, `stereo`, `binaural`; su propio
colchón y su propio emparejamiento de volumen). Cada experimento que mida radio o salida lo incluye
(i-7c8794-7505b5).

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
4. **El rendimiento del motor medido** (i-7c8794-be46cb, pedido del 2026-10-05): un informe que se
   pueda repetir, en el panel junto a la calidad y la sincronía.
5. **Antes del próximo release** (pedido del 2026-10-05): el modo simple con antes y después
   (i-7c8794-0ad844), "Sonando ahora" (i-7c8794-99f87e) y la campaña de A/B de la ecualización
   (i-7c8794-d9c64a), que se mide con los parlantes en `PC-Ryzen5`.

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
([experimentos/13](research/experimentos/13-e-s-nativa-en-rust.md); la parte sin parlantes, medida y
cumplida en `HP-O16` el 2026-10-09; falta la de los Go 4); si cumple, el motor en
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

**Para la próxima sesión (escrito al cerrar el 2026-10-09, s-7c8794-474a38):**
1. ~~Integrar Rust 13–14 y el GIL desde la rama `rust-ramps-loudness`~~: **hecho el 2026-10-10**, con el
   convoy del GIL medido y mitigado (experimentos/20 §15). Queda medir `sys.setswitchinterval` en `HP-O16`
   (i-7c8794-46d296).
2. ~~Etapa 3 de transiciones, el render (i-7c8794-da4172)~~: **construida el 2026-10-10**; falta escucharla
   (experimentos/23 §9.2).
3. La revisión final de toda la rama `seamless-transitions` y `check.sh` entero.
4. El A/B final con el protocolo de experimentos/23 §6.5, más las escuchas de las etapas 4 (§8.2) y 3 (§9.2).

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
- **4× SuperMini nRF52840** (clon de nice!nano), compradas el 2026-09-26 (el usuario corrigió
  el 2026-10-07: eran 4, no 5) y **recibidas el 2026-10-07** (d-7c8794-b82ee9). Van con `hci_uart` como controlador para Bumble; se flashean con
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
**Actualización del 2026-10-07:** las 4 SuperMini llegaron. E1 (en la SuperMini) y E2 a E5 quedan
desbloqueados, con su plan en `docs/superpowers/plans/2026-10-07-auracast-supermini-e1-e5.md` y la
validación del hardware con fuentes primarias en [research/02](research/02-le-audio-auracast-linux.md) §7.
Esa validación concluye que sirven con un BIG sin cifrar, los búferes ISO subidos y un RTN efectivo de
1–2. Los caminos alternativos están en §7.1.
**E1 en la SuperMini, hecho (2026-10-07, [experimentos/21](research/experimentos/21-f1-iso-en-la-supermini.md)):
SÍ.** En las placas A y C, la SDC crea el BIG de 4 BIS en 48_4 y 48_2, con NSE 2 e IRC 2 (una
retransmisión), y rechaza el cifrado. La otra placa lo recibe entero, y los 4 BIS van alineados en la fuente
(80 arranques) y en el aire (39 arranques, y 2 corridas de 60 s y una de 10 min). El emisor necesita ≥ 40 ms de colchón y seguir el reloj del controlador.
La placa C tiene cristal de 32 kHz (estable a 0,04 ppm; con el RC, ±26 ppm) y su controlador ya lo usa.
La placa A también tiene cristal. **El cristal de 32 MHz de las dos placas corre rápido** (A +79 ppm, C +64 ppm),
fuera de los ±50 ppm de BLE: queda abierto si un JBL lo acepta (E3). El emisor autónomo para la prueba de
distancia está listo y la prueba quedó para otra sesión.
El sniffer está preparado (research/02 §8, experimentos S0–S5).
**Pausa de los emisores Bluetooth (usuario, 2026-10-08).** Todo lo de Auracast y las SuperMini queda
cerrado por ahora, documentado y en `main`. **La próxima sesión retoma el panel web y el motor en Rust,
para probarlos en `HP-O16`.**

| Qué | Estado al pausar | Dónde |
|---|---|---|
| E1 en la SuperMini | **Sí**: BIG de 4 BIS alineados, recibido entero | experimentos/21 |
| Relojes | las dos placas medidas tienen cristal de 32 kHz y el de 32 MHz rápido (+64 y +79 ppm) | experimentos/21 §6, §8, §10 |
| Placa A | controlador con cristal y toque a 1200 baudios | inventario de HP-O16 |
| Placa C | **emisor autónomo**, listo para la prueba de distancia; vuelve al controlador por software | inventario de HP-O16 |
| Placas B y D | sin estrenar; medir su reloj antes de elegir el emisor (d-7c8794-507516) | — |
| Controlador | plan aprobado en su orden, sin empezar | specs/2026-10-07-supermini-controller-improvements-design.md |
| Sniffer | preparado, sin grabar; Wireshark más adelante; hay un Android | research/02 §8 |

**Para retomarlos, en orden:**
1. la prueba de distancia con la C en un cargador;
2. repetir el colchón a lazo abierto, que se cortó a los 16 min;
3. la prueba LC3 de punta a punta;
4. medir B y D;
5. la base del controlador;
6. E2–E4 con los JBL.

E5 va en `PC-Ryzen5`, que ya tiene NCS instalado (s-7c8794-884b69).

**Orden acordado con el usuario (2026-10-07, noche) para llevar esto al servicio:**
- el panel y el diagnóstico van ya; el **backend emisor Auracast espera a E4**;
- dentro del panel, el orden es:
  1. una **auditoría del panel** contra las apps del rubro y los estándares de UI, con prioridad en PC
     (medida con Playwright y axe, y un guion SUS para el usuario), que se escribe en
     [research/10](research/10-panel-de-control.md);
  2. el **modo simple** con antes/después (i-7c8794-0ad844), ajustado por la auditoría;
  3. el **diagnóstico de la radio Auracast** en el servicio y el panel: el controlador, el reloj, el BIG y
     lo que anuncian los JBL, con el molde de `RadioMonitor`;
  4. el **informe de rendimiento del motor** (i-7c8794-be46cb).
- Cada uno lleva su propia spec y su propio plan.
- **1. La auditoría, hecha (2026-10-07):** está en [research/10](research/10-panel-de-control.md) §9–§10 (las apps del
  rubro y los estándares, con la lista priorizada) y en [experimentos/22](research/experimentos/22-auditoria-medida-del-panel.md)
  (lo medido con Playwright y axe). Siguiente:
  - el grupo 1 de §10 (defectos medidos y baratos: el nombre del volumen, el foco que se pierde en
    Parlantes, las ayudas `(?)`, el contraste, las regiones vivas, el deshacer) como un cambio chico antes del
    modo simple;
  - revisar la spec del modo simple con los puntos 10 y 11.
  - **Decidido por el usuario (2026-10-07):**
    - el grupo 1 va **antes** del modo simple;
    - **menú lateral en PC** como `?layout=lateral` de acceso anticipado (d-7c8794-b7cdbd);
    - **axe-core** como dependencia de desarrollo de los tests (d-7c8794-a5f3ba);
    - copiar el **SUS en español** con su cita (pendiente: traer los ítems de la fuente);
    - la spec del modo simple suma el **desvío rápido no ciego** y la **luz de estado de la cadena**.
- **El plan de mejora del controlador** de las SuperMini (lo requerido y lo deseado, y cómo entra en
  aurasync) quedó **aprobado en su orden** (d-7c8794-507516):
  - primero la base: R5, R3, R4 y R7;
  - después los comandos propios (D1) y la telemetría del reloj dentro del controlador (D2);
  - las placas B y D se miden antes de elegir el emisor, y la de peor reloj va al sniffer.

  **Sniffer:** Wireshark más adelante; hay un teléfono Android para S5.
  **E5:** en `PC-Ryzen5` con el fifine, con tolerancia < 5 ms adelante y < 20 ms atrás (d-7c8794-910d28).
  [superpowers/specs/2026-10-07-supermini-controller-improvements-design.md](superpowers/specs/2026-10-07-supermini-controller-improvements-design.md).

Etapas del plan:

```
Fase 1 · Factibilidad (probes, sin producto)            ← en curso
  inventario ✔ → E1 ✔ (NO: el AX210 no transmite) → E1 en la SuperMini ✔ (SÍ: BIG de 4 BIS, experimentos/21)
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
**Estado (2026-10-07): Hecho también en la SuperMini, y ahí la respuesta es SÍ**
([experimentos/21](research/experimentos/21-f1-iso-en-la-supermini.md)): un BIG de 4 BIS con NSE 2 e IRC 2,
sin cifrar, recibido por otra SuperMini, y alineado.
**Estado: Hecho (2026-09-28) en el AX210. La respuesta es NO**, medida por dos caminos
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
**Estado: A medias (2026-10-08): el andamio, la lectura sinc y las etapas 5, 6, 7, 10 y 11 del
plan portadas** (d-7c8794-dc712e, d-7c8794-196e0c, d-7c8794-0d2a1e; el 2026-10-05 eran los pasos 1
y 2). Son **cinco piezas portadas**, contando la lectura: la lectura sinc, el upmix espacial, el
extractor de ambiente, los filtros FIR (`StreamingFIR` y `PartitionedFIR`, debajo del EQ, el
crossover, la protección de graves y la cola difusa) y el generador de armónicos (`VirtualBass`);
las ordinales de abajo (segunda a sexta) cuentan los pasos medidos en experimentos/20, no las
piezas. El andamio (`engine/`, maturin, `check.sh`) y la lectura sinc en Rust están
construidos, solo con tests y el costo medido en `HP-O16` (8,3–10,5× numpy,
[experimentos/20](research/experimentos/20-costo-de-la-lectura-sinc-en-rust.md)); nada se ha
oído ni probado con parlantes. **Segunda etapa portada (2026-10-05): el upmix espacial / frente
intacto**, golden ≤ 2,2e-15, 3,2–3,8× numpy (experimentos/20 §2). **Tercera (2026-10-05): el
extractor de ambiente**, golden ≤ 5,4e-14, 1,4–2,0× numpy, una vez por entrada y no por
parlante (experimentos/20 §3). **Cuarta (2026-10-08): los filtros FIR por convolución FFT**
(`StreamingFIR` y `PartitionedFIR` de `dsp/eq.py`; con ellos pasan a Rust, sin tocar quien los
usa, el EQ por parlante, las ramas del crossover, la protección de graves y su pasa-todo, los
graves virtuales y la cola difusa), golden ≤ 3,1e-13, 2,6–3,0× numpy por filtro; la cadena entera
con todo encendido queda en 25–35× tiempo real con Rust contra 11–14× con numpy (experimentos/20
§5). **Quinta (2026-10-08): el crossover y la protección de graves** sobre esos FIR: no hubo nada
más que portar (lo que la etapa hace por bloque fuera de la convolución son 0,02–0,05 ms; el
diseño del filtro sigue en numpy), golden ≤ 1e-9 contra numpy en `engine=rust` en los dos órdenes
LR y cortes de 40 a 200 Hz, 2,4–2,6× numpy por etapa; de paso se quitó de `VirtualBass` el rearmado
de dos `PartitionedFIR` por bloque con los armónicos apagados (0,64 de 0,94 ms con 3 parlantes;
experimentos/20 §6). **Sexta (2026-10-08): el generador de armónicos de los graves virtuales**
(`VirtualBass` de `dsp/virtual_bass.py`: banda, rectificador, banda de armónicos, calibración y rampa
de la ganancia en un objeto Rust que posee sus dos `PartitionedFir`, una llamada por bloque), golden
≤ 2,4e-15 contra numpy (sin punto mal condicionado: el rectificador `abs` es continuo), cambio de
motor a mitad de ciclo exacto en los dos sentidos; 2,4–2,7× numpy, pero **1,0× frente a los FIR en
Rust llamados desde Python** (la ganancia ya era de la cuarta etapa); la cadena con `protect` +
armónicos queda en 32× tiempo real con Rust contra 14× con numpy (experimentos/20 §7). **Pendiente
aparte:** la fuente de fase del upmix espacial tiene **dos** discontinuidades en el propio numpy,
las dos superficies donde `_frame` cambia de qué toma la fase del directo: el desempate `el >= er`
(en antifase a igual nivel, 1 ulp de entrada cambia 0,349 su salida: MEDIDO, experimentos/20 §2.3,
y lo fija `tests/test_spatial_rust.py`) y el umbral `et > 0.01 * energy` (donde L + R deja de
usarse y la fase salta a la del canal más fuerte; INFERIDO del código: mover el umbral a 0,011
cambió la salida hasta 0,030 en el golden). Suavizar las dos (una fuente de fase continua)
cambiaría el sonido numpy y pide su propio A/B. **`PC-Ryzen5` necesita rustup antes de su próximo `check.sh`**
(`engine/README.md`). Un miembro de workspace de hatch con maturin no sirve (hatch 1.16.2); se usa
un script de compilación. Antes de esto (2026-10-02): la prueba de concepto de E/S nativa estaba
en preparación
(`probes/17-e-s-nativa-rust/`, d-7c8794-36dde5). **Su parte sin parlantes se midió el 2026-10-09 en
`HP-O16`** (la opción A de research/12 §6, contra un sink nulo de prueba, sin tocar el servicio en uso):
compila sin cambios con `pipewire-rs` 0.10.1, callback en `FF` 83, un solo driver, 0 xruns en 2 × 10 min con
y sin carga, p99,9 del callback ≤ 0,81 % del cuántum, 0 muestras de latencia agregada y nada queda tras
`kill -9` (experimentos/13 §4, research/12 §6.5). **Sigue pendiente** la parte con los 3 Go 4 en
`PC-Ryzen5` (A/B, radio, latencia acústica de A y B); después se borra `probes/17` (d-7c8794-3208b7). Lo pidió el usuario: explorar Rust para la parte
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


**Corregido (2026-10-08):** una etapa Rust que falla *al construirse o configurarse* (no por
pánico: `ValueError`, `TypeError`, `AttributeError` de una extensión vieja) ahora vuelve a numpy en
vez de tumbar la sesión: `backend.built` atrapa cualquier `Exception` en el constructor y en
`set_params` / `set_layout` / `state` / `set_state` / `reset`; `backend.guarded` sigue siendo solo
`RuntimeError` en el bloque. Hechas el 2026-10-08: las etapas 5, 6, 7 (filtros FIR), 10
(crossover) y 11 (graves virtuales). Siguen pendientes la 8, la 9 y de la 12 a la 15 (la
convolución de la 12 ya corre en Rust a través de los FIR).

**Avance (2026-10-09, en el Mac, sin commit en `seamless-transitions`):**
- **El motor quedó idiomático, sin cambiar ningún número** (research/15, d-7c8794-ef6117):
  - lints del workspace, ayudantes de test compartidos, errores con datos y constructores sin pánico;
  - un puente reorganizado: excepciones `EngineError`/`EnginePanic`, módulo declarativo, `.pyi`, `Reader` como clase y `set_params` solo por nombre.
- **Etapas portadas:**
  - **la 8, el decorrelador**: su convolución pasa por `eq.StreamingFIR`; 8 parlantes cuestan 0,27–0,32 ms en Rust contra 0,97–1,16 ms del `np.convolve` anterior (experimentos/20 §8);
  - **la 9, el limitador true peak**: ≤ 1,55e-15 frente a numpy; 1,6–2,5× numpy cuando limita y empate cuando solo detecta (§9).
- **La 12, la cola difusa, no tiene nada más que portar**: 0,026 ms por bloque fuera del FIR con 3 parlantes (§10).
- **La 14, los medidores de sonoridad, portada** (2026-10-09, `HP-O16`): el trabajo por bloque de
  `LoudnessMeter` (energía K-ponderada de cada paso de 100 ms y pico verdadero) en Rust, ≤ 5,54e-13
  frente a numpy; `QualityMeter` con 8 parlantes baja de 1,11–1,19 a 0,49–0,55 ms por bloque
  (2,1–2,4×), el monitor 1,4× (experimentos/20 §13). Un medidor que falla sigue midiendo en numpy, en
  la misma grilla de pasos.
- **La 13, las rampas y el corte, medida y no portada: decide el usuario.** En reposo cuestan
  0,003–0,005 ms por bloque con 8 parlantes; 0,17–0,31 ms solo mientras todas se mueven a la vez, unos
  bloques (experimentos/20 §12). Como objetos sueltos, el puente se comería buena parte de lo
  ahorrado; tiene sentido dentro del `RustMotor`.
- **El puente suelta el GIL** (2026-10-09, pedido por el usuario): cada llamada por bloque copia su
  entrada y trabaja con `Python::detach`, así que el servidor HTTP y el panel no esperan al motor
  (`host/tests/test_engine_gil.py`; costo en experimentos/20 §14, research/15 §B.14). Era §B.14 de
  research/15, que queda aplicado; los otros experimentos de i-7c8794-b4b8b1 siguen.
- **Solo en llamadas largas desde el 2026-10-10:** con otro hilo de Python ocupado, retomar el GIL en cada
  llamada multiplicaba el efecto convoy por 2,7 (490–503 ms por bloque frente a 176–181 ms;
  experimentos/20 §15, MEDIDO en un contenedor). Ahora suelta el GIL una llamada de 65536 muestras o más
  (`gil.rs`, `set_detach_min_samples`), y las del motor por bloque lo conservan, como antes.
- **Sigue** la 15 (`RustMotor`, con su propio plan).
- **Falta cerrar esta tanda:**
  - la re-revisión de los arreglos de la revisión final;
  - el test de navegador `tests_browser/test_panel_engine.py`, que el Mac no puede correr.

  Los pasos están en el estado del plan `docs/superpowers/plans/2026-10-09-rust-idioms-and-decorrelation.md`.
- **Las mediciones del 2026-10-09 son del Mac en uso:** falta repetirlas en `HP-O16`.

**Los menores diferidos del motor Rust**, copiados el 2026-10-08 del ledger local
(`.superpowers/sdd/…/progress.md`, que git ignora), para retomarlos desde cualquier equipo. Ninguno bloquea; la
revisión final de la rama los clasificó como "pueden esperar".
- **Del arreglo de construcción:**
  - `built()` registra el traceback aunque ya haya una falla, así que cada falla sale dos veces en el log;
  - el motivo tiene dos formatos ("Rust failed (RuntimeError: …)" frente a "Rust failed (…)");
  - `built()` también envuelve código de numpy, así que un error de numpy se informa como falla de Rust;
  - los ayudantes de test `_ProxyRust`/`_raiser` y el try/except del cambio de motor están copiados en 3
    archivos.
- **De la tarea 7:**
  - `register` reescribe `_stages` mientras `use()` quita referencias muertas: sería un `ValueError` si
    `register` corriera durante `use()`, cosa que hoy no pasa;
  - el cerrojo `_registered` de `eq.py` no se limpia tras `backend.reset()` (solo afecta a los tests).
- **De la tarea 11:**
  - dos casos de `SWITCH_CASES` no prueban lo que dice su nombre, porque Rust nunca corrió antes del
    cambio;
  - `PartitionedFir::reset` no tiene un test directo en Rust;
  - los `PartitionedFIR` de numpy se siguen construyendo bajo Rust (~0,2 ms por etapa), a sabiendas.
- **De la ronda R6 (2026-10-05):**
  - en el titular del experimento 20, "sin un punto mal condicionado" figura como MEDIDO y debe decir
    INFERIDO;
  - el test del borde de energía mínima no puede detectar una divergencia en ese borde;
  - `_rust_call` de spatial/ambience puede tocar un objeto roto tras una falla (resuelto ya en `eq.py` y
    `virtual_bass.py` por la revisión del 2026-10-08);
  - el falso `NumpyAmbienceExtractor` copia campos privados;
  - no hay test de asignación de memoria después de `set_state`;
  - `test_motor::test_la_ganancia_se_aplica_en_decibeles` divide 0/0 con `AURASYNC_ENGINE=rust`: hay que
    comparar `b` con `0,5·a`.
- **Para usarlo:** después de cada cambio en Rust, `cd host && hatch run engine-build`. Una extensión vieja
  se rechaza por la versión de `capabilities()` y el servicio vuelve a numpy, con el motivo.

### Panel como PWA en GitHub Pages conectado por red local con HTTPS y token por cliente · i-7c8794-b10884
**Estado: A medias (2026-10-04): publicada; falta probarla en teléfonos.** El transporte con token y
ticket, la pantalla de conexión y emparejamiento, la administración de clientes, el service worker
offline al estilo de `thom-music-player` y el workflow de Pages están hechos (75 tests de navegador).
**Publicada el 2026-10-04** en https://fabaindaiz.github.io/bluetooth-sync/ desde la rama
`gh-pages` (solo el sitio compilado y `.nojekyll`, como thom-music-player); cada versión nueva se
vuelve a empujar a mano hasta que `pages.yml` lo haga. Para conectarse, el servicio necesita
`tls: true` en `service.json` (encendido en `PC-Ryzen5` el 2026-10-04). **El ingreso (2026-10-04):**
un **código de conexión** de 13 a 20 símbolos (`connection_code.py` y `web/src/connect/code.ts`,
mismos vectores) lleva la IPv4, el puerto si no es 8443, el código de emparejamiento de 6 dígitos y
20 bits de la huella de la raíz: se escribe una vez y el equipo queda encontrado, comprobado y
emparejado; «Conectar teléfono» → «Generar código» lo muestra, y la terminal lo imprime. Y un botón
«Escanear QR» que lee el QR desde la página (`BarcodeDetector`: Chrome; en Safari y Firefox dice que
se use la cámara del teléfono). La dirección sigue sirviendo en el mismo campo. **Modo demo
(2026-10-04):** «Probar el panel en modo demo» en la pantalla de conexión, o `?demo` en la dirección,
abre el panel sin ningún equipo: `web/src/demo/api.ts` contesta como el servicio desde un estado
capturado del simulado (`web/src/demo/fixture.json`, que escribe `host/scripts/demo_fixture.py` y revisa
`tests/test_demo_fixture.py` con los mismos patrones de privacidad), guarda en memoria lo que se cambia
y contesta «En la demo no hay equipo…» a lo que necesita parlantes o micrófono. Un aviso ámbar arriba
con «Salir de la demo» y el chip DEMO en la barra. Lo pidió el usuario: un panel reutilizable
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

### Modo espacial y disposición auto para N parlantes, con parlantes principales y ambientales · i-7c8794-eb1f78
**Estado: A medias (2026-10-04). Construido y probado en simulación en `PC-Ryzen5`; falta el A/B con
parlantes** ([experimentos/17](research/experimentos/17-modo-espacial-con-3-go-4.md)). Spec:
[superpowers/specs/2026-10-04-spatial-mode-and-auto-layout-design.md](superpowers/specs/2026-10-04-spatial-mode-and-auto-layout-design.md); plan: `docs/superpowers/plans/2026-10-04-spatial-mode-and-auto-layout.md`.

**Qué es:** la disposición `auto` (los principales en ángulos iguales, para cualquier N), el papel de
cada parlante (principal o ambiental), y un modo de render `spatial` (`dsp/spatial.py`) junto al
clásico, con sus perillas explicadas (`spatial_docs.py`, op `spatial_explain`) y la tarjeta Espacial.

**Lo medido en simulación:** con 3 principales, una fuente paneada a un lado sale > 60 dB sobre el
principal más lejano en el espacial, contra 22 dB en el clásico; el carácter lleva el ambiente de ~0 a
> 10 %; el costo es ~3,1 ms por bloque con 3 parlantes y < 8 ms con 8; el clásico queda idéntico bit a
bit.

**Lo que falta:** el A/B ciego (experimentos/17, criterio 8 de 10), DBAP con posiciones x, y, y un
estudio de herramientas abiertas parecidas (se pidió el 2026-10-04 y se detuvo).

### Monitor de audífonos, no sincronizado, junto con los parlantes · i-7c8794-f696d0
**Estado: A medias (2026-10-04). Construido; probado en simulación y contra el PipeWire real de
`PC-Ryzen5` sin parlantes**; falta escucharlo con los 3 Go 4 y medir si agrega cortes. Spec:
[superpowers/specs/2026-10-04-headphone-monitor-design.md](superpowers/specs/2026-10-04-headphone-monitor-design.md)
(d-7c8794-f950ee).

**Qué es:** una segunda salida del motor a cualquier sink que no sea un parlante (audífonos, la salida
del PC): `stereo` (la entrada sin procesar), `mix` (los parlantes plegados a I/D por su ángulo) o
`binaural` (cada parlante como un parlante virtual por el espacializador SOFA de PipeWire, HRTF MIT
KEMAR). `monitor.py`, `monitor_control.py`, op `monitor_set`, `state.monitor`, la tarjeta "Monitor
(audífonos)" en Escuchar; la elección se guarda en `service.json`.

**Lo medido (MEDIDO, `PC-Ryzen5`, PipeWire 1.6.9, 2026-10-04, silencio a −40 dB):** los tres modos
llegan a la salida analógica según `pw-dump` (en binaural, el stream entra al filtro y el filtro sale
a la salida); el filtro aparece en 84 ms; `push` cuesta ≤ 0,19 ms en el hilo del motor; al cerrar no
queda ningún nodo.

**Lo que falta:** (1) escucharlo: que el binaural ubique cada parlante a su lado (la convención de
azimut de SOFA es REPORTADA, de la documentación de PipeWire); (2) con los 3 Go 4 sonando, contar
los cortes del experimento 12 con y sin monitor, y con audífonos Bluetooth en la misma radio
(INFERIDO que puede empeorarlos).

### El software en contenedores · i-7c8794-3b1b92
**Estado: A medias (2026-10-04).** Construido y verificado en `PC-Ryzen5` (d-7c8794-6b1a15;
[08](research/08-integracion-y-plan.md) §6.2.1): imágenes `runtime` y `dev` (la `ml` está escrita y
se construye con el piloto de pistas), `host/container/aurasync-container`, y el estado de los
servicios por D-Bus en vez de `systemctl`.

**Lo que falta:** correr el experimento 12 (cortes) con el servicio en el contenedor y en el host,
con los parlantes; hasta entonces las pruebas con audio siguen en el host. La imagen para la Pi.

### Parlantes virtuales, sesión sin parlantes reales y entrada en caliente · i-7c8794-757041
**Estado: A medias (2026-10-05). La fase 1 y las tareas 8 a 10 de la fase 2 están construidas y probadas
solo con tests y `--simular`, sin validar con parlantes; faltan las tareas 11 y 12 y los experimentos.** Spec: [superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md](superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md);
plan: `superpowers/plans/2026-10-05-virtual-speakers-and-hot-join.md`. Decisiones: d-7c8794-0e5063
(el parlante virtual es `sink: null`), d-7c8794-05bdd6 (la sesión vive sin parlantes reales),
d-7c8794-618666 (el regreso automático).

**Qué es:** poder usar y probar la cadena sin los parlantes reales (en `HP-O16`, solo con audífonos),
y que un parlante real entre y salga con la sesión en marcha.

**Lo hecho (fase 1, en `HP-O16`, sin parlantes ni audífonos todavía):** parlantes virtuales
(`sink: null`); sesiones con los parlantes reales ausentes que sobreviven a perderlos todos;
`outputs.py` (`output_kind`, `Pacer`, `OutputSet`); la op `speaker_add_virtual`; `output` y
`output_kind` en el snapshot; el botón «Agregar parlante virtual» y sus estados en el panel; el
monitor recibe todos los canales. Se validó con `hatch test`, vitest y Playwright contra `--simular`.

**Lo hecho (fase 2, tareas 8 a 10, en `HP-O16`, solo con tests y `--simular`; sin validar con parlantes):**
entrar y salir en caliente (`speaker_join` / `speaker_leave`, `POST /v1/speakers/{name}/join|leave`),
con el corte de 80 + 80 ms; el regreso automático de un parlante `lost` (un intento cada 10 s por
parlante; 3 caídas en 5 minutos y deja de intentar; nunca reconecta el Bluetooth); el lazo de
recalibración que se reinicia solo sobre el conjunto nuevo; en el panel, **Hacer entrar**, **Sacar** y
**Reintentar** por parlante (campo `rejoin` del snapshot), el aviso «la alineación puede haber
cambiado, recalibrá» tras entrar con el lazo apagado, y la calibración que lista a los que quedan
fuera. Enmienda al spec §5: en `separado` también se reconstruye toda la parte real.

**Lo hecho (tarea 11, en `HP-O16`, solo con tests):** el colchón de los parlantes (`cushion.py`,
compartido con el monitor): un solo valor para todos, rellenado a la vez y solo en el fondo de un corte;
en `separado` con relojes distintos no corta (lo dice en `reason`), con freno de 30 s entre cortes y
abandono tras 3 rellenos que no recuperaron el nivel.

**Pausa (2026-10-05, a pedido del usuario):** el desarrollo quedó en pausa con las tareas 8 a 11
construidas y revisadas, sin commit, en el árbol de trabajo de `main`, junto con la igualación de
volumen del monitor y el volumen por el audífono (ya integrados y revisados). La tarea 12 no empezó.

**Lo hecho (tarea 12, 2026-10-06, en `HP-O16`, solo con tests y `--simular`):** el render `direct`
(estéreo puro alineado: sin ambiente, decorrelación, EQ, graves, cola ni Haas; mantiene alineación,
ganancia, silencio, volumen y limitador) y la igualación de volumen entre renders (`render_match.py`
sobre `loudness_match.py`). La referencia es `classic`, recordada; la corrección es lenta y con tope de
±12 dB; se congela con silencio, en un corte, en una calibración y durante un A/B ciego. Así los renders
quedan pre-igualados, y un A/B entre renders compara a igual sonoridad. **La fase 2 está completa.**

**Lo que falta:**
1. **Validar con parlantes** (experimento 19 en `PC-Ryzen5`) y escuchar `direct` frente a `classic`.
2. **Experimento 18** ([experimentos/18](research/experimentos/18-parlantes-virtuales-y-monitor-en-hp-o16.md)),
   en `HP-O16`: bloques en tiempo real con todo virtual, cortes del monitor por minuto y ningún
   `pw-play` hacia un parlante. Necesita que el usuario permita los audífonos WH-CH520 en A2DP.
3. **Experimento 19** ([experimentos/19](research/experimentos/19-entrada-en-caliente-con-3-go-4.md)),
   en `PC-Ryzen5` con los Go 4: apagar y encender un parlante, entrar y salir, el empalme entre el
   fundido de salida y el de entrada, y el desfase antes y después medido con micrófono. Protocolo
   listo, sin medir.
4. Pendientes menores: la PWA publicada rotula un parlante virtual «sin observar» o «perdido» hasta
   reconstruirla; `speaker_add_virtual` repite la elección de rol de `speaker_add`; los colores del
   punto de estado de `virtual` y `absent`.

### Modo simple: controles de lo que se oye, y antes y después · i-7c8794-0ad844
**Estado: Planificado (pedido del usuario el 2026-10-05; necesita spec).**

**Qué es:**
- **Un modo simple que se agrega, sin ocultar ni quitar nada.** La vista experta de la cadena queda
  igual y a un toque. Lo pidió así el usuario, y coincide con la advertencia del rediseño de Sonos de
  2024: no simplificar quitando funciones (`research/11` §4.2, REPORTADO).
- **Controles en términos de lo que se oye**, como IRCAM Spat (VERIFICADO en `research/11` §4.2):
  *Envolvimiento*, *Graves*, *Brillo* e *Intensidad del efecto*. Cada uno mueve varias perillas reales,
  y la vista experta muestra cuáles movió.
- **Antes y después.**
  - Lo audible: pasar al instante de directo a procesado, con la sonoridad igualada. Se apoya en el
    render `direct` de la tarea 12 de la fase 2 y en `loudness_match.py`.
  - Lo visible: el espectro de la entrada contra el de cada parlante, y los números (LUFS, PSR).
  - Así lo hacen las herramientas de especialista (Dirac Live, Audyssey).

**Antes de construirlo:** que las sugerencias de la cadena (i-7c8794-d99df9) se expresen en estos
mismos controles.

### Sonando ahora (MPRIS) · i-7c8794-99f87e
**Estado: Planificado (pedido del usuario el 2026-10-05).**

**Qué es:** una tarjeta en el panel con la aplicación, la canción, el artista y la carátula, y los
controles play/pausa, siguiente y anterior.
- **Fuente:** MPRIS por el D-Bus de sesión. VERIFICADO en esta sesión en `HP-O16`: `busctl --user`
  leyó `PlaybackStatus` de Spotify y le mandó `Play`.
- **Además:** la aplicación que suena hacia el sink `aurasync`, que ya se lee de PipeWire.
- **Usos:**
  - que la igualación de volumen distinga una pausa real del silencio de la música;
  - más adelante, que las sugerencias de la cadena consideren qué está sonando.
- **Límites:** no hay MPRIS en macOS, ni reproductores en la Raspberry Pi sin escritorio.

### Campaña de A/B ciegos de la ecualización antes del release · i-7c8794-d9c64a
**Estado: Planificado (pedido del usuario el 2026-10-05: va antes del próximo release).**

**Qué es:** entender **qué mejora y qué empeora la ecualización**, con A/B ciegos con la sonoridad
igualada (la máquina de A/B y la igualación ya existen), una variable por vez:
- el EQ medido contra sin EQ;
- el presupuesto de realce;
- el techo de agudos sobre 8 kHz (`research/11` §2.1);
- la curva tipo Harman (+6,6 dB de graves bajo 105 Hz, −2,4 dB de agudos sobre 2,5 kHz; REPORTADO,
  Olive et al. 2013).

**Dónde:** con los parlantes en `PC-Ryzen5`, porque el EQ sale de la calibración con micrófono de
cada parlante; los parlantes virtuales de `HP-O16` no tienen respuesta medida. Solo en audífonos se
puede ensayar el procedimiento, no sacar conclusiones del EQ. Cada resultado es MEDIDO (aciertos
sobre ensayos) y se repite (CLAUDE.md).

### Sugerencias de ajustes para la cadena · i-7c8794-d99df9
**Estado: Planificado (fase 2 de i-7c8794-757041; necesita su propia spec antes de construirse).**
Pedido del usuario el 2026-10-05.

**Qué es:** un modo que **sugiere** valores para las perillas de la cadena, cada uno con su porqué y
el número que lo sostiene. **Nada cambia solo:** tú aplicas con un botón (por el corte de 80 + 80 ms)
y puedes deshacer, como en la sincronía sugerida (d-7c8794-2c6f91).

**De dónde salen las sugerencias** (las cuatro fuentes las pidió el usuario):
1. **Los problemas medidos:** las métricas de calidad y los cortes (limitador trabajando mucho,
   aplanamiento, saturación, cortes por causa) → qué perilla bajar o cambiar, y por qué.
2. **La música que suena:** el análisis de la entrada (ancho estéreo, dinámica, cuánto ambiente trae,
   graves) → cuánto ambiente extraer, la decorrelación, el modo espacial, el limitador.
3. **La sala y los parlantes:** lo medido con micrófono (respuesta por parlante, niveles, retardos)
   → EQ, ganancias, crossover y protección de graves. Necesita el micrófono de `PC-Ryzen5`.
4. **Tus preferencias:** lo que eliges en los A/B ciegos y lo que ajustas a mano → hacia dónde mover
   las perillas.

**Orden propuesto:** 1 → 2 → 3 → 4. Empieza por lo que ya tiene datos (`quality.py`, `cuts.py`), y
la 4 necesita historial de A/B. Cada fuente es una spec y un plan.

**Antes de diseñarlo:** que una sugerencia nunca empeore lo medido (cada una dice qué número debería
mejorar, y después de aplicarla se mide si mejoró); la sonoridad igualada al comparar
(`loudness_match.py`, d-7c8794-be46cb).

### Prioridad del hilo del motor · i-7c8794-246f79
**Estado: Hecho (2026-10-09, `HP-O16`), con una medición pendiente en el servicio.**
- Lo construido: `host/src/aurasync/priority.py` y la opción `"engine_nice"` de `service.json`, en -15 por
  defecto (d-7c8794-923eed). El hilo obtiene -15 por RealtimeKit. **VERIFICADO.**
- Lo medido, en experimentos/23 §4.1: con la CPU saturada, el trabajo por bloque baja de un p99 de 52–58 ms
  a uno de 21 ms. **MEDIDO**, con la sonda `probes/25-prioridad-motor`.
- **Pendiente:** contar las entregas tardías con el servicio real bajo carga. Exige arrancarlo, porque crea
  su sink en PipeWire, así que se hace con permiso del usuario.

**Qué es:** subir la prioridad del hilo del motor: `nice` −15 pedido a RealtimeKit, o −11 por
`RLIMIT_NICE` si no está, y opcional (`service.json`). El servicio corre hoy con `nice` +1 y clase `TS`
(VERIFICADO con `ps -L`, experimentos/23 §4), mientras PipeWire pide tiempo real.

**Por qué:** MEDIDO, 179 entregas tardías del motor en 10 min mientras corría la suite completa
(experimentos/23 §4); `motor_ms` era 13,9 de 85,3 ms, o sea que faltó CPU.

**Cómo se mide:** con la suite de tests como carga, los bloques tardíos en 10 min con la prioridad y sin ella.

### El A/B empareja el volumen con la ganancia neta · i-7c8794-50caa5
**Estado: Hecho (2026-10-09).** `Service._ab_measure` usa `meter.net_lu`. Test: con la música 12 dB más fuerte mientras suena B y dos presets iguales, la compensación queda en 0; antes era -12,06 dB (`test_the_music_getting_louder_between_a_and_b_is_not_a_difference`).
- **El problema:** experimentos/23 §6.4. `match_loudness` compara la sonoridad de la salida mientras suena A
  con la de la salida mientras suena B, en momentos distintos de la música. Por eso la compensación saltó
  de -1,86 dB en A a -5,17 dB en B en un minuto. **MEDIDO.**
- **El arreglo:** medir la ganancia neta (sonoridad de la salida menos la de la entrada en la misma ventana),
  como ya hace `render_match`, y compensar con esa diferencia, con un test que use música de volumen
  variable.
- **Choca con:** nada. El contrato de `state.ab` no cambia.

### Presets en el panel: renombrar, confirmar el borrado y ficha expandible · i-7c8794-dcbd24
**Estado: Hecho (2026-10-09), revisado.** Lo construido:
- la operación `preset_rename` (`PATCH /v1/presets/{name}`), y la respuesta de `presets` lleva `chain`;
- un diálogo para renombrar;
- confirmar antes de borrar (el foco empieza en «Cancelar»), sin quitar el «Deshacer»;
- la ficha «Ver/Ocultar» con la configuración.

**Ajuste del 2026-10-10:** en el teléfono los cuatro botones partían cada fila en dos y subían el costo de
navegación 1,4. Por decisión del usuario, «Renombrar» y «Borrar» pasaron dentro de la ficha que abre «Ver»
(research/10 §7.5).

Pruebas: 139 tests de servicio y 12 de navegador. Quedan dos menores:
- Escape todavía cierra el diálogo mientras el renombre está en curso;
- si `api.raw` lanza un error, los botones quedan deshabilitados.

**Antes era:** Planificado. Lo pidió el usuario el 2026-10-09, después de la escucha de presets
(experimentos/23 §6). Son tres cosas:
1. **Renombrar** un preset. Hace falta una operación nueva (`preset_rename`), que también renombre su parte
   de cadena en `presets-chain.json`.
2. **Confirmar antes de borrar.** Hoy, al borrar aparece «Deshacer» durante 10 s, sin confirmación, y es
   fácil borrar sin querer.
3. **Una ficha rápida expandible** por preset con su configuración: render, difusión, ambiente,
   decorrelación, EQ, graves, limitador y los valores por parlante.

Choca con: el grupo 1 de la auditoría del panel (research/10 §10), que toca la misma pantalla. Hay que
hacerlas juntas o en orden.

### El monitor en la observación de radio y las mediciones de protocolo · i-7c8794-7505b5
**Estado: Planificado.** Lo pidió el usuario el 2026-10-09 (d-7c8794-7f1790).
- **Hoy** `state.radio` (`radio.py`, el registro de radio) solo sigue a los parlantes (`speakers`), así que
  del monitor no se sabe su códec, su señal ni sus descartes. **VERIFICADO** en `Service.radio_view`.
- **Lo que hay que hacer:**
  - que el registro de radio y las mediciones de protocolo de los parlantes (descartes, retransmisiones,
    códec y tasa de bits, latencia A2DP, señal) cubran también al dispositivo del monitor, con su propia
    entrada (`monitor`) y marcado como estéreo y no sincronizado;
  - que el registro de cortes distinga los del monitor de los de los parlantes;
  - que el panel lo muestre junto a los parlantes.
- **Por qué importa:**
  - hoy las pruebas A/B se hacen por el monitor (experimentos/23), así que sus cortes se confunden con los del
    motor;
  - el `pw-play` del monitor ya traía 39 errores acumulados (experimentos/13 §4) y nadie los veía como
    enlace.
- **Choca con:** el registro de radio necesita su permiso (`btmon`/HCI) y hoy mira la dirección de los
  parlantes. El monitor es otro dispositivo, en otro perfil, y a veces otro adaptador.

### Parlantes estéreo, grupos estéreo y ajustes por salida · i-7c8794-35655d
**Estado: Planificado.** Lo pidió el usuario el 2026-10-09, en la misma línea que d-7c8794-7f1790 (el monitor
como un enlace más).
- **Hoy** cada parlante de la instalación recibe **un canal** (mono, con pan, ambiente y retardo propios). El
  monitor es la única salida estéreo, y lo es por sus modos (`stereo`, `mix`, `binaural`). **VERIFICADO** en
  la forma de `Parlante` y en el monitor.
- **Lo que se pidió:**
  1. **Configurar un parlante como estéreo**: que reciba dos canales (L/R) en vez de uno. Sirve para un
     Charge 6, unos audífonos o un parlante con dos vías. Lleva su propio reparto (qué recibe cada lado), su
     downmix cuando hace falta y sus ajustes (balance y ancho).
  2. **Un grupo estéreo dentro de la herramienta**: dos parlantes que actúan como un par L/R. El grupo comparte
     alineación y ganancia, se ubica como una unidad en el plano y en los layouts, y en el panel se mueve como
     una sola salida.
  3. **Usar un grupo estéreo del propio dispositivo**, como el modo estéreo de JBL entre dos parlantes, si se
     puede manejar desde Linux. **Pregunta abierta**: no se sabe si ese modo se puede activar o detectar sin
     la app de JBL (**INFERIDO**: probablemente no). Se investiga antes de diseñar (research/01).
  4. **Otros ajustes por salida que sirven a lo mismo**: el mapa de canales, el códec y la tasa de bits si el
     dispositivo deja elegirlos, y el tipo de salida (mono, estéreo, grupo) en el panel. Cada salida estéreo
     queda medida como un enlace más (i-7c8794-7505b5).
- **Choca con:**
  - el motor de hoy genera un canal por parlante, y el layout, el reparto espacial y la calibración por
    parlante asumen mono. Una salida estéreo cambia el contrato (`speakers[]`) y la calibración (dos canales
    que medir en un mismo enlace);
  - es diseño de producto, así que va con su spec y sus preguntas antes de construir.

### Monitor binaural de audífonos · i-7c8794-541555
**Estado: Planificado (idea).** experimentos/23 §6.3: con los audífonos, el procesado no gana claramente
a `direct`. El monitor `mix` suma los 4 parlantes virtuales en 2 canales, y esa suma de copias retardadas y
filtradas colorea el sonido con un filtro peine (**INFERIDO**). Un monitor binaural, que ubique cada
parlante virtual alrededor de la cabeza con HRTF en vez de sumarlos, juzgaría el envolvimiento con
audífonos de una forma más justa. **Choca con:** el costo del HRTF por parlante en el motor, y elegir un
conjunto de HRTF con licencia libre (preguntar antes de traer datos de terceros).

### El monitor vuelve solo tras un cambio de perfil Bluetooth, comprobado en HP-O16 · i-7c8794-6c2a37
**Estado: Hecho (2026-10-09, `HP-O16`).** experimentos/23 §7.1: al desconectar y reconectar los audífonos, el monitor volvió solo, sin cambiar de modo. Quedan dos cosas:
- medir cuánto tarda (la línea `lost` no tiene hora);
- rechazar `monitor_set` hacia un dispositivo cuyo micrófono ya está abierto.
micrófono de los propios WH-CH520 los pasó a manos libres, PipeWire rehízo su sink y el monitor quedó mudo
diciendo que llegaba. Lo construido el 2026-10-09: el monitor vuelve a abrirse solo cuando pierde su destino
(`MonitorController.watch`), y el micrófono Bluetooth de una salida en uso nunca se abre (`microphones.py`).
**Falta, en `HP-O16` y con permiso del usuario** (toca los audífonos y reinicia el servicio): reiniciar el
servicio, entrar a Calibrar con el micrófono de los audífonos elegido (debe negarse y el monitor seguir
sonando), y provocar un cambio de perfil de otra forma (desconectar y reconectar los audífonos) midiendo
cuántos segundos tarda en volver a sonar. **Choca con:** nada; queda fuera negar `monitor_set` hacia un equipo
cuyo micrófono ya está abierto (el lazo o una calibración con él), que se decide aparte.

### Escucha de la etapa 1 de transiciones sin corte · i-7c8794-93f50c
**Estado: Hecho (2026-10-09, `HP-O16`).** experimentos/23 §5.1: el fundido no se oye en las rampas ni en los retardos (80 y 200 ms), y el corte sí; el peine y los +3 dB en graves no se oyen. El usuario pidió la perilla de 0 a 500 ms con 80 por defecto.
El protocolo (antes de escuchar) era: cambios de motor ocultos, A/B `crossfade` contra `cut`, la pregunta del
peine (10–30 ms a 80 y 200 ms) y el posible +3 dB en graves bajos, con el usuario en `HP-O16`.

### Transiciones sin corte, etapa 2: etapas con estado por `Crossfaded` · i-7c8794-a0a68b
**Estado: Hecho (2026-10-09; falta escucharla en el A/B final, experimentos/23 §6.5).** Las etapas con estado
(difusión, graves, limitador de la misma latencia, EQ, decorrelador y extractor) cambian por fundido de
objetos enteros (`Crossfaded`): la etapa nueva se calienta a la sombra y se mezcla con la vieja, y
`chain_set` responde `apply: "crossfade"`. Siguen cortando un limitador que cambia de latencia y el render
(etapa 3). Reglas del controlador: el limitador funde siempre `equal_gain`; el decorrelador y los retardos,
siempre `equal_power`; las rampas se sostienen durante WARM; las perillas vivas se re-aplican a una etapa
pendiente. `preset_load` y `ab_play` van por el fundido si `pide_corte` es falso; el A/B decide una vez para
su par. Spec §5. Costo: correr dos copias durante el traspaso.

**Menores diferidos de la revisión de las tareas 3 a 5 (2026-10-09):**
- con `assignment=mix`, la asignación del decorrelador se calcula dentro del bloque de audio: 13,5 ms con 8
  parlantes, contra 0,1–0,5 ms con 3–4. Hay que calcularla en `aplicar_cadena`;
- una reasignación que deja pendiente un cambio de pan espera un corte que ya casi no llega. Que un fundido la
  lleve;
- falta un test de cambio de banco más encendido o apagado en la misma transición (el revisor lo corrió: 4
  pasan);
- renombrar `test_choosing_mix_goes_through_the_cut`;
- el comentario de `service.py` ~916 sobre el EQ en el fondo del corte quedó viejo.

### Transiciones sin corte, etapa 3: el render · i-7c8794-da4172
**Estado: A medias (construida el 2026-10-10; falta escucharla).** Cambiar de render (clásico, espacial, front,
direct) con fundido en vez de corte: una rama entera nueva (`render_branch.py`) se calienta a la sombra hasta
1 s y se mezcla por parlante con la que se va. La forma va por par (`equal_gain` entre `spatial` y `front`,
`equal_power` en el resto; experimentos/23 §9.1, MEDIDO fuera de línea). Desvío del plan: las líneas de retardo
van por rama, no compartidas. **Falta:** la escucha de experimentos/23 §9.2 en `HP-O16`. **Choca con:** que el
render nuevo puede tardar hasta 1 s en oírse (el calentamiento).

### Transiciones sin corte, etapa 4: el colchón por estiramiento · i-7c8794-1b74ad
**Estado: A medias (construida el 2026-10-09; falta escucharla).** El colchón de los parlantes y el del monitor
se rellenan estirando el audio (`dsp/stretch.py`, adaptativo de 0,1 a 0,5 %, perillas
`transition.start_stretch_ppm` y `max_stretch_ppm`) en vez de con silencio; el silencio (y el corte de los
parlantes) quedan como último recurso bajo un cuantum. Fuera de línea: THD+N de −90,7 a −112,6 dB y la
frecuencia exacta (experimentos/23 §8.1). **Falta:** la escucha de experimentos/23 §8.2 en `HP-O16`, con
permiso del usuario. **Choca con:** nada; seguir el reloj de forma continua (un lazo como `ClockLoop`) es el
paso siguiente, en su propio spec.

### Un intervalo de cambio del GIL más corto en el servicio · i-7c8794-46d296
**Estado: Propuesto (2026-10-10).** Con un hilo de Python ocupado al lado, el `Motor` tarda 176–181 ms por
bloque aunque ninguna llamada a Rust suelte el GIL (el plazo es 85,3 ms): numpy lo suelta en sus
operaciones largas y cada una paga un intervalo de cambio (5 ms). Con `sys.setswitchinterval(0,0005)` baja a
16,4–16,6 ms, y el otro hilo pierde un 15 % de su avance (experimentos/20 §15, una corrida en un contenedor).
**Falta:** repetirlo, saber qué hilos del servicio corren Python de forma sostenida, y medirlo en `HP-O16`
con el servicio; después, decidir si va como opción de `service.json`. **Choca con:** que es un cambio de
todo el proceso.

### Quitar los nombres de los equipos de los mensajes de commit · i-7c8794-df6774
**Estado: Planificado (el usuario lo dejó para después, 2026-10-10).** Los mensajes de commit nombran los
equipos (`HP-O16`, `PC-Ryzen5`, el Mac): son 29 líneas, y el usuario los considera datos privados. Hay que
reescribir los mensajes como se hizo con las líneas de atribución (registro del 2026-10-10): filter-branch
sobre todas las ramas, comprobar que los árboles quedan iguales, y force-push. Los otros equipos tienen que
resetear sus clones después.
- **Por decidir:** con qué se reemplazan los nombres (un alias genérico o nada), y si también se limpian los
  archivos. Esos nombran los equipos en 194 lugares, porque la regla es anotar en qué equipo se midió, y
  tienen rutas `/home/fadiaz` y direcciones Bluetooth (d-7c8794-8374e1).
- **Desde ahora:** los mensajes de commit nuevos no nombran los equipos.

### `test_a_signal_restores_it` se cuelga con la suite desacoplada de la terminal · i-7c8794-d5c5d6
**Estado: Planificado.** `tests/test_radio_service.py::test_a_signal_restores_it` se queda esperando cuando
pytest o `scripts/check.sh` corren en segundo plano (sin terminal), y pasa en primer plano. Mientras tanto
la suite completa se corre con `--deselect` de ese test y el test aparte, en primer plano (etapa 1 de
transiciones sin corte, 2026-10-08). Falta ver por qué: el test se manda a sí mismo `SIGINT`/`SIGTERM`, y
INFERIDO que, desacoplado, la señal no llega al hilo que la espera o la toma el grupo de procesos.

### La ventana del A/B desde el fin real de la transición · i-7c8794-ee3f38
**Estado: Planificado (menor).** `_ab_measure` vuelve a esperar mientras `en_corte`, pero solo se consulta
cada 0,5 s (`QUALITY_S`): la ventana de 3 s puede empezar hasta ~0,2 s antes del fin de una transición
larga (`fade_ms` 500 con un lote pendiente). Antes del arreglo eran ~0,7 s. INFERIDO por la re-revisión
del 2026-10-09; el test llama a `_ab_measure` a mano y no modela el sondeo. **Recomendación:** que el motor
anote el instante (o el bloque) en que terminó su última transición, y la ventana cuente desde ahí.

### Menores diferidos del Rust idiomático y del port del 2026-10-09 · i-7c8794-a75d67
**Estado: Planificado (menores).** Salen de las revisiones por tarea y de la revisión final del plan
`2026-10-09-rust-idioms-and-decorrelation`. La revisión final los clasificó como «pueden esperar»;
ninguno cambia el audio.
- **Del lint (tarea 1):**
  - los comentarios de `cast_sign_loss`/`cast_possible_truncation` en `engine/Cargo.toml` afirman cosas no comprobadas;
  - `expect(struct_field_names)` en `fir.rs` podría ser un cambio de nombre;
  - los `# Panics: Never in practice` son vagos.
- **De los tests (tarea 2):** la referencia `numpy_pairwise` no comprueba el orden de su rama `n > 128` (solo con enteros).
- **De la API del núcleo (tarea 3):**
  - el texto de `OutOfRange` no se comprueba en un test de Rust;
  - el formato de `BadShape`/`OutOfRange` se repite en 4 enums;
  - el caso de filas se lee «fdl rows: 2 values where 3 are needed»;
  - `fft.rs` afirma que el plan es idéntico entre planners sin citar la comprobación bit a bit de 12,4 M muestras.
- **Del puente (tareas 4 y 5):**
  - líneas largas sin reenvolver en `backend.py`, `engine/README.md`, `lib.rs`, `Cargo.toml` y `host/pyproject.toml`;
  - `read.__module__` queda en `aurasync_engine.aurasync_engine` y `dir()` muestra el submódulo;
  - nombres de test que todavía dicen `_is_a_runtime_error_`;
  - `EngineError::Internal` no se alcanza en ningún test;
  - `match="tap"` es suelto;
  - falta un comentario de por qué solo `RuntimeError` descarta el `Reader`.
- **Del decorrelador (tarea 6):**
  - el test del pánico llama a `backend.reset()` y no recorre el camino del fondo del corte;
  - `test_a_new_bank_starts_from_silence_in_both_engines` no comprueba la cola en silencio;
  - el test de pertenencia no usa `AURASYNC_ENGINE=rust`;
  - un import queda dentro de una función.
- **De la tanda final (2026-10-09), para decidir o arreglar con la re-revisión:**
  - `OutputMismatch` de `SpatialError`/`AmbienceError` sigue saliendo como `ValueError`, aunque la tanda pasó los de FIR y del limitador a `EngineError` (mismo caso, no se amplió);
  - **pregunta de diseño:** un pánico plantado en `set_state` del limitador, al pasar a Rust, deja a numpy seguir desde su propio estado y no desde reposo. El test lo fija así; ¿debería empezar desde reposo?;
  - `probes/README.md` dice que un probe se borra al anotar su resultado, pero la tanda guardó probes nuevos como fuente de experimentos/20 §9. Hay que decidir si quedan;
  - el test de navegador pasó con un plugin de Playwright provisorio que apunta al headless shell 1243 en caché, porque el build 1223 no está en el Mac. Falta correrlo con la instalación normal.
- **De la re-revisión de la tanda final (2026-10-09; los 13 puntos quedaron resueltos):**
  - `probes/20-costo-sinc-rust/producto_aislado/src/main.rs`: `vorig` escribe en `outs[3]` después de `v3`, así que el «identical … v3 true» final comprueba `vorig` y no `v3`. Ninguna cifra anotada depende de eso. Además, su `Cargo.toml` dice «cinco formas» y son seis;
  - la docstring de `host/tests/test_limiter_rust.py` sigue diciendo «un bloque en silencio y numpy desde reposo», pero el test fija silencio hasta el corte, y en el caso `set_state`, numpy desde su propio estado.
- **Del limitador (tarea 7):**
  - el test con bloques de 1 muestra cubre solo las primeras 11 000;
  - `reduction_db` queda viejo en la ventana de silencio tras una falla de Rust.

### Experimentos de rendimiento del motor Rust · i-7c8794-b4b8b1
**Estado: Planificado.** Salen de la investigación de Rust idiomático
([research/15](research/15-rust-idiomatico-y-buenas-practicas.md) §B.11–16). Cada uno cambia el
rendimiento y no la forma, así que se mide antes de tocarlo (en `HP-O16` o `PC-Ryzen5`, con `nice` si
el servicio suena):
- buffers planos en lugar de `Vec<Vec<f64>>` por parlante;
- lazos con iteradores en vez de índices en ambiente y espacial;
- una salida `out=` que evite el arreglo numpy nuevo por bloque;
- ~~`Python::detach` durante el bloque, para no retener el GIL~~: **hecho el 2026-10-09** (pedido por el
  usuario; copia la entrada; costo medido en experimentos/20 §14, entrada del motor Rust arriba), y desde
  el 2026-10-10 solo en llamadas largas, por el efecto convoy (§15);
- los denormales en los suavizados recursivos tras un silencio largo; un arreglo iría en numpy y en
  Rust a la vez;
- `target-cpu=native` en `engine-build`.

### Cuatro tests fallan en macOS · i-7c8794-437907
**Estado: Planificado (menor).** MEDIDO en el Mac el 2026-10-09 (suite completa: 1905 bien, 4 fallas),
sin relación con la rama de transiciones:
- `test_sonido.py::test_room_is_that_of_the_fullest_pipe` y `::test_room_of_the_combined_stream_counts_its_channels`:
  `sonido.tamano_de_tuberia` usa `F_GETPIPE_SZ`, que solo existe en Linux, y devuelve `None`.
- `test_spatial_rust.py::test_numpy_itself_changes_0_349_for_one_ulp_at_the_anti_phase_tie[60.0|150.0]`:
  en el Mac (arm64) el FFT de numpy da 0,361 y no 0,349, lo que el propio docstring del test prevé.

Por esto, `scripts/check.sh` no pasa en el Mac. Además, en el Mac `cargo clippy`/`cargo test` de `engine/`
toman un Python 3.11 del PATH y PyO3 (abi3-py312) no compila. Se resuelve con
`PYO3_PYTHON=/opt/homebrew/bin/python3.14`, que `check.sh` podría exportar desde `$PY`. **Recomendación:** marcar los dos primeros como solo-Linux
(`skipif`), porque el servicio corre solo en Linux. En el tercero, dejar el número exacto solo para Linux y
comprobar en todas partes lo que importa (que no baje de 1e-9).

### Cambio del reproductor de salida con dos streams superpuestos · i-7c8794-43c2a5
**Estado: Planificado.** Cambiar de `ReproductorCombinado` a `separado` (o al revés) sin silencio, con los dos
streams sonando superpuestos un instante. Con qué choca: d-7c8794-a41ec9 (`dont-move`, un reloj por salida).

### Cambio de volumen por AVRCP sin corte · i-7c8794-46b4d7
**Estado: Planificado. Necesita el JBL.** Primero se mide la latencia de cada paso del volumen AVRCP; sin ese
número no se sabe si un fundido puede esconderlo. INFERIDO hasta medirlo.

### Rendimiento del motor medido, con su informe en el panel junto a los demás datos · i-7c8794-be46cb
**Estado: Planificado.** Pedido del usuario el 2026-10-05, mientras se construían los parlantes
virtuales (i-7c8794-757041).

**Qué es:** medir cuánto le cuesta al motor cada bloque, de forma que el número se pueda repetir.
Incluye:
- **los percentiles** p50, p99 y máximo, no solo el promedio;
- **el costo por etapa** de la cadena;
- **el margen frente al presupuesto** del bloque;
- **la CPU del proceso**;
- **la relación con los cortes** de `cuts.py`.

El informe se muestra **en el panel junto a las métricas de calidad y las mediciones de
sincronía**, y se guarda con ellas (equipo, versiones, ajustes de la cadena), así un cambio de
rendimiento se puede comparar entre sesiones y entre equipos.

**Lo que ya existe:**
- el panel muestra el tiempo medio por bloque, suavizado, y cuántas veces más rápido que el tiempo
  real procesa el motor (`snapshot._health`: `motor_ms`, `realtime_x`, en Diagnóstico);
- `probes/18-costo-de-la-cadena/costo.py` mide el costo con todo encendido, pero fuera de línea.

Falta unir las dos cosas en una medición dentro de la sesión, con su informe.

**Antes de diseñarlo:**
- decidir qué se guarda y dónde (junto a `measurement_save`);
- decidir cómo se mide sin cargar el hilo del motor: el costo de medir, también medido;
- **anotar la carga del equipo mientras se mide.** El 2026-10-05, en `HP-O16` (enchufado),
  `test_interpolation.py::test_it_costs_far_less_than_the_formula` falló de forma intermitente
  (entre 6,3× y 7,6× contra el 8× que pide) **también sobre `main` sin cambios**: pasó 1 de 5 veces
  con otros procesos corriendo tests en paralelo (MEDIDO). Al principio se lo atribuí a la batería,
  y era falso: el adaptador estaba conectado. Un informe de rendimiento que no registra la carga del
  equipo no se puede comparar entre sesiones, y ese test necesita más margen o correr aislado.

### Igualación del render direct: lo que encontró la revisión de la rama · i-7c8794-353aff
**Estado: Planificado (2026-10-07).** La revisión de la rama `auracast-supermini-f1`, en un contexto limpio
antes del merge, dio "OK para el merge", sin bloqueantes. Hay que arreglar, en un cambio aparte:
- **P1** (`render_match.py`, la clave de la referencia): la clave no incluye la ganancia de cada parlante.
  Si el usuario sube un parlante en `direct`, la igualación baja el makeup de todos y deshace en parte el
  ajuste. Se arregla con las ganancias redondeadas dentro de la clave, o sacándolas de la medición, como el
  volumen.
- **P2:** al cambiar la clave (silenciar, o P1), la referencia adopta la ventana de 3 s que todavía es del
  estado anterior. Silenciar y cambiar de modo a los 2 s deja `direct` guardado ~1,4 dB alto. Se arregla
  reiniciando la ventana al cambiar la clave.
- **Menores:**
  - P3: la primera visita a `direct`, o una sesión que arranca en `direct`, suena sin igualar mientras el
    panel dice "mismo volumen"; hay que mostrar `unmeasured` o sembrar una estimación;
  - P4: en `direct`, las métricas de decorrelate/diffuse/bass siguen diciendo `active`, y falta documentar
    `spatial {render, makeup_db}` en `control-api.md`;
  - P5: los tonos de identificación se miden como parte del modo; hay que usar `hold` mientras suenan;
  - P6: nada del producto llama todavía a `bumble_fixes.apply()`; el backend Auracast tiene que hacerlo.

### Estabilizar el test de costo de la lectura sinc · i-7c8794-a439a5
**Estado: Hecho (2026-10-07).**
- **Qué se hizo:** las dos funciones se miden intercaladas (mínimo de 15 rondas), y el test pide ≥ 5× en
  quieto y ≥ 1,5× en rampa.
- **Por qué esos umbrales:** con el equipo tranquilo, en `HP-O16`, se midió 9,5–10,1× y 2,8–3,2×.
- **El número exacto** queda para `probes/18`.
- **El otro test que fallaba al azar en el mismo cierre**, `test_clients_cli`, era un **defecto del producto**:
  el id de la solicitud de emparejamiento (`secrets.token_urlsafe`) empezaba con `-` una de cada 64 veces, y
  `aurasync clients approve <id>` lo leía como una opción. Ahora el id nunca empieza con `-`
  (`pairing._request_id`), con su test.

**Antes (2026-10-07):** `tests/test_interpolation.py::test_it_costs_far_less_than_the_formula`
falla cuando el equipo está cargado: compara un tiempo contra un umbral (8×). **Contado en el registro de
sesiones el 2026-10-07: se menciona 6 veces**, en sesiones del 03, 04, 05 y 07 de octubre. El 2026-10-07 dio 1
fallo de 1513 con dos subagentes trabajando en paralelo, y corrido solo pasó 2 de 3 con load 7,9. Cada vez
cuesta una explicación en el cierre y oculta si un fallo es real.

**Opciones** (se decide al hacerlo):
- comparar el mínimo de varias repeticiones en vez de una sola medición;
- bajar el umbral a lo que se mide con carga (~6×) y dejar el 8× para `probes/18`;
- marcarlo como test de rendimiento y correrlo aislado en `check.sh`.

### Estimador base de sincronía alimentado por mediciones continuas y puntuales de varios micrófonos · i-7c8794-737d4e
**Estado: A medias (2026-10-03). Pasos 1 y 2 hechos en simulación, en `PC-Ryzen5`**; los pasos 3 a 5
y las pruebas con parlantes, pendientes. Spec: [superpowers/specs/2026-10-03-sync-estimator-design.md](superpowers/specs/2026-10-03-sync-estimator-design.md); plan de los pasos 1–2:
`docs/superpowers/plans/2026-10-03-sync-estimator-steps-1-2.md`.

**Qué es:** un estimador en el servidor que junta todas las mediciones de sincronía (el micrófono
del servidor hoy; los celulares del panel después, en medición puntual o continua) y **sugiere**
retardos absolutos que el usuario aplica desde el panel (d-7c8794-2c6f91). Extiende
i-7c8794-4745b4 (calibrar sin un micrófono central) e i-7c8794-e3e40d (la sonda).

**Lo hecho (SIMULADO):** `sync_measurement.py`, `sync_sim.py`, `sync_methods.py` (mínimos
cuadrados robustos: deriva, saltos que se creen al repetirse, base por micrófono, anclas y votos),
`sync_estimator.py` (hilo propio, O(1) para el motor), `knob_docs.py` y `sync_docs.py` (cada
perilla con recomendación, cómo suena y figura SIMULADA, y el texto "en conjunto"), las operaciones
`sync_state`, `sync_set`, `sync_apply`, `sync_explain`, y la tarjeta "Sincronía sugerida" del
panel. Criterios en dos semillas y con ventana de 10 y 20 min: deriva de 22 ppm seguida con
< 0,25 ms de error; un salto de 6,52 ms creído con la segunda medición; micrófonos que oyen 2 de 3
combinados; un micrófono con sesgo constante no mueve nada.

**Paso 3 hecho (2026-10-04, SIMULADO):** «Medir desde aquí» en la tarjeta Sincronía sugerida.
`probe_ring.py` guarda la sonda que salió a cada parlante con su hora (anillo fijo de 30 s); las
operaciones `sync_time`, `probe_reference` y `sync_measure`; `web/src/sync/` mide en el navegador
(`fft.ts`, `measure.ts`: el mismo `probe_measure.measure`, igual a Python dentro de 0,01 ms en
`web/test/measure.test.ts` con la fixture que escribe `tests/test_probe_measure_fixture.py`) y
`fromHere.ts` hace el flujo (reloj por 8 idas y vueltas, 8 s de micrófono sin procesamiento de voz a
48 kHz por un AudioWorklet, referencia, medición, envío). De punta a punta en el navegador, con la
grabación hecha de la sonda retrasada 20, 27,5 y 24 ms, el servidor recibe esas diferencias dentro de
0,05 ms (`tests_browser/test_panel_sync_here.py`). El anillo cuesta 0,04 ms por bloque en el hilo
del motor con 3 parlantes y 0,10 ms con 8 (MEDIDO, `PC-Ryzen5`, mediana de 500 bloques de 4096), y
ocupa 17 MB (46 MB con 8).

**Lo que falta:** probar el micrófono real del teléfono (Chrome Android y Safari: si respetan apagar
el procesamiento de voz), el límite de una medición por segundo por cliente, paso 4 (celulares en
continua, micrófono movido), paso 5 (`tracks` y `kalman` con su tabla comparativa), la calibración con
ruido como objetivo, y las pruebas con parlantes (spec §7).

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

**Desde el 2026-10-03** lo continúa i-7c8794-737d4e: un estimador que junta las mediciones de varios
micrófonos (los celulares del panel) en vez de depender de uno.

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
