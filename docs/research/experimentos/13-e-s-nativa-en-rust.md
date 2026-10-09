# 13 · E/S nativa en Rust contra la tubería de hoy: prueba de concepto

**Pregunta:** si el audio entra y sale de PipeWire desde un hilo de tiempo real nativo (dos
`pw_stream` en el mismo `node.link-group`, al estilo `module-loopback`) en vez de pasar por
`pw-record` → Python → `pw-play`, ¿desaparecen los cortes de "motor tarde" y "tubería vacía" y
el posible desfase de relojes entre la captura y la salida, y cuánto baja la latencia?
Roadmap i-7c8794-fd9732; decisión d-7c8794-36dde5; diseño en
[research/12](../12-motor-de-audio-en-rust.md) §2.1 y §4.

**Estado (2026-10-09): la parte sin parlantes, MEDIDA en `HP-O16` contra un sink de prueba** (la
opción A de [research/12](../12-motor-de-audio-en-rust.md) §6): cumple todo lo que se pudo medir
(§4, Veredicto). **Falta la parte con los 3 Go 4** en `PC-Ryzen5`: el A/B contra la tubería de hoy,
los descartes de radio, la latencia acústica de A y de B, y el driver con `aurasync_salida` y los
bluez de destino en vez del sink nulo.

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

En `probes/17-e-s-nativa-rust/README.md`. La parte sin parlantes (§4) usó un protocolo reducido,
escrito abajo, porque `HP-O16` estaba en uso: no se tocó el servicio, ni los audífonos, ni el sink por
defecto.

## 4. Resultados de la parte sin parlantes (`HP-O16`, 2026-10-09, 16:13–16:45)

Datos crudos, scripts y la lista de cambios de sistema en
[`datos/13/`](datos/13/) (`cambios-de-sistema.txt` tiene cada cambio, su reversión y la verificación).

### 4.0 Entorno y montaje

- **Equipo:** `HP-O16`, Intel i5-11400H (12 hilos), kernel `7.2.9-1-cachyos` (`PREEMPT_DYNAMIC`),
  PipeWire 1.6.9, WirePlumber 0.5.18, rtkit 0.14; `ulimit -r` = 99, `sched_rt_runtime_us` = 1 000 000.
  `cargo`/`rustc` 1.99.0, `clang` 23.1.1, `pipewire-rs` 0.10.1 (fijado). Sin paquetes instalados.
- **Lo que corría al lado:** el servicio `aurasync` con Spotify sonando en él y el monitor hacia los
  WH-CH520 (sin tocar); otros agentes con `pytest` a nice 15 (carga ~3–5 sin la nuestra).
- **El destino:** en vez de `aurasync_salida` y los Go 4, un sink nulo de prueba,
  `aurasync_poc_test`, de 4 canales `AUX0…AUX3`, con `priority.session=0 priority.driver=0`
  (`pactl load-module module-null-sink`, módulo 536870916, quitado al final). La PoC con
  `--target aurasync_poc_test --node-name aurasync_poc` y 3 parlantes (`datos/13/poc-config.json`):
  pan −1, +1 y 0; retardos 0, 0 y **480 muestras** (el tercero, de control para la latencia).
- **La entrada:** un `pw-play` de prueba (`node.name=poc-test-player`, con `node.dont-fallback`,
  `dont-reconnect` y `dont-move`) con ruido rosa a −47 dBFS RMS, o clics. Cada arranque comprobó con
  `pw-link -l` que el reproductor llegaba solo a `aurasync_poc` y la salida de la PoC solo al sink
  de prueba (`sesion.sh`, `latencia.sh`), y un vigilante miró cada 3–5 s el sink por defecto, el
  enlace Spotify → `aurasync` y los enlaces de la PoC desde B1 (en bash hasta las 16:30, después
  `vigilar.py`; §4.9).
- **Prioridad:** compilación, tests, `bench`, la carga y los reproductores a `nice -n 19`; la PoC a
  prioridad normal (es la medición del callback; su hilo de datos va en `SCHED_FIFO` igual).

### 4.1 Compila y enlaza en Linux (VERIFICADO)

`cargo build --release --locked` compiló y enlazó con `libpipewire` 1.6.9 al primer intento, **sin
cambiar una línea** de `pw_io.rs` (24 s). `cargo test --workspace --locked`: 26 tests verdes (8 + 2 + 7
+ 9), ahora con `pw_io` compilado y enlazado; `cargo clippy --workspace --all-targets -- -D warnings` y
`cargo fmt --all --check`: limpios. El costo del motor (`bench`, MEDIDO, `datos/13/bench-hp-o16.txt`):

