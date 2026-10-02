# 13 · E/S nativa en Rust contra la tubería de hoy: prueba de concepto

**Pregunta:** si el audio entra y sale de PipeWire desde un hilo de tiempo real nativo (dos
`pw_stream` en el mismo `node.link-group`, al estilo `module-loopback`) en vez de pasar por
`pw-record` → Python → `pw-play`, ¿desaparecen los cortes de "motor tarde" y "tubería vacía" y
el posible desfase de relojes entre la captura y la salida, y cuánto baja la latencia?
Roadmap i-7c8794-fd9732; decisión d-7c8794-36dde5; diseño en
[research/12](../12-motor-de-audio-en-rust.md) §2.1 y §4.

**Estado: prueba de concepto en preparación, sin medir.** Se corre en `PC-Ryzen5` con los 3 Go 4.

## 1. Qué se compara

- **A, la tubería de hoy:** el servicio con la cadena reducida a lo que hace la prueba de
  concepto: ambiente, separación y ecualización apagados, los mismos pan y retardos.
- **B, la prueba de concepto** (`probes/17-e-s-nativa-rust/`): un motor mínimo en Rust (mezcla
  por parlante, retardo entero, ganancia y silencio) con E/S nativa hacia el mismo sink combinado.

Los dos con la misma canción, las mismas posiciones y el registro de radio de
[experimentos/12](12-microcortes-con-3-go-4.md) encendido, para separar los descartes de radio,
que ninguno de los dos puede evitar.

## 2. Criterios (escritos antes de medir)

- En B, 2 × 10 min: **0 xruns propios** y el ERR de `pw-top` del nodo sin moverse.
- El callback de B en p99,9 por debajo del **25 % del cuántum**.
- Los descartes de radio de B dentro de un factor 2 de los de A: la E/S no debería empeorar la
  radio.
- **Latencia de punta a punta de B menor que la de A**, medida con el mismo método en los dos.
- `pw-top` muestra los nodos de B, el combine-stream y los bluez **bajo el mismo driver**.
- **B nunca queda como sink por defecto**, se comprueba antes y después (la trampa de
  experimentos/09). Y tras `kill -9` el nodo desaparece.

Si B cumple y A no, la E/S nativa se adopta como paso 4 del plan de research/12. Si los dos
cortan igual, la causa está en la radio y Rust se justifica por la latencia, la Pi y Auracast, no
por los cortes.

## 3. Protocolo

En `probes/17-e-s-nativa-rust/README.md`.

## 4. Resultados

Pendiente.

## Veredicto

Pendiente.
