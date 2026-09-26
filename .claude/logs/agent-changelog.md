# Registro de sesiones

Cada sesión que cambia algo agrega su entrada **arriba**, justo debajo del
separador que sigue, con el formato que está al final del archivo. Las sesiones
paralelas no se ven entre sí. Lo único que avisa a la siguiente es lo que salió
mal y lo que quedó pendiente.

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
