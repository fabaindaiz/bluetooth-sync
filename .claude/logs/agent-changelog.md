# Registro de sesiones

Cada sesión que cambia algo agrega su entrada **arriba**, justo debajo del
separador que sigue, con el formato que está al final del archivo. Las sesiones
paralelas no se ven entre sí. Lo único que avisa a la siguiente es lo que salió
mal y lo que quedó pendiente.

---

## 2026-09-26 · s-7c8794-f44ace — Software de audio del PC (07) y plan de integración e I+D (08)

**Qué.**
- **Documento 07 nuevo**, sobre el software del PC:
  - la captura del audio del sistema en Linux, macOS y Windows;
  - qué fuentes traen multicanal;
  - los algoritmos y las herramientas de upmix;
  - el ruteo de canales;
  - qué ven las apps con un sink multicanal;
  - la latencia, el lip-sync y el desfase tolerable entre parlantes;
  - la entrada de audio de Bumble;
  - cómo repartir los canales entre los 3 Go 4 y el Charge 6.
- **Documento 08 nuevo**, sobre la integración:
  - niveles de huella N0–N4 por mecanismo;
  - dónde vive el emisor (PC, Pi como tarjeta USB, nRF5340, comercial, red);
  - el lazo de reloj;
  - el stack y la arquitectura de una CLI (`jblsync`, nombre provisional);
  - el plan P1–P3, M0–M5 y Fase 3;
  - las decisiones que quedan abiertas para el usuario.
- **Roadmap:**
  - entradas nuevas P1 (i-7c8794-fd5f03), P2 (i-7c8794-cb208f), P3
    (i-7c8794-346d45), la herramienta CLI del MVP (i-7c8794-2fe665) y la Fase 3
    (i-7c8794-80f3ac);
  - la entrada de upmix (i-7c8794-c7ccb9) quedó corregida;
  - el diagrama de etapas y la tabla de la invariante quedaron actualizados.
- **Otros documentos:**
  - una corrección en 03 §2;
  - README, `CLAUDE.md` y `references.md` actualizados con 07 y 08.

**Archivos.** `docs/research/07-software-de-audio-en-el-pc.md`,
`docs/research/08-integracion-y-plan.md`, `docs/research/03-…md`,
`docs/research/README.md`, `docs/roadmap.md`, `docs/references.md`, `CLAUDE.md`,
`.agents/tracking/candidates.md`.

**Por qué.** El usuario pidió dos cosas:
1. investigar el software del PC para surround y canales (fuentes, captura de
   cualquier audio, compatibilidad);
2. evaluar cómo integrar audio y Bluetooth en herramientas que modifiquen lo
   mínimo el sistema, con un plan de I+D.

**Arquitectura.** ✅ Cumple.
- No hay código de producto. Todo el diseño de 08 está marcado INFERIDO, y las
  entradas del MVP quedan bloqueadas por la decisión de seguir (d-7c8794-346170).
- Se aplicaron las tarjetas *cleanup-belongs-to-the-supervisor*,
  *detect-by-observation-not-build-flag*, *derive-state-from-one-clock*,
  *close-the-loop-in-the-actuators-frame* y *fail-closed-defaults* (08 §2.4). Sus
  chequeos quedan como criterios de P1, P2 y M2, porque todavía no hay nada que
  ejecutar.

**Qué salió mal en el camino.**
- Dos agentes se contradijeron sobre el upmix por defecto de PipeWire: uno leyó
  "psd por defecto" en la documentación, el otro "NONE" en el código. Se revisó
  `audioconvert.c` y `channelmix-ops.c` del tag 1.6.9:
  - el flag `channelmix.upmix` arranca en true, pero el método arranca en `none`
    y los cutoffs en 0;
  - con eso no se genera ningún canal.
  El 07 se corrigió a "apagado en la práctica". **La documentación de
  pipewire-props muestra los valores de ejemplo como si fueran los de por
  defecto**: no hay que fiarse de ella para los valores por defecto.
- gitlab.freedesktop.org y la ArchWiki bloquearon a los agentes con Anubis, así
  que usaron el espejo de GitHub y `action=raw`.

**Qué quedó pendiente.**
- Las decisiones de 08 §8 (sistema de referencia, stack, comprar una Pi Zero 2 W
  para P3, nombre). **Ninguna está registrada en `decisions.md`.**
- P1 se puede hacer ya en el Mac, sin hardware.
- El experimento 1 de 07 (BlackHole o un tap con 6 canales) tampoco necesita
  parlantes.

**No verificado.**
- Todo 07 y 08 es investigación documental. Los agentes leyeron las fuentes, y
  esta sesión solo volvió a abrir el código de upmix de PipeWire.
