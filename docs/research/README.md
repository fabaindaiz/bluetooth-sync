# Investigación: audio multicanal sincronizado en parlantes JBL

**Estado:** fase de investigación (2026-09-25). No hay código. Este documento
resume lo encontrado y sirve para decidir si vale la pena desarrollar algo.

## La pregunta

Se tienen **3× JBL Go 4 y 1× JBL Charge 6**. ¿Puede un PC, un teléfono o un
adaptador en Linux mandarles **un canal distinto a cada uno**, con **audio
sincronizado**? El objetivo es un estéreo real, cuadrafonía o surround simulado sin
depender de las limitaciones de la app de JBL.

## Los documentos

| Documento | Qué cubre |
|---|---|
| [01-parlantes-jbl.md](01-parlantes-jbl.md) | Qué soportan los parlantes y cómo funcionan en el protocolo el Auracast y el estéreo de JBL |
| [02-le-audio-auracast-linux.md](02-le-audio-auracast-linux.md) | LE Audio y Auracast desde Linux: BlueZ, PipeWire, Bumble, Nordic, controladores y multicanal por BIS |
| [03-bluetooth-clasico-y-sync-por-software.md](03-bluetooth-clasico-y-sync-por-software.md) | A2DP con varios parlantes, PipeWire combine-stream, Snapcast, upmix y calibración |
| [04-implementaciones-y-stacks.md](04-implementaciones-y-stacks.md) | Implementaciones abiertas por capa, con lenguaje, licencia y actividad; proyectos de ingeniería inversa de JBL; opciones de stack para el prototipo |
| [05-opcion-a-bumble.md](05-opcion-a-bumble.md) | Opción A en profundidad: Bumble por dentro, controladores y transportes, parche para 4 BIS, BASS, sincronización y primeros comandos |
| [06-opcion-c-nrf5340.md](06-opcion-c-nrf5340.md) | Opción C en profundidad: placas Nordic y precios, entrada USB, varios BIS, datos de fabricante, licencias, timestamps y primeros pasos |

**Qué significa cada marca:**
- **VERIFICADO**: fuente primaria.
- **REPORTADO**: foro, blog o reseña.
- **INFERIDO**: deducción no comprobada.

Nada se ha medido todavía con los parlantes propios.

## Las respuestas cortas

**¿Bluetooth trae una forma estándar de sincronizar varios dispositivos?**
- **Bluetooth clásico (A2DP) no.** Cada enlace es independiente, cada parlante
  tiene su propio reloj y buffer, y los mecanismos de varios receptores que existen
  (CSB, Qualcomm TWS, PartyBoost) son propietarios.
- **LE Audio sí.** En Auracast, todos los BIS de un mismo BIG comparten una
  referencia de tiempo y cada receptor reproduce después de un Presentation Delay
  común. La sincronización la garantiza el estándar.

**¿Se puede usar el "stereo group" o el Auracast de JBL desde fuera?**
- **Como receptores, sí.** Collabora (PipeWire y BlueZ en Linux) y Google Bumble
  lograron que un **JBL Go 4 reciba una transmisión Auracast de terceros**. La
  condición es incluir en el anuncio los **datos de fabricante de Harman**; sin
  ellos, JBL ignora la transmisión. Eso también explica por qué Samsung, Pixel y
  Windows no funcionan con estos parlantes.
- **No se sabe todavía** si un JBL, ante una transmisión de varios BIS, **elige el
  canal que se le indique** (por su Audio Location o por BASS `BIS_Sync`). Esa es
  **la pregunta que decide el proyecto**.
- El estéreo propio de JBL solo funciona entre dos parlantes del mismo modelo, y
  What Hi-Fi critica su sincronización.

**¿En qué lenguajes están?**
- Las capas de protocolo y de audio están en **C** (BlueZ, PipeWire, Zephyr,
  NimBLE, liblc3).
- Las herramientas para experimentar con Auracast y con JBL están sobre todo en
  **Python** (Bumble, openjbl).
