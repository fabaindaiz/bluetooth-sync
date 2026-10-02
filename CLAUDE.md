# CLAUDE.md

Este repositorio investiga si un equipo Linux (o un adaptador) puede mandar **un
canal de audio distinto a cada uno de varios parlantes JBL** (3× Go 4 y 1× Charge
6), **sincronizados**, para lograr estéreo real, cuadrafonía o surround simulado sin
las limitaciones de la app de JBL.

**Estado (2026-09-28): se construye el núcleo, con A2DP como primer backend emisor**
(d-7c8794-9afee2). Lo que lo desbloqueó es que **E4 resultó imposible con el hardware
actual** —el AX210 no puede transmitir ni escuchar un BIS— y que **A2DP con 2 o 3
parlantes iguales alinea a pocos milisegundos**. La decisión sobre Auracast sigue
abierta hasta que lleguen las SuperMini. **Lo próximo (2026-10-02):** con los 3 Go 4 encendidos, escuchar
20 minutos de música mirando Diagnóstico → Cortes, para confirmar o descartar las causas de
los cortes (`docs/research/experimentos/10-…` §9, d-7c8794-560b54); después, construir la
sonda enmascarada en el motor (i-7c8794-e3e40d, paso 2; el paso 1 dio luz verde en
`experimentos/11-…`). El servicio de control y su panel ya están construidos y en uso
(`aurasync service`).

Lo distinto de este proyecto es que la pregunta que decide todo (si los JBL reproducen
solo el BIS de Auracast que les corresponde) solo se responde con los parlantes en la
mano, no leyendo. Y lo segundo: **el objetivo no es la precisión de imagen estéreo sino
el envolvimiento** —parlantes en los bordes de una pieza y el oyente moviéndose—, lo que
cambia qué importa (`docs/research/09-…`).

## Restricciones que no se negocian

- **El núcleo del host se construye con A2DP como primer backend emisor**
  (d-7c8794-9afee2, que enmienda d-7c8794-346170). **Auracast no queda descartado**: E4
  se hace igual cuando llegue el hardware, y nada del núcleo debe atarse a A2DP. El
  roadmap lo dice así desde el principio: *mismo núcleo, otro backend emisor*.
- **Nada de lo que se mide con parlantes se da por bueno si no sobrevive a cambiar un
  parámetro que no debería importar.** Esta sesión descartó tres criterios de calidad
  que parecían razonables (la nitidez del pico, la coincidencia entre ventanas iguales,
  el residuo de reconstrucción) porque cada uno informaba "todo bien" mientras la
  medición estaba equivocada. El que quedó es la **estabilidad ante cambios del
  análisis** (`docs/research/experimentos/06-…`). **Y tampoco alcanza sola:** con
  referencias correlacionadas entre sí, la estabilidad informó 0,01 ms mientras la
  medición erraba 7,10 ms, porque los tres tamaños de ventana se equivocaban igual
  (`docs/research/experimentos/08-…`). Lo que agrega el filtro que faltaba es la
  **repetición entre mediciones independientes**: un desfase verdadero se ve dos veces
  y un artefacto de correlación no.
- **Todo hallazgo queda escrito en `docs/research/`**, con su fuente y su marca
  VERIFICADO, REPORTADO, INFERIDO o MEDIDO (d-7c8794-1253b0). Un resultado que solo
  queda en el chat se pierde. Se revisa en cada revisión.
- **Si se sigue por Auracast, todos los canales van en un solo BIG**
  (d-7c8794-203de2, provisional hasta el experimento E5). Dos transmisiones
  separadas no quedan alineadas entre sí.

## Salvaguardas que no se relajan

- **La sincronización se mide, no se supone.** Una afirmación sobre desfase o drift
  lleva un número medido con micrófono, junto con el entorno (versiones y firmware)
  en `docs/research/experimentos/`. El desfase entre parlantes es el tipo de error
  que ningún log muestra: solo se oye.
- **Los parlantes son una caja negra de firmware cerrado.** Lo que JBL no documenta
  se marca INFERIDO hasta que se mide. Una actualización de firmware de los JBL
  puede cambiar un resultado, así que cada medición anota la versión.