- Nada se midió.
- Las cifras de latencia de extremo a extremo son estimaciones de
  especificación.

**Pico 2 W (segunda parte de la sesión).** El usuario avisó que ya tiene una
Raspberry Pi Pico 2 W, y quedó evaluada en 08 §3.1:
- **su radio CYW43439 no hace advertising extendido**, así que no hay BIG
  (REPORTADO, con una traza HCI en pico-sdk #2313);
- **el RP2350 puede ser el cerebro de la Fase 3 (H2b)**: TinyUSB con 4 canales +
  liblc3 + host BTstack, con una SuperMini por UART. BTstack ya tiene un port
  oficial casi igual (`rp2040-vela-if820`);
- **P3 se rehízo para la Pico**, en dos pasos: (a) benchmark de LC3 en el M33;
  (b) speaker USB de 4 canales. La Pi Zero 2 W queda como alternativa si (a)
  falla, y no hay que comprar nada por ahora;
- se sumó a P2 `bluekitchen/hci_uart_iso_timesync` (con el comando `LE Read ISO
  Clock`) como firmware candidato para las SuperMini;
- la Pico sirve también de sonda SWD (debugprobe) para recuperar una SuperMini.

**Qué salió mal en el camino (segunda parte).** El script de edición se cortó a
la mitad porque un bloque de texto no coincidía. Quedaron aplicadas solo las
primeras ediciones; se detectó por el `AssertionError` y se completó en una
segunda pasada.

**No verificado (segunda parte).**
- El costo de liblc3 en el RP2350: no hay cifras publicadas, y la estimación de
  72–150+ MHz para 4 canales es INFERIDA.
- Que TinyUSB haga 4 canales con feedback en el RP2xxx.
- Que `LICENSE.RP` cubra un controlador externo.

**Aprendizajes para el harvest.** Quedaron dos filas en
`.agents/tracking/candidates.md`:
- una segunda ocurrencia de "Documented default values drift from the code"
  (`OPEN.md`): la documentación de PipeWire mostraba valores de ejemplo como si
  fueran los de por defecto;
- `check-every-anchor-before-the-first-write`: el script de edición que se cortó
  a la mitad.

**Commits.** Dos, uno por tema: la investigación del software del PC (07) y el
plan de integración (08) con la Pico 2 W. Van sin trailer de coautor, por
preferencia del usuario.

**Medido.** Nada. `scripts/check.sh` pasa.

---

## 2026-09-26 · s-7c8794-da36f9 — Placas nRF52840 evaluadas; compradas 5 SuperMini

**Qué.**
- Se agregaron la Seeed XIAO nRF52840 y la SuperMini nRF52840 (clon de nice!nano)
  a la tabla de hardware de 06 §1, con sus diferencias frente al dongle nRF52840.
- Se revisaron la wiki del vendedor de la SuperMini y su footprint KiCad. Ninguno
  dice si lleva cristal de 32 kHz: el footprint trae solo pads.
- Se configuró `origin` (GitHub, privado) y se hizo el primer push, forzado a
  pedido del usuario: reemplazó un "Initial commit" que traía solo un README de
  una línea.
- El usuario compró 5 SuperMini. Quedó registrada la decisión d-7c8794-b82ee9, y
  el roadmap (hardware, E1 y E2) y el README (opción combinada) se actualizaron.

**Archivos.** `docs/research/06-opcion-c-nrf5340.md`, `docs/decisions.md`,
`docs/roadmap.md`, `docs/research/README.md`.

**Por qué.** El usuario preguntó por un kit Meshtastic (XIAO + Wio-SX1262), luego
por la SuperMini, y terminó comprando un pack de 5.

**Arquitectura.** ✅ Cumple: solo investigación y compra, sin código.

**Qué quedó pendiente.**
- Cuando lleguen las placas:
  - flashear `hci_uart` con `promicro_nrf52840`, copiando `nrf52840dongle_nrf52840.conf`;
  - revisar si el cristal de 32 kHz arranca, y si no, usar RC;
  - hacer E1 y E2 desde el Mac por `serial:`.
- El footprint KiCad (`SuperMini NRF52840.kicad_mod`) ya no está en el
  repositorio: lo sacó el usuario. Lo que tenía de útil quedó en 06 §1.
- No se revisó si `auracast-hackers-toolkit` corre en estas placas.

**No verificado.**
- Los precios son REPORTADOS; no se vieron en una tienda.
- Que `hci_uart` con ISO funcione en `promicro_nrf52840` o `xiao_ble` es
  INFERIDO, porque usan el mismo chip que el dongle.
- Que los 4 pads traseros de la SuperMini sean SWD es INFERIDO.

---

## 2026-09-26 · s-7c8794-00d464 — Datos crudos guardados sin máscara (continúa s-7c8794-1a0f01)

**Qué.**
- A pedido del usuario, se guardaron los datos que se habían enmascarado:
  - los 6 escaneos crudos en `docs/research/experimentos/datos/01/`;
  - `system_profiler SPBluetoothDataType` completo y `sw_vers` en
    `docs/research/experimentos/datos/00/`;
  - en los documentos 00 y 01, los bytes completos, las direcciones de los JBL y
    del Mac, y el nombre personalizado del Go 4.
- Se agregaron el histograma de company IDs del escaneo sin filtro y los JBL Tune
  770NC-LE como posible receptor LE Audio de prueba.
- Se registró la decisión d-7c8794-8374e1: estos datos se guardan mientras el
  repositorio sea privado y se limpian antes de publicarlo.

**Por qué.** El usuario dijo que no hay problema en guardarlos, porque el
repositorio no se publicará por ahora.

**Qué quedó pendiente.**
- Los nombres de dispositivos de terceros del escaneo sin filtro no se guardaron:
  no son del usuario y no aportan a la investigación.
- La salida de ese escaneo nunca se guardó en un archivo; solo queda el
  histograma.

**Medido.** Con los bytes completos se ve que los bytes 4–7 del Charge 6
(`ed 0e 88 3f`) son iguales en reposo y transmitiendo, así que son estables por
unidad.

---

## 2026-09-26 · s-7c8794-1a0f01 — E2 parcial en el Mac: anuncios de los JBL leídos con CoreBluetooth

**Qué.**
- Se instaló `bleak` 3.0.2 en un entorno virtual del scratchpad, fuera del
  repositorio, con permiso del usuario.
- Se escribió el probe `probes/e2-scan-mac/scan.py`.
- Con el usuario manejando los parlantes, se escaneó en tres estados: en reposo,
  con el Go 4 transmitiendo Auracast y con el Charge 6 transmitiendo Auracast.
- El resultado quedó en `docs/research/experimentos/01-e2-anuncios-jbl-mac.md`, y
  se actualizaron 01, el índice, las referencias y E2 en el roadmap ("A medias").

**Archivos.** `probes/e2-scan-mac/scan.py`,
`docs/research/experimentos/01-e2-anuncios-jbl-mac.md`,
`docs/research/01-parlantes-jbl.md`, `docs/research/README.md`,
`docs/references.md`, `docs/roadmap.md`.

**Por qué.** El usuario pidió intentar las pruebas en el Mac y autorizó instalar
lo necesario.

**Arquitectura.** ✅ Cumple. Es un probe desechable en `probes/`; no hay código de
producto.

**Qué salió mal en el camino.**
- El primer escaneo filtrado de 20 s no vio el Charge 6. Un escaneo sin filtro
  mostró que estaba presente. Se subió la duración a 30 s, y el probe informa
  primero cuántos dispositivos vio, para que un resultado vacío no se lea como
  "no anuncia nada".
- El escaneo sin filtro imprimió en la consola los nombres de dispositivos
  cercanos de terceros. No se copiaron al repositorio.
- En el documento se enmascararon los bytes propios de cada parlante y el nombre
  personal del Go 4.

**Qué quedó pendiente.**
- Escanear durante el emparejamiento estéreo, por si el anuncio Auracast aparece solo
  en ese momento. También medir el mismo Go 4 solo y en estéreo.
- La BASE y el BIGInfo, que necesitan un controlador accesible.
- Repetir una transmisión para saber si el Broadcast_ID cambia.
- **El probe se conserva** hasta cerrar E2. Después se borra (d-7c8794-3208b7).

**Medido.**
- Datos de fabricante de la transmisión: `0x0057 +
  00000000000000000000000000000000dffd`, idénticos en el Go 4 y el Charge 6.
- El Charge 6 anuncia PBP `04 00` y el Broadcast_ID 0x112233.
- El Go 4 anuncia el Broadcast_ID 0x008105 y no anuncia PBP.
- Dos Go 4 (rojo y azul) sincronizados en estéreo y reproduciendo: **ningún
  0x1852 en 30 s ni en 45 s**. Su anuncio de reposo tiene `09 60` en los bytes 8–9.
  El byte 2 vale negro = `01`, rojo = `02`, azul = `03`; la hipótesis es que indica
  el color.
- Un primer intento leyó el byte 2 como el rol en el par estéreo, suponiendo que
  el "Bl" era la misma unidad negra. El usuario aclaró que era un Go 4 azul, y el
  documento se corrigió.

---

## 2026-09-26 · s-7c8794-84eb42 — Inventario del Mac: Bumble no alcanza el controlador interno

**Qué.**
- Se hizo el inventario de solo lectura del Mac (`system_profiler`) y se leyó la
  documentación de Bumble para macOS.
- Se registró el resultado en `docs/research/experimentos/00-inventario-mac.md`.
- En el roadmap, el inventario pasó a "A medias".

**Archivos.** `docs/research/experimentos/00-inventario-mac.md`,
`docs/roadmap.md`.

**Por qué.** El usuario preguntó si las pruebas se pueden hacer en este Mac.

**Arquitectura.** ✅ Cumple. No se instaló ni se cambió nada en el sistema.

**Qué salió mal en el camino.** Nada. Se evitó escribir en el repositorio las
direcciones Bluetooth y los nombres de los dispositivos emparejados, aunque
`system_profiler` los muestra.

**Qué quedó pendiente.**
- El inventario del equipo Linux.
- El E2 parcial con `bleak` en el Mac: requiere instalar `bleak` en un entorno
  virtual temporal y que el usuario ponga un JBL a transmitir. Se propuso y no se
  ejecutó.

**Medido.**
- El controlador del Mac es MTK_7932, por PCIe, con LEA declarado.
- El PID del Charge 6 es 0x20E3, igual al que documenta openjbl.

---

## 2026-09-26 · s-7c8794-77b101 — Opciones A (Bumble) y C (nRF5340) investigadas en profundidad

**Qué.**
- Se escribieron `docs/research/05-opcion-a-bumble.md` y
  `docs/research/06-opcion-c-nrf5340.md`, a partir de la lectura del código de
  Bumble, Zephyr y sdk-nrf en commits fijos.
- Se agregó al índice de la investigación la comparación entre A y C, con la
  opción combinada (Bumble con un nRF por `hci_uart`) y un orden sugerido.
- Se actualizaron las opciones de compra de E1 en el roadmap y el mapa de
  `CLAUDE.md`.

**Archivos.** `docs/research/05-opcion-a-bumble.md`,
`docs/research/06-opcion-c-nrf5340.md`, `docs/research/README.md`,
`docs/roadmap.md`, `CLAUDE.md`.

**Por qué.** El usuario eligió las opciones A y C para seguir explorándolas.

**Arquitectura.** ✅ Cumple. Solo documentación.

**Qué salió mal en el camino.** La línea A sugirió que `hci_usb` de Zephyr podía
servir como controlador ISO para Bumble. La línea C mostró que no: Zephyr manda a
ACL todo lo que llega por USB bulk (issue #44013). Se corrigió en 05 §1. Dos
agentes que leen el mismo código desde lados distintos se corrigen entre sí; uno
solo no lo habría notado.

**Qué quedó pendiente.**
- El harvest de `.agents/` sigue a la espera: el usuario lo dejó para después.
- Las dos sesiones del 2026-09-25/26 (implementaciones, y opciones A y C) van en
  un solo commit de investigación, porque comparten archivos (índice, roadmap,
  `CLAUDE.md`) y son un mismo tema.
- Sigue pendiente el inventario del chip (i-7c8794-d9c834).

**No verificado.**
- Los agentes leyeron el código en copias temporales del scratchpad. Los números
  de línea corresponden a los commits citados en cada documento.
- Los precios de DigiKey son del 2026-09-25.

---

## 2026-09-25 · s-7c8794-ed065e — Investigación de implementaciones y stacks; preguntas previas del harvest

**Qué.**
- Cuarta línea de investigación: las implementaciones abiertas por capa, con su
  lenguaje, licencia y actividad revisados en cada repositorio; proyectos de
  ingeniería inversa de JBL; las cuatro opciones de stack para el prototipo.
- Se corrigieron dos datos del documento 02 (el nodo por BIS de PipeWire queda en
  disputa; el ESP32 queda descartado como emisor).
- Se actualizaron el índice de la investigación, `CLAUDE.md`, las referencias y
  E2 y E3 del roadmap.
- Se preparó el harvest del problema de `.agents/` y se le hicieron al usuario las
  preguntas previas. Todavía no se escribió nada en `tracking/`.

**Archivos.** `docs/research/04-implementaciones-y-stacks.md`,
`docs/research/02-le-audio-auracast-linux.md`, `docs/research/README.md`,
`docs/references.md`, `docs/roadmap.md`, `CLAUDE.md`.

**Por qué.** El usuario preguntó qué implementaciones existen, en qué lenguajes y
qué stack usan, y quiere resolver el problema del paquete `.agents/`.

**Arquitectura.** ✅ Cumple. Solo documentación; no hay código.

**Qué salió mal en el camino.** La línea 02 marcó como VERIFICADO, por los
comentarios del código de PipeWire, que se crea un nodo por BIS. La línea 04 no
pudo confirmarlo. Un comentario de código no basta para marcar algo VERIFICADO si
se refiere a un comportamiento en ejecución.

**Qué quedó pendiente.**
- El harvest espera la respuesta del usuario a las preguntas previas.
- El chipset de los JBL: el sitio de la FCC bloqueó el acceso.
- chicco-carone/sync-test sin revisar.

**No verificado.** Las fuentes las reunió un agente de investigación; esta sesión
no volvió a abrir cada repositorio.

---

## 2026-09-25 · s-7c8794-88a0f6 — Repositorio preparado para la fase de investigación

**Qué.**
- Se hizo la investigación de factibilidad en tres líneas: los parlantes JBL,
  LE Audio y Auracast en Linux, y A2DP con sincronización por software. Quedó en
  `docs/research/`.
- Se escribieron el registro de fuentes, las decisiones iniciales, el plan de
  desarrollo (`docs/roadmap.md`), `CLAUDE.md` y el chequeo `scripts/check.sh`.
- Se ejecutó `git init` y se hizo el primer commit en `main`, sin remoto.
- Se generó el id propio del repositorio (`r-7c8794`) y se vació el outbox.

**Archivos.** `CLAUDE.md`, `.gitignore`, `docs/`, `probes/README.md`, `scripts/check.sh`,
`.agents/carrier.toml`, `.agents/tracking/`.

**Por qué.** El usuario quiere investigar antes de decidir si vale la pena
desarrollar, y dejar un plan de desarrollo basado en la investigación.

**Arquitectura.** ✅ Cumple. Es un bootstrap reducido. Lo que quedó fuera está
en `declined`, en `.agents/carrier.toml`.

**Qué salió mal en el camino.**
- La copia de `.agents/` no se hizo con `bundle.py export`. Traía el
  `carrier.toml` del repositorio de origen (otro id, sin `upstream`), 5 filas de
  outbox ajenas y un `tools/__pycache__/`. Se borraron y se regeneraron.
- `bundle.py` falló con el `python3` por defecto (3.9, sin `tomllib`), así que se
  usa `/opt/homebrew/bin/python3.14`.
- La plantilla de roadmap del método pone el id antes del `·` (`### i-… · Idea`),
  pero `bundle.py ids` solo reconoce un id en un título cuando va después
  (`### Idea · i-…`). Con la plantilla, los 15 ids del roadmap no se revisaban. El
  contador del chequeo lo delató (7 ids definidos en vez de 22). Se invirtió el
  orden en los títulos.

**Qué quedó pendiente.**
- No se identificó el chip Bluetooth del equipo Linux (i-7c8794-d9c834).
- No hay remoto configurado (i-7c8794-c28668).
- No se leyeron los QDID del Bluetooth SIG de los JBL.
- No se hizo ningún experimento.
- El desajuste entre la plantilla y la herramienta queda para un harvest
  (`.agents/method/prompt-harvest.md`), que lo ofrecería como candidato al origen.

**No verificado.** Todo lo que está en `docs/research/` es investigación
documental (VERIFICADO, REPORTADO o INFERIDO). No se midió nada con los parlantes
propios. Los agentes de investigación reunieron las fuentes y esta sesión no volvió
a abrir cada una.

**Medido.** `scripts/check.sh` pasa y reconoce 22 ids definidos. También se vio fallar: con un id duplicado
plantado en una copia de prueba, `bundle.py ids` salió con error.

---

## Formato de una entrada (va siempre al final del archivo)

    ## AAAA-MM-DD · s-<repo6>-<content6> — <título en una línea>
    **Qué.** Qué cambió, en concreto.
    **Archivos.** Archivos o carpetas.
    **Por qué.** El motivo, incluido el pedido que lo originó.
    **Arquitectura.** ✅ Cumple · ⚠️ Desvío · REVISAR, y por qué.
    **Qué salió mal en el camino.** Qué hizo mal el primer intento y qué lo detectó.
    **Qué quedó pendiente.** La deuda creada o esquivada, con nombre.
    **Desvío del plan.** En qué se aparta de lo aprobado, y la medición que lo decidió.
    **No verificado.** Lo que no se pudo comprobar, y dónde queda la pregunta.
    **Medido.** El número, si se afirmó algo.

El id `s-` se genera con `bundle.py id s "<título>"`.