- El detalle y las cuatro opciones de stack están en
  [04](04-implementaciones-y-stacks.md).

**¿Existen proyectos parecidos?**
- **Sí, pero ninguno hace exactamente esto.**
  - Por Bluetooth clásico: HyperBoom (L/R en dos parlantes con PipeWire), Sendspin
    BT Bridge y Snapcast.
  - Por Auracast: Bumble (L/R en 2 BIS), Nordic nrf5340_audio y nRF Auraconfig.
- **No encontré** ningún proyecto abierto de surround o de más de 2 canales por
  Auracast, ni una herramienta Linux que calibre con micrófono varios parlantes
  Bluetooth.

## Los tres caminos

| | A. Auracast desde Linux (un BIG, un BIS por parlante) | B. A2DP con varios parlantes + PipeWire combine-stream | C. Snapcast + una Raspberry Pi por parlante |
|---|---|---|---|
| Sincronización | **Garantizada por el estándar** dentro de un BIG | Sin garantía. Offsets manuales; desfase de ~5–20 ms que varía (INFERIDO) | Red bajo 1 ms. El tramo BT de cada parlante no se corrige |
| Canal por parlante | Lo permite el estándar. **Falta saber si JBL lo respeta** | Sí (`audio.position` por stream) | Sí, con remap en cada cliente |
| Mezcla Go 4 + Charge 6 | Probable si el Charge 6 acepta los datos de Harman (por verificar) | Sí | Sí |
| Hardware | Controlador con `iso-broadcaster`: **MediaTek MT7921** (probado con Go 4), Intel BE200, o un nRF5340 DK | 2 o más dongles USB (RTL8761B recomendado), 1 a 3 parlantes por dongle | 4 Raspberry Pi (el BT integrado sirve para un parlante cada una) |
| Madurez | Experimental. BlueZ y PipeWire con modos experimentales; Bumble funciona | Estable | Estable |
| Qué se puede aportar | **Surround por Auracast**, que no existe en proyectos abiertos | Calibración automática con micrófono | Poco nuevo |

Ruta extra: **el Charge 6 acepta audio por USB-C desde un laptop.** Podría ser un
canal cableado (centro o subwoofer) con latencia fija, combinado con cualquiera de
los tres caminos.

## Opciones A y C comparadas

Las opciones A (Bumble) y C (nRF5340) se estudiaron en detalle en
[05](05-opcion-a-bumble.md) y [06](06-opcion-c-nrf5340.md).

| | A · Python + Bumble en el PC | C · nRF5340 como emisor dedicado |
|---|---|---|
| Hardware para empezar | **Quizás ninguno**: `hci-socket` usa el chip interno si tiene `iso-broadcaster` | nRF5340 Audio DK (~US$173) + microSD |
| Entrada de audio | Flexible: WAV, stdin (ffmpeg o PipeWire) o dispositivo; cualquier cantidad de canales con el parche | Para 4 canales, solo archivos `.lc3` en SD (`nrf_auraconfig`). La entrada USB es estéreo y llegar a 4 canales cuesta 1–3 semanas |
| 4 BIS con ubicación por BIS | Parche pequeño a `auracast.py` | Ya existe en `nrf_auraconfig` (`nac location`) |
| Datos de fabricante de JBL | Ya existe (`--manufacturer-data`) | Parche de ~15 líneas |
| Alineación entre BIS | **Sin timestamps** (riesgo de 10 ms; se pueden agregar) | **Con timestamps del controlador** y precisión de µs |
| Drift de reloj | No se maneja; se evita alimentando desde stdin o PipeWire | Por USB se descartan bloques (clics); con SD no hay drift |
| Asistente BASS | Cliente existe, con parche para elegir BIS; hace falta un 2.º controlador o un solo proceso | Shell de Zephyr con `sync_bis` por receptor, lista para usar |
| Licencias | Apache-2.0 | Samples de Zephyr: Apache-2.0. `nrf_audio`: Nordic y Packetcraft (archivos que no se pueden modificar) |
| Esfuerzo hasta la prueba decisiva | Horas, si el chip sirve | 2–4 días, incluida la instalación de herramientas |
| Camino a producto | Directo: se integra con PipeWire en el mismo PC | Requiere resolver la entrada USB multicanal |

