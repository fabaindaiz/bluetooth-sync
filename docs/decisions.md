# Decisiones

Aquí se registra todo lo ya resuelto. Cada decisión se toma **una sola vez**: si
alguien la reabre sin un hecho nuevo, la respuesta está en este documento.

Cada id se genera con
`/opt/homebrew/bin/python3.14 .agents/tools/bundle.py id d "<decisión>"` y nunca
cambia. El detalle vive en el documento que se enlaza; esta tabla es el índice.

**Cómo leer la tabla:**
- Si "Se hace cumplir en" dice "—", **nada impide romper la decisión sin que se
  note**.
- Las filas marcadas **(descartado)** o **(provisional)** son decisiones, no reglas.

## Alcance y forma de trabajar

| Id | Decisión | Por qué | Se hace cumplir en |
|---|---|---|---|
| d-7c8794-346170 | No se escribe código de producto hasta que esté registrada la decisión de seguir o no (i-7c8794-0d129c). Lo único que se programa antes son probes desechables | La investigación dejó abierta una pregunta de sí o no que decide qué camino tomar (¿los JBL respetan la selección de BIS?). Construir antes de responderla es apostar a un camino sin evidencia | — (revisión; `CLAUDE.md`) |
| d-7c8794-1253b0 | Todo hallazgo, sea de investigación o de experimento, se escribe en `docs/research/` con su fuente y su marca VERIFICADO, REPORTADO, INFERIDO o MEDIDO | Los hallazgos son lo que usará el desarrollo. Un resultado que solo queda en el chat se pierde en la sesión siguiente | — (revisión; `CLAUDE.md`) |
| d-7c8794-3208b7 | El código de los probes va en `probes/<nombre>/` y se borra una vez que el resultado está anotado en `docs/research/experimentos/` | Un probe responde una pregunta y no es la base del producto (prompt-context §18, "exploratory spike"). Guardar su resultado conserva el dato sin dejar código huérfano | — (revisión) |
| d-7c8794-3b6b73 | El chequeo corre `bundle.py` con `/opt/homebrew/bin/python3.14` | `bundle.py` necesita Python 3.11 o superior (usa `tomllib`), y el `python3` por defecto de este equipo es 3.9.6, que falla al importar | `scripts/check.sh` |

## Arquitectura de audio

| Id | Decisión | Por qué | Se hace cumplir en |
|---|---|---|---|
| d-7c8794-203de2 | **(provisional)** Si se sigue por Auracast, todos los canales van en **un solo BIG**, un BIS por canal. Nunca en varias transmisiones independientes | La alineación entre parlantes la garantiza la referencia de tiempo común del BIG; dos BIGs no quedan alineados entre sí ([02](research/02-le-audio-auracast-linux.md) §5). Es una deducción a partir del estándar, **no medida**. La confirma o la corrige el experimento E5 (i-7c8794-2cf5e1) | — |
| d-7c8794-1b2706 | **(descartado)** Usar como emisor Auracast para los JBL un teléfono (Samsung o Pixel) o Windows | Los JBL solo aceptan transmisiones que llevan datos de fabricante de Harman, y estos emisores no los incluyen. El Pixel además exige unicast LE, que los JBL no tienen ([01](research/01-parlantes-jbl.md) §3). Es una deducción, no probada: se reabre si alguien muestra un JBL recibiendo de uno de ellos | — |