| Parlantes | Cuántum | Mediana | p99 | Máx | Mediana / cuántum |
|---|---|---|---|---|---|
| 3 | 1024 | 7,3 µs | 10,6 µs | 26,9 µs | 0,034 % |
| 3 | 2048 | 14,3 µs | 18,6 µs | 21,0 µs | 0,034 % |
| 4 | 2048 | 19,2 µs | 30,5 µs | 36,8 µs | 0,045 % |

Unas 2200–3000 veces el tiempo real, como en el Mac (README §2).

### 4.2 El callback corre en tiempo real (MEDIDO)

`ps -L` (y `chrt -p` en `B0`) en las tres corridas en que se miró (`B0`, `B1`, `B2`;
`datos/13/*-hilos.txt`): el hilo `data-loop.0` de `poc-pw` en **`FF` 83**
(`SCHED_FIFO|SCHED_RESET_ON_FORK`), el principal y el de control en `TS`. La prioridad 83 es la de
`rtprio-client` (research/12 §2.2); INFERIDO que `module-rt` la pone directo, sin rtkit, porque el
límite `RTPRIO` del usuario es 99.

### 4.3 Un solo driver (MEDIDO)

- `pw-dump`: `aurasync_poc` y `aurasync_poc_output` con `node.driver-id` = 117, el sink de prueba
  (`datos/13/B0-drivers.txt`), y el mismo `node.group`/`node.link-group` `aurasync-poc-<pid>`.
- `pw-top`: los dos colgando de `aurasync_poc_test` con ` + ` (`B0-pwtop.txt`, `B1/B2-pwtop-*.txt`).
- El registro: `same_driver` = `true` en **599 de 599** intervalos de B1 y **599 de 599** de B2, y el
  `clock.id` del driver siempre 117 (`clock.system.monotonic`, el temporizador del sink nulo) en los dos
  streams.

El supuesto central del README §9 —dos streams de un **cliente** con `node.link-group` quedan bajo el
driver del nodo al que se enlaza la salida— queda **VERIFICADO con un sink nulo como driver**. Con
`aurasync_salida` (combine-stream) y los bluez como driver es INFERIDO lo mismo (las mismas reglas de
`context.c`), y se ve en el primer arranque con los Go 4.

Dos observaciones al margen:
- El README §4.4 esperaba ` = ` (asíncrono) para la captura, que lleva `PW_STREAM_FLAG_ASYNC`; `pw-top`
  la muestra con ` + `, igual que la reproducción. No cambia nada medido (la latencia de §4.5 es 0); qué
  marca exactamente ` = ` en `pw-top` 1.6.9 no se miró (el reproductor de prueba sí sale con ` = `).
- En el mismo `pw-dump`, el nodo `aurasync` del servicio (su `pw-record`) y Spotify cuelgan del driver
  120, el sink de los audífonos que recibe el monitor (VERIFICADO en `HP-O16` con el monitor encendido;
  no dice nada del caso con los Go 4 de experimentos/12 §1.1).

### 4.4 Xruns y costo del callback, 2 × 10 min (MEDIDO)

Las dos sesiones con la misma PoC, el mismo ruido entrando y el cuántum que eligió el grafo (1024 al
arrancar, 2048 con el reproductor). B2 con 12 lazos `while :; do :; done` a `nice -n 19`, uno por hilo.

| | B1, sin carga (16:20–16:30) | B2, 12 hilos de carga a nice 19 (16:31–16:41) |
|---|---|---|
| Callbacks | 14 089 | 14 090 |
| Cuántum (mín–máx) | 1024–2048 | 1024–2048 |
| Xruns propios (`no_input` + `no_output`) | **0** | **0** |
| `graph_xrun_flags`, `bad_buffer`, `trigger_failed` | 0, 0, 0 | 0, 0, 0 |
| `rt_alloc_violations` | **0** | **0** |
| Callback p50 / p99 / p99,9 / máx | 27,6 / 94,2 / 172,0 / 934,2 µs | 43,0 / 55,3 / 69,6 / 94,2 µs |
| `callback_p999_of_min_quantum` | **0,0081** | **0,0033** |
| Despertar (inicio − `clock.nsec`) p50 / p99,9 / máx | 65,5 / 262,1 / 1413,9 µs | 53,2 / 94,2 / 169,0 µs |
| ERR de `pw-top` de los 3 nodos de la PoC, inicio → fin | sin dato válido (¹) | 0 → 0 |
| Carga media al terminar (`uptime`) | 3,21 | 25,26 |