- **Los cambios en el sistema (`/etc/bluetooth/main.conf`, modos experimentales de
  BlueZ) se anotan junto con cómo revertirlos**, antes de hacerlos.
- **Lo que el programa le pide al sistema de audio se verifica, no se supone.** Un
  `pw-play --target` puede terminar en otro destino sin dar error: WirePlumber mueve el
  stream si el sink por defecto cambia, aunque el target esté puesto. El síntoma fue un
  parlante mudo y un lazo de realimentación, y **lo detectó el oído del usuario, no un
  test** (`docs/research/experimentos/09-…`). Cada vez que se le pide algo a PipeWire,
  después se comprueba que pasó.

## Archivos que no se editan a mano

- `.agents/`, salvo `carrier.toml` y las propuestas `proposals/p-*.md`. El resto es
  de la versión del paquete, y `scripts/check.sh` falla si cambia. Lo que este
  repositorio aprende para el paquete sale como propuesta, un archivo por cada una,
  que escribe `bundle.py propose` y nunca se edita (`.agents/proposals/README.md`).
- `.claude/agents/knowledge-reviewer.md`, que es una copia de
  `.agents/agents/knowledge-reviewer.md` y se vuelve a copiar en cada actualización.

## Comandos

```bash
scripts/check.sh                                              # el chequeo: bundle, ids, archivos del host, lint y tests
/opt/homebrew/bin/python3.14 .agents/tools/bundle.py id d "…"  # id de una decisión (i = roadmap, s = changelog)
cd host && hatch test                                         # tests del paquete aurasync
cd host && hatch fmt --check                                  # lint y formato (ruff, fijado por hatch)
cd host && hatch run aurasync --version
```

- `bundle.py` necesita Python 3.11 o superior; el `python3` por defecto de este
  equipo es 3.9 (d-7c8794-3b6b73). En Linux, se corre con `PY=python3.12
  scripts/check.sh` o con el intérprete que corresponda.
- El host se maneja con **hatch**, no con uv directo (d-7c8794-c23c20). **No se
  activan los lockfiles de hatch**: con hatch 1.18.1 desinstalan el propio proyecto
  del entorno.

## Verificación

- Antes de dar algo por terminado se corre `scripts/check.sh`.
- Un experimento está terminado cuando su archivo en `docs/research/experimentos/`
  tiene el resultado MEDIDO y el veredicto, y su entrada en `docs/roadmap.md` cambió
  de estado en el mismo cambio.

## Commits

Solo cuando el usuario lo pide. Un tema por commit: investigación, experimento o
estructura. `scripts/check.sh` tiene que pasar antes.

## Registro obligatorio

Cada sesión que cambia algo agrega arriba una entrada en
`.claude/logs/agent-changelog.md` (el formato está al final de ese archivo). Las
sesiones paralelas no se ven entre sí, y lo que salió mal y lo que quedó pendiente
es lo único que avisa a la siguiente.

## Forma de trabajar

- Documentos propios en español; identificadores, comandos y rutas tal cual. Lo que
  está dentro de `.agents/` sigue en inglés.
- **El código nuevo del host va en inglés** (d-7c8794-7b3093): módulos, identificadores,
  docstrings, rutas REST, campos del contrato y la documentación de la API. Lo existente
  queda en español hasta su migración (i-7c8794-f30928); al editar un archivo en español
  se mantiene su idioma y la lógica nueva va, si se puede, a un módulo nuevo en inglés.
- **Hay tres equipos** (`docs/research/experimentos/00-inventario-*.md`): el Mac,
  `PC-Ryzen5` (el de las pruebas con parlantes, con el micrófono fifine) y el portátil
  `HP-O16` (mismo AX210; por ahora solo desarrollo, sin pruebas de audio). Cada
  medición anota en qué equipo se hizo.
- Explicar los trade-offs y preguntar antes de cambios estructurales o de gastos
  (hardware). Extender un documento existente antes de crear otro.