**Opción combinada** (INFERIDA): Bumble en el PC con un nRF como controlador por
`hci_uart` sobre USB CDC-ACM (el dongle nRF52840 cuesta US$11.69). Tiene la entrada
flexible de A sin depender del chip del laptop, y con
`bluekitchen/hci_uart_iso_timesync` se pueden obtener timestamps. Nadie la ha
probado con un JBL. **El usuario compró 5 SuperMini nRF52840 para esta opción**
(2026-09-26, d-7c8794-b82ee9); sirven también el dongle o una XIAO nRF52840
([06](06-opcion-c-nrf5340.md) §1).

**Orden sugerido:**
1. **Probar A primero**, porque puede no costar nada.
2. Si el chip interno no tiene `iso-broadcaster`, elegir entre la **combinada** (la
   compra más barata) y **C con el Audio DK** (la más confiable para la prueba
   decisiva, porque manda los timestamps y trae ubicaciones por BIS sin tocar
   código).
3. **C también sirve de referencia** si A muestra desfase entre BIS: si con C el
   desfase desaparece, la causa es la falta de timestamps en Bumble.

## Recomendación

**Todavía no conviene desarrollar un proyecto completo. Sí conviene hacer una
prueba de factibilidad corta y barata**, porque una sola pregunta, con respuesta de
sí o no, decide todo:

> ¿Un JBL Go 4 (y el Charge 6) reproduce solo el BIS que le corresponde dentro de
> un BIG con varios BIS?

Pasos, de menor a mayor costo (el detalle está en cada documento):

1. **Revisar el controlador que ya hay** (`bluetoothctl` → `menu mgmt` →
   `iso-broadcaster`). Costo: minutos.
2. **Leer el anuncio y la BASE de los propios JBL** en modo transmisor y en estéreo
   JBL. Así se obtienen sus datos de fabricante, cómo transportan L/R y si cifran.
3. **Transmitir mono con Bumble a un Go 4**, para reproducir el resultado conocido.
   Si el controlador actual no sirve, esto requiere comprar uno (MT7921/BE200 o un
   nRF5340 DK).
4. **La prueba decisiva: estéreo en 2 BIS**, primero dejando que cada Go 4 elija y
   después forzando `BIS_Sync` por BASS.
5. Si el paso 4 sale bien: 4 BIS en un BIG y medición de la alineación con un
   micrófono.

**Criterio de decisión:**
- **Seguir con el camino A** si el paso 4 muestra que los JBL respetan la selección
  de BIS.
- **Pasar al camino B o C, con expectativas más bajas** (fiesta o estéreo aceptable,
  sin un surround exigente), si los JBL siempre toman el primer BIS o mezclan.
- **Descartar el surround** si además el desfase A2DP medido supera lo tolerable.

## Qué queda abierto en toda la investigación

- Si los JBL respetan Sink Audio Locations o BASS `BIS_Sync`.
- ~~Si el Charge 6 acepta los mismos datos de fabricante que el Go 4.~~ Transmite
  los mismos 18 bytes que el Go 4 (MEDIDO,
  [experimentos/01](experimentos/01-e2-anuncios-jbl-mac.md)). Que los acepte como
  receptor sigue pendiente (E3).
- Cómo transporta L/R el estéreo propio de JBL. Mientras reproduce en estéreo no
  expone una transmisión Auracast visible ([experimentos/01](experimentos/01-e2-anuncios-jbl-mac.md), resultado 4). Se puede
  probar escaneando durante el emparejamiento estéreo, o con un sniffer de anuncios
  periódicos.
- Un dongle USB con LE Audio confirmado que funcione.
- Cifras medidas de latencia o drift de estos parlantes.
- Los QDID del Bluetooth SIG de los parlantes.