(¹) En B1 el script guardó la primera muestra de `pw-top -b`, que sale en cero antes de medir (`???`);
se corrigió para B2 (la última de tres). En B1 lo cubren `graph_xrun_flags` = 0 y los xruns propios = 0.

- **Con carga, el callback es más rápido y más parejo, no más lento.** INFERIDO: con todos los núcleos
  ocupados la CPU no baja de frecuencia ni entra en estados de reposo profundo, y el máximo de B1 (934 µs)
  y el despertar de 1,4 ms son salidas de reposo. Es el efecto contrario al de la tubería de hoy, donde la
  carga ajena retrasa al motor (179 `late` con carga 9,2, experimentos/23 §4): el hilo `FIFO` le gana a
  cualquier proceso `TS`. Ninguna de las dos cifras se acerca al 25 % del cuántum (5,3 ms con 1024).
- **El servicio no se enteró:** el ERR de `pw-top` de sus nodos quedó igual antes y después de cada sesión
  (`aurasync` 0, `bluez_output…` 0, el `pw-play` del monitor 39, ya en 39 a las 16:13). Sus contadores de
  `late` no se leyeron (la API pide credenciales).
- Otras corridas cortas, sin xruns propios ni violaciones: el arranque (`B0`, 213 s, con cuántum 1024 y
  2048) y la latencia con cuántum forzado a 256 y 2048 (`L-q256`, `L-q2048`; p99,9 del callback 24,6 µs =
  0,0046 del cuántum de 256).

### 4.5 Latencia que agrega la E/S nativa (MEDIDO)

**Método.** El sink de prueba tiene 4 canales: `AUX0…AUX2` reciben las tres salidas de la PoC y `AUX3`,
con un `pw-link` a mano, la misma salida izquierda del reproductor que entra a `aurasync_poc`. Un solo
`pw-record` graba el monitor de los 4 canales, así que todo queda en un reloj y sin el sesgo de arranque de
dos procesos (el problema de `latencia.py`). El reproductor toca 40 clics de una muestra, uno cada 0,5 s;
`analizar-latencia.py` empareja cada clic de `AUX3` con el primero que llega en cada salida de la PoC. El
retardo de 480 muestras del tercer parlante es el **control**: si el método funciona, tiene que aparecer
exacto.

| Cuántum | Corridas | `AUX0` y `AUX1` (pan −1 y +1) | `AUX2` (control, 480) |
|---|---|---|---|
| 1024 (el del grafo) | 3 × 40 clics | **0 muestras** en 120 de 120 | 480 en 120 de 120 |
| 256 (`--latency 256/48000`) | 2 × 40 | 0 en 80 de 80 | 480 en 80 de 80 |
| 2048 (`--latency 2048/48000`) | 2 × 40 | 0 en 80 de 80 | 480 en 80 de 80 |

(`datos/13/latencia.jsonl`.) **La PoC no agrega ni una muestra**: la captura, el `trigger_process` y la
reproducción caen en el mismo ciclo del grafo que el camino directo, en los tres cuánta (un parámetro que
no debería importar, y no importa). El control sale exacto en las 280 parejas. `pw_stream_get_time_n`
da `delay` 0 para la reproducción en todos los intervalos.

**Lo que esto no es:** no es la latencia de punta a punta con los parlantes. De la aplicación al sink, B
cuesta lo mismo que tocar directo en un sink (el búfer del reproductor y el cuántum), más los retardos de
alineación que se pidan; después vienen el combinado, A2DP y el aire, que no cambian entre A y B. **A no
se midió** en `HP-O16`: habría que tocar en el sink `aurasync` que el usuario estaba escuchando. Su costo
propio sigue siendo el de research/12 §1, ~300 ms (bloque + 2 bloques de margen + el búfer de `pw-play`;
INFERIDO de los componentes, no medido con este método). El criterio «B < A medido igual» queda para
`PC-Ryzen5`, con este mismo montaje de 4 canales (un null-sink con la salida de A en un canal y la
referencia en otro) además de `latencia.py`.

