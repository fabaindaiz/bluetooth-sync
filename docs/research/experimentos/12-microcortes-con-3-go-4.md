# 12 · Microcortes con 3 Go 4: qué parte es la radio

**Pregunta:** ¿de dónde salen los microcortes que oye el usuario con los 3 Go 4 ("constantes,
a veces", 2026-10-02)? En particular: ¿cuántos son paquetes SBC que PipeWire descarta porque el
enlace Bluetooth no da abasto, y de qué depende eso (carga del controlador, el enlace de un
parlante, la posición, el escaneo LE)? Roadmap i-7c8794-7d4aec; decisión d-7c8794-0022c6; spec
`superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §3.

**Estado: protocolo listo, sin medir.** Se corre en `PC-Ryzen5` con los parlantes.

## 1. Por qué la radio es la sospechosa principal. VERIFICADO (código) e INFERIDO

- En PipeWire 1.6.9 (`spa/plugins/bluez5/media-sink.c`), cuando el socket del enlace está
  lleno el sink **descarta el paquete** y escribe `reduce bitpool` en nivel debug (l. 1090), a
  lo más una vez cada 0,5 s. `increase bitpool` (l. 1164) sale cada segundo con el enlace
  sano. Detalle y mapeo del puntero al parlante: `probes/14-microcortes/README.md` §1–4 y
  [research/11](../11-procesamiento-calidad-canales-y-panel.md) §3.2.
- [experimentos/10](10-servicio-de-control-con-3-go-4.md) §5.5 contó 62 "bajadas" en 50 s con
  los tres sonando. Si eran `reduce`, son ≥1,2 cortes por segundo; no se guardó cuáles se
  contaron (ver la corrección en §5.1 y §5.5 de ese experimento).
- Cada descarte son ~24 ms de audio con `write_mtu` ≈ 895 y bitpool 40, hasta ~40 ms con
  bitpool bajo (INFERIDO; el `write_mtu` real sale del log).
- La tarjeta Cortes del panel solo veía lo que pasa antes de PipeWire (motor tarde, tubería
  vacía, xruns, entrada que se corta). **Un corte de radio no dejaba rastro.**

### 1.1 Otras dos causas candidatas, encontradas al investigar Rust (2026-10-02)

**El motor llegaba tarde por la lectura sinc. MEDIDO en el Mac (A18 Pro).** Desde que la
interpolación del retardo pasó a sinc ([experimentos/10](10-servicio-de-control-con-3-go-4.md)
§8, 2026-10-01 noche), `dsp/interpolation.read` evaluaba una función de Bessel por muestra y por
coeficiente: **4–7 ms por bloque y por parlante**. El motor completo quedaba en ~5–6× tiempo real
(el 63× de experimentos/10 §1 se midió antes del cambio), y con la máquina cargada su p99 (146 a
202 ms, medido por el investigador) superaba el bloque de 85 ms: es el mecanismo de los cortes
"motor tarde" y "tubería vacía". Se corrigió el 2026-10-02:
- con el retardo quieto, los pesos se calculan una vez por fracción distinta, con la misma
  fórmula: la salida es **idéntica**;
- en una rampa, el núcleo sale de una tabla con interpolación de Lagrange: dentro de 1e-10;
- **lectura: 0,38–0,6 ms quieta y 1,5 ms en rampa; motor completo con 3 parlantes y EQ, mediana
  2,7 ms y p99 3,4 ms por bloque: 32× tiempo real** (Mac, con otros procesos corriendo).
  Tests: `host/tests/test_interpolation.py`, y el golden del motor sigue pasando.

Si los microcortes que oyó el usuario eran de este tipo, deberían desaparecer con este cambio:
**C2 es también la medición de antes contra después** (la tarjeta Cortes separa "motor tarde"
de "radio").

**El reloj de la captura y el de la salida podrían ser distintos (INFERIDO, leyendo
`context.c` de PipeWire).** El sink virtual (`pw-record`) y la salida (`pw-play` → combine-stream)
no están enlazados entre sí, así que PipeWire puede ponerlos bajo drivers distintos (por ejemplo
el micrófono USB cuando el lazo graba). Si pasa, la tubería entre los dos se llena o se vacía de
a poco, y cada tanto da un corte (del orden de una vez cada 30–60 min con ~20–50 ppm). **Se
comprueba en el paso 0** con `pw-top` mientras suena: la columna del driver de los nodos
`aurasync` y `aurasync_salida` tiene que ser la misma.

## 2. Lo que se construyó para medirlo (2026-10-02, en el Mac)

- `host/src/aurasync/radio.py`: el monitor que sigue el journal de WirePlumber, cuenta los
  descartes por parlante con su bitpool, y `LogLevel`, que sube el nivel de log **solo para
  los topics bluez5** (`wpctl set-log-level "spa.bluez5.sink.media:D,spa.bluez5:D,<previo>"`),
  anotando el cambio y su reversión **antes** de hacerlo, y lo revierte al apagar.
- `cuts.py`: el corte de tipo `radio`, y la causa probable "radio: el enlace Bluetooth de X
  descartó paquetes" cuando la mitad o más de los cortes caen junto a un descarte.
- `probes/14-microcortes/sesion.py` (una sesión de N minutos contra el servicio vivo) y
  `comparar.py` (el criterio de repetición).
- Tests: `host/tests/test_radio.py` (25, cada uno visto fallar con la implementación rota a
  propósito), con fixtures que citan la línea del código de PipeWire de donde salen.

## 3. Protocolo

El paso a paso está en `probes/14-microcortes/README.md` §5. En resumen:

1. **Paso 0 (una vez):** verificar que el journal de usuario trae las líneas con el formato
   que espera el monitor, guardar una muestra en `datos/12/journal-muestra.jsonl`, anotar el
   `write_mtu` real y que el mapeo nombre a los tres Go 4.
2. **C2, línea de base:** 2 × 10 min, misma canción, mismas posiciones, batería anotada, cada
   corte oído anotado con su hora.
3. **C3, una variable a la vez** (2 × 10 min cada una): **el Wi-Fi del AX210 bloqueado con
   `rfkill`** (la más barata: hay reportes de cortes de A2DP por coexistencia en AX200/AX210
   aunque el equipo esté por cable, REPORTADO en research/13; "no conectado" no es lo mismo que
   bloqueado), dos parlantes, posiciones intercambiadas, el peor a 1 m, escaneo Bluetooth a
   propósito. Un buffer más grande en los
   sinks no se puede probar: no hay propiedad en 1.6.9.
4. **Criterio:** se adopta solo lo que baja los descartes en **las dos** repeticiones; un
   descarte cuenta si la tasa del parlante se repite dentro de un factor 2 entre sesiones.
5. **Revertir** el nivel de log al terminar el día.

**Objetivo:** 0 descartes en 20 min con 3 Go 4; si no se llega, el mínimo medido y su causa.
Si todo apunta a la carga del controlador, la salida son dos controladores: es hardware y se
conversa antes de comprar nada.

## 4. Resultados

Pendiente.

## Veredicto

Pendiente.
