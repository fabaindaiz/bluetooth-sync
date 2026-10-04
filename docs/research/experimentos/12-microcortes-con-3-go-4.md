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

### 4.1 Sesión del 2026-10-02 (noche), `PC-Ryzen5`: lo oído, y lo que se descartó sin parlantes

**Entorno:** `PC-Ryzen5`, kernel 7.2.8-2-cachyos, PipeWire 1.6.9, WirePlumber 0.5.18, BlueZ
5.87, aurasync 0.0.0 en `489fc8a`; 3 Go 4 por A2DP (SBC), salida `combinado`, bloque 4096,
`pw-play` 50 ms. Firmware de los Go 4: no se anotó. Cadena: ambience `avendano_jot`, diffuse
`noise_tail`, bass `protect` (80 Hz, orden 8, armónicos -14 dB), limiter `peak`, eq
`boost_only`, decorrelate `group_delay`, volume `avrcp`.

**Lo oído. REPORTADO (usuario).** Durante la reproducción empezaron "microcortes constantes,
como 2/s", que se iban al reiniciar. Los logs muestran que lo reiniciado fue **la sesión de
aurasync** (stop y start desde el panel, 23:43:02 y 00:11:28), no PipeWire, WirePlumber ni
bluetoothd: systemd no registró ningún reinicio de esos servicios. A las 00:27:07–00:27:13 los
tres transportes A2DP fallaron en 6 s (`Failure in Bluetooth audio transport` en WirePlumber,
sin errores del controlador en el kernel). Logs: `~/.local/share/aurasync/sesiones/2026-10-02-PC-Ryzen5/`
(fuera del repositorio).

**Lo que no quedó.** El registro de radio estaba apagado (`radio_log.active` false, ninguna
línea de bluez5), y los eventos de `cuts.py` vivían solo en memoria: se perdieron al cerrar
cada sesión. **No hay ningún número de los cortes de esa noche.** Desde este cambio el
servicio los escribe en su log (`host/src/aurasync/cut_report.py`): una línea cada 10 s con
fallas (cuántas por tipo, el nivel mínimo de la tubería, el último atraso del motor, la causa
probable) y el total al cerrar la sesión.

**Descartado: el procesamiento no se queda sin tiempo. MEDIDO en `PC-Ryzen5`, sin parlantes.**
Con la instalación y la cadena reales (`~/.config/aurasync/`), entrada sintética:

| Qué | Mediana | Peor | Presupuesto |
|---|---|---|---|
| `Motor.procesar`, 3 parlantes, 15 min de audio (10 500 bloques) | 5,33–5,38 ms | 10,5 ms | 85,3 ms |
| `AudioSession.step` completo (motor, medidores, telemetría, calidad), 15 min | 7,5–7,7 ms | 14,9 ms | 85,3 ms |
| `aurasync service --simular` con un `/v1/stream` abierto, 30 min en tiempo real | 6,1–6,4 ms | 15,6 ms | 85,3 ms |

En los dos primeros, ningún bloque pasó de la mitad del presupuesto, y el costo no crece con el
tiempo. En los 30 min del servicio simulado hubo 7 cortes `late` (43–66 ms tarde), **todos
mientras `scripts/check.sh` corría los tests en paralelo** (01:05:06–01:09:11) y ninguno fuera
de ese rato: otro proceso que satura la CPU atrasa al motor, pero no tanto como para vaciar la
tubería. La tubería
hacia `pw-play` tiene 220 ms (`pipe_size_ms`): para vaciarla, el hilo del motor tendría que
trabarse más de 200 ms, dos veces por segundo. La cadena del usuario cuesta lo mismo que la de
fábrica (5,4 ms las dos). Parsear un `pw-dump` de este equipo (250 KB) toma 1 ms.

**Encontrado de paso, sin ser la causa. MEDIDO.** `QualityMeter.summary` recalcula la sonoridad
integrada desde el inicio de la sesión, en el hilo del motor, cada 0,5 s: 0,33 ms al principio y
1,9 ms a los 15 min, lineal. A las 4 h serían ~30 ms.

**Corregido el 2026-10-03, con la regla del usuario: el stream se procesa siempre sobre datos de
largo fijo, y el historial va aparte y asíncrono.** Lo que dependía del historial en el hilo del
motor:

| Qué | Antes | Ahora |
|---|---|---|
| `QualityMeter.summary`, cada 0,5 s | 0,33 → 1,90 ms en 15 min, lineal; 5 listas sin tope (~3 MB a los 30 min) | 0,13–0,14 ms planos en 15 min (MEDIDO); los medidores guardan 3 s, y la sonoridad integrada la lleva `LoudnessHistory` en su hilo, con un histograma de memoria fija (`GatedIntegrator`, dentro de 0,01 LU del cálculo completo) |
| `CutLog.summary` en la foto, cada bloque | 0,001 ms vacío, 0,68 ms con el registro lleno (MEDIDO) | el hilo `aurasync-cut-summary` lo arma (al llegar un corte, o cada 1 s) y la foto lee el último |
| `CutReporter`, cada bloque | copiaba el registro entero | lee solo lo nuevo (`CutLog.since`) |
| `Room` de `--simular` | guardaba cada bloque si nadie leía el micrófono (~690 MB en 30 min, estimado) | 30 s como máximo |

Revisado y descartado (está acotado o no depende de la sesión): `telemetry`, el historial del
limitador, los buffers del DSP, los cachés (`lru_cache`, todos con tope), los subprocesos y los
hilos (uno por sesión o por orden, todos cerrados). Lo que sí crece a lo largo del servicio y no
se reinicia con la sesión (`RadioMonitor._links`, `LogBuffer`) no explica que reiniciar la
sesión lo arregle. Con el cambio, `aurasync service --simular` 15 min en tiempo real con un `/v1/stream` abierto
(MEDIDO, `PC-Ryzen5`): 0 cortes, motor 5,85–6,04 ms, 19 hilos y 6 descriptores todo el rato, sin
procesos hijos, y la memoria de 96,2 a 97,2 MB (de 05:07:45 a 05:21:45, frenando).
**Nada de esto explica solo dos cortes por segundo:** el peor caso medido era
de ~2,6 ms por bloque de 85 ms. Si los cortes vuelven, el log nuevo dice de qué tipo son.

**Lectura. INFERIDO.** 2 cortes por segundo es exactamente el techo con que PipeWire 1.6.9
anota un paquete descartado por el enlace (`reduce bitpool`, a lo más uno cada 0,5 s, §1), y
experimentos/10 §5.5 había contado ~1,2 por segundo con los tres sonando. Que reiniciar la sesión
lo arregle calza con la deriva de reloj de §1.1 (la tubería vuelve a llenarse), aunque esa
deriva daría cortes esporádicos y no dos por segundo. Si también calza con la radio no está
verificado: no se sabe si recrear los streams devuelve el sink Bluetooth a su estado inicial.
Lo que las separa es correr C2 con el registro de radio prendido **antes** de que empiece
a sonar.

## Veredicto

Pendiente.