### 4.6 Sin aplicación tocando (MEDIDO)

En los primeros 15 s de `B0`, sin nada entrando a `aurasync_poc`, los callbacks siguieron a 46–47 por
segundo (48 000 / 1024) con `no_input` = 0 y `same_driver` = `true`: la captura recibe silencio del grafo y
dispara la salida igual. Con el sink nulo no hay nada que se suspenda; si los Go 4 se suspenden al
pausar (README §4.6) sigue abierto.

### 4.7 `kill -9` y cierre (MEDIDO)

Con el reproductor tocando en `aurasync_poc` (`datos/13/K-kill9.txt`): tras `kill -9` del proceso,
**0 nodos** `aurasync_poc*` a los 2 s, el sink de prueba sigue, el sink por defecto sigue en `aurasync` y
los enlaces de Spotify y del monitor no cambiaron. El reproductor de prueba, con `node.dont-reconnect`,
nunca se enlazó a otro sink: WirePlumber lo destruyó («defined target not found») y `pw-play` terminó,
como el monitor del servicio en experimentos/23 §7. Cada cierre con SIGINT (B0, B1, B2, L-q256, L-q2048)
escribió el resumen, y al final no quedó ningún nodo de la PoC.

### 4.8 Nunca por defecto (VERIFICADO)

`pactl get-default-sink` = `aurasync` al empezar y al terminar cada corrida, y `pw-metadata -n default`
idéntico antes (16:13) y después (16:45) (`base-metadata.txt`, `fin-metadata.txt`). `verificar.py --destino
aurasync_poc_test`: todo `ok` salvo «0 salidas del sink combinado», esperado (un sink nulo no tiene
salidas hacia `bluez_output.*`). Al final, módulos, enlaces y metadata idénticos a los del comienzo
(`cambios-de-sistema.txt`).

### 4.9 Lo que salió mal

- **Un falso aviso del vigilante.** El primero (en bash) avisó «el reproductor enlazado a otro lado»
  cuando el reproductor ya no existía: en `HP-O16` `grep` es **ugrep 7.8.4**, que con `-v -q` y entrada
  vacía devuelve 0 (GNU grep devuelve 1). Se comprobó a mano que nada se había movido y se cambió por
  `vigilar.py`. Las comprobaciones de los scripts tenían esa misma forma, pero del lado seguro (con
  entrada vacía abortan, no siguen).
- La primera grabación de latencia salió con `--channel-map aux0,…`, que `pw-record` no reconoce (pide
  `AUX0`): se descartó y se repitió.
- Los scripts de `datos/13/` llevan las rutas del scratchpad de la sesión para los WAV de prueba.

## Veredicto

**Parte sin parlantes (`HP-O16`, sink de prueba): cumple todo lo medido.** Contra los criterios de §2 y
de research/12 §6.4:

| Criterio | Resultado |
|---|---|
| Compila y enlaza en Linux | **sí**, sin cambios en `pw_io.rs`; 26 tests, clippy y fmt limpios (VERIFICADO) |
| 2 × 10 min con 0 xruns propios y ERR quieto; `rt_alloc_violations` = 0 | **sí**, con y sin carga (MEDIDO) |
| Callback p99,9 < 25 % del cuántum | **sí**: 0,81 % sin carga, 0,33 % con carga (MEDIDO) |
| `same_driver` siempre `true`, con el hilo en `FF` 83 | **sí** (MEDIDO), con un sink nulo como driver |
| Nunca sink por defecto; el nodo desaparece tras `kill -9` | **sí** (VERIFICADO / MEDIDO) |
| Latencia de B menor que la de A, medida igual | **B agrega 0 muestras** (MEDIDO); A no se midió aquí: **pendiente** |
| Descartes de radio de B dentro de un factor 2 de A; 0 cortes oídos | **pendiente** (Go 4) |

**Qué decide.** `pipewire-rs` 0.10.1 sirve tal cual: no hace falta el *shim* en C (README §10). El patrón
`module-loopback` desde un cliente funciona: un driver, el callback en `SCHED_FIFO` y sin latencia propia,
y la carga ajena no lo toca. Eso confirma con números la razón de fondo de research/12 §6.2 (el hilo RT le
gana a la CPU ajena) y deja en pie el orden de obra de §6.3 (C: `RustMotor` primero, la E/S nativa
después). **No dice nada de los cortes de radio**, que son la causa más probable de los microcortes.

