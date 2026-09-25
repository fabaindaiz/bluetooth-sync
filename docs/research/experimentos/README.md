# Experimentos

Un archivo por experimento del roadmap (`docs/roadmap.md`, Fase 1), con el nombre
`NN-<experimento>.md`. Cada archivo registra:

- **Pregunta:** la pregunta que responde y el id del roadmap (`i-7c8794-…`).
- **Entorno:**
  - el equipo y el chip Bluetooth;
  - las versiones de kernel, BlueZ, PipeWire, WirePlumber y Bumble;
  - el firmware de cada JBL;
  - la fecha.
- **Qué se ejecutó:** los comandos exactos. El código queda en `probes/<nombre>/`
  hasta que el resultado está anotado aquí, y después se borra (d-7c8794-3208b7).
- **Resultado:** marcado **MEDIDO**, con el número y la unidad. Si no hay número,
  se dice qué se observó y cómo.
- **Veredicto:** si confirma, mueve o refuta lo que dice `docs/research/`, y qué
  entrada del roadmap o de las decisiones cambia.

Cuando no se logra medir algo, **se escribe igual**, junto con el motivo. Un
experimento fallido también es un dato.