## Los documentos y qué pregunta responde cada uno

| Pregunta | Documento |
|---|---|
| ¿Qué se sabe y qué se recomienda? | `docs/research/README.md` |
| ¿Qué soportan los JBL? | `docs/research/01-parlantes-jbl.md` |
| ¿Cómo se transmite Auracast desde Linux y con qué hardware? | `docs/research/02-le-audio-auracast-linux.md` |
| ¿Qué se puede hacer con A2DP y sincronización por software? | `docs/research/03-bluetooth-clasico-y-sync-por-software.md` |
| ¿Qué implementaciones existen, en qué lenguaje, y qué stack conviene? | `docs/research/04-implementaciones-y-stacks.md` |
| ¿Cómo se hace con Bumble (opción A)? | `docs/research/05-opcion-a-bumble.md` |
| ¿Cómo se hace con un nRF5340 (opción C)? | `docs/research/06-opcion-c-nrf5340.md` |
| ¿Qué software del PC captura, separa y hace upmix del audio, y con qué latencia? | `docs/research/07-software-de-audio-en-el-pc.md` |
| ¿Cómo se integra todo en una herramienta, con qué stack y en qué orden? | `docs/research/08-integracion-y-plan.md` |
| ¿Qué produce el efecto envolvente, cómo potenciarlo y qué considerar al implementarlo? | `docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` |
| ¿Qué tiene el panel, cómo se organiza y por qué así? | `docs/research/10-panel-de-control.md` |
| ¿Qué se midió? | `docs/research/experimentos/` |
| ¿Qué fuentes cambiaron una decisión? | `docs/references.md` |
| ¿Qué ya está decidido? | `docs/decisions.md` |
| ¿Cómo se diseñó una pieza antes de construirla? | `docs/superpowers/specs/` (en inglés) |
| ¿Cómo está organizado el código y qué va en cada módulo? | `docs/research/08-integracion-y-plan.md` §6.1, `host/README.md`, `firmware/README.md` |
| ¿Qué sigue y con qué choca? | `docs/roadmap.md` |
| ¿Qué hizo cada sesión? | `.claude/logs/agent-changelog.md` |
| Un cambio toca estado, un contrato, datos, seguridad o verificación | Antes de una decisión de diseño, búscalo en `.agents/knowledge/INDEX.md` y abre solo las tarjetas a las que te lleve. Decide cada una por su *Applies if* y su *Not when*, y corre su comprobación antes de darlo por terminado. Abre la nota completa solo si no está claro dónde deja de aplicar aquí. Si este repositorio fija una invariante que contradice una nota, sigue al repositorio y dilo. Solo cuando el usuario pide una revisión en un contexto nuevo, o nombra al revisor, pásale el diff al subagente `knowledge-reviewer` (`.claude/agents/`, copiado de `.agents/agents/` en cada actualización; no se edita) y espera su respuesta. Si el cambio borra o reescribe datos guardados, mueve dinero o toca autenticación y nadie pidió la revisión, ofrécela en el informe, en una línea |
| Privacidad | Nada que se escriba en `.agents/` ni en un archivo que salga de este repositorio puede identificar, directamente o por deducción, un repositorio privado, a sus personas o a sus usuarios. `bundle.py privacy .agents` lo revisa (va dentro de `scripts/check.sh`). Los datos crudos de los dispositivos propios **sí** se guardan en `docs/research/experimentos/` mientras el repositorio sea privado, y se limpian antes de publicarlo (d-7c8794-8374e1) |
| Un procedimiento de `.agents/method/` dice otra cosa que este repositorio | Un procedimiento de `.agents/method/` nunca manda sobre los de este repositorio. Donde nombra un archivo, un formato, un paso, una unidad de trabajo o una regla de commits que aquí se define distinto (el roadmap y la forma de sus títulos, los experimentos en `docs/research/experimentos/`, el registro de sesiones, la numeración, `scripts/check.sh`, los commits solo cuando el usuario los pide), gana lo de este repositorio, y `.agents/carrier.toml` `adapted` anota la equivalencia |
