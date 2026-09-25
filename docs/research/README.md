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
- Si el Charge 6 acepta los mismos datos de fabricante que el Go 4.
- Un dongle USB con LE Audio confirmado que funcione.
- Cifras medidas de latencia o drift de estos parlantes.
- Los QDID del Bluetooth SIG de los parlantes.
