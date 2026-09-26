# CLAUDE.md

Este repositorio investiga si un equipo Linux (o un adaptador) puede mandar **un
canal de audio distinto a cada uno de varios parlantes JBL** (3× Go 4 y 1× Charge
6), **sincronizados**, para lograr estéreo real, cuadrafonía o surround simulado sin
las limitaciones de la app de JBL.

**Hoy está en fase de investigación y factibilidad: no hay código de producto.** Lo
distinto de este proyecto es que la pregunta que decide todo (si los JBL reproducen
solo el BIS de Auracast que les corresponde) solo se responde con los parlantes en
la mano, no leyendo.

## Restricciones que no se negocian

- **No se escribe código de producto hasta registrar la decisión de seguir o no**
  (d-7c8794-346170). Lo único que se programa antes son probes en `probes/`. Si esto
  se rompe, el código queda construido sobre un camino que nadie validó. Se revisa
  en cada revisión.
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

## Archivos que no se editan a mano

- `.agents/`, salvo `carrier.toml` y `tracking/`. El resto es de la versión del
  paquete, y `scripts/check.sh` falla si cambia.

## Comandos

```bash
scripts/check.sh                                              # el chequeo: bundle.py verify + ids
/opt/homebrew/bin/python3.14 .agents/tools/bundle.py id d "…"  # id de una decisión (i = roadmap, s = changelog)
```

`bundle.py` necesita Python 3.11 o superior; el `python3` por defecto de este equipo
es 3.9 (d-7c8794-3b6b73). Todavía no hay build ni tests, porque no hay código.

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
| ¿Qué se midió? | `docs/research/experimentos/` |
| ¿Qué fuentes cambiaron una decisión? | `docs/references.md` |
| ¿Qué ya está decidido? | `docs/decisions.md` |
| ¿Qué sigue y con qué choca? | `docs/roadmap.md` |
| ¿Qué hizo cada sesión? | `.claude/logs/agent-changelog.md` |
| Un cambio toca estado, un contrato, datos, seguridad o verificación | Antes de decidir el diseño y antes de darlo por terminado, consulta `.agents/knowledge/INDEX.md`. Aplica cada tarjeta a la que te lleve (qué afirma, dónde deja de aplicar, cómo se comprueba). Abre la nota completa solo si no está claro dónde deja de aplicar aquí. Si este repositorio fija una invariante que contradice una nota, sigue al repositorio y dilo |
| Privacidad | Nada que se escriba en `.agents/` ni en un archivo que salga de este repositorio puede identificar, directamente o por deducción, un repositorio privado, a sus personas o a sus usuarios. `bundle.py privacy .agents` lo revisa (va dentro de `scripts/check.sh`) |
