# 12 · Un motor de audio en Rust para el camino crítico, con Python para el resto

Investigación del 2026-10-02, desde el **Mac** y **sin compilar Rust**: no hay `cargo` en este
equipo y no se instaló. Todo sale de leer código (PipeWire 1.6.9, pipewire-rs, cpal, CamillaDSP,
EasyEffects, PyO3, maturin, hatch) y de mediciones en Python. Responde lo que pidió el usuario:
**¿conviene llevar a Rust la parte crítica del audio y dejar el resto en Python? ¿Con qué
topología, cómo se haría y en qué orden?** Roadmap i-7c8794-fd9732. Afecta a d-7c8794-c23c20
(Python + hatch), que dice "se reabre si P2 exige Rust para el backend de Linux".

**Marcas:** VERIFICADO (código o documentación primaria), REPORTADO, INFERIDO, MEDIDO (con el
entorno).

## Resumen

1. **Antes de hablar de Rust, apareció una causa de cortes que se arregla en numpy.** La lectura
   sinc del retardo (experimentos/10 §8) evaluaba una función de Bessel por muestra y por
   coeficiente: el motor estaba a ~5–6× el tiempo real, no a 63×, y con la máquina cargada su
   p99 pasaba el bloque de 85 ms. **Ya está corregido**: 32× con 3 parlantes y EQ, salida
   idéntica con el retardo quieto ([experimentos/12](experimentos/12-microcortes-con-3-go-4.md)
   §1.1). MEDIDO en el Mac.
2. **Rust no arregla la causa más probable de los microcortes**: los paquetes SBC que descarta
   el sink Bluetooth de PipeWire, después del motor (research/11 §3.2). Lo que sí elimina por
   construcción son los cortes de tipo "motor tarde" y "tubería vacía", y la posible diferencia
   de reloj entre la captura y la salida (§2.1).
3. **Lo que Rust aporta y numpy no puede dar:** un hilo de **tiempo real** sin GIL dentro del
   ciclo del grafo de PipeWire; **E/S nativa** sin `pw-record`/`pw-play` (latencia de ~300 ms a
   ~75 ms, INFERIDO); poder ser **driver del grafo** (el reloj del BIG para Auracast, P2); y
   **margen en la Raspberry Pi** de la Fase 3, donde el jitter de Python es lo que no cabe.
4. **La topología recomendada:** el núcleo DSP en Rust, validado contra el motor numpy como
   **oráculo** (el golden, ≤1e-9 en f64), y la E/S nativa al estilo de `module-loopback`
   (captura y reproducción en el mismo `node.link-group`, `RT_PROCESS`). Python se queda con el
   servicio, el contrato, el panel, la cadena (descriptores y diseño de filtros), la calibración,
   el lazo de decisión y Bumble.
5. **La decisión abierta** es si el motor Rust vive **dentro del proceso de Python** (extensión
   PyO3: más simple, más testeable; un segfault tumba el servicio) o en **un proceso aparte**
   (aislamiento total; IPC y supervisión). Los dos investigadores discrepan (§3).
6. **Esfuerzo estimado: 9–17 semanas-persona** (INFERIDO), sin contar las sesiones con
   parlantes; el núcleo DSP son 4–7.

## 1. Lo que hay hoy y dónde duele

Camino actual: apps → sink virtual `aurasync` (un `pw-record` con `media.class=Audio/Sink`) →
tubería → hilo de Python (numpy, bloques de 4096) → tubería → `pw-play` → sink combine-stream →
nodos bluez5 → Go 4.

| Problema | Causa | ¿Lo resuelve Rust? |
|---|---|---|
| El motor llegaba tarde | la lectura sinc cara (ya corregido) y el jitter del hilo de Python con GIL (hasta 114–125 ms entre bloques de 85 ms, experimentos/10 §9.2) | sí, el jitter: el `process` corre en el hilo RT de PipeWire (`rtprio-client` 83, VERIFICADO) |
| La tubería se vacía | margen de 2 bloques que absorbe el jitter | sí: no hay tubería |
| Dos relojes (INFERIDO) | el sink virtual y la salida no están enlazados; PipeWire puede ponerlos bajo drivers distintos (`context.c` l. 1475–1555, VERIFICADO el mecanismo) | sí: con `node.link-group` compartido, todo queda bajo el driver del combine-stream |
| ~300 ms de latencia | bloque + 2 bloques de margen + buffer de `pw-play` | sí: ~1 cuantum + ~30 ms de algoritmo |
| Descartes de radio | el enlace A2DP no da abasto | **no** |
| Lip-sync con video | la latencia | en parte: baja, pero hay que declararla igual (research/11 §3.5) |

**Correcciones a lo escrito antes** (VERIFICADO en el código):
- El remuestreo adaptativo no lo hace combine-stream, sino cada sink bluez5 seguidor
  (`media-sink.c` l. 647–702).
- El README de pipewire-rs 0.10.1 ya no lleva el aviso "currently not actively maintained"
  (quitado el 2026-04-05), aunque sigue advirtiendo "expect frequent breakage".

## 2. Las opciones técnicas

### 2.1 E/S de audio en Linux

| Opción | Qué es | Veredicto |
|---|---|---|
| **Dos `pw_stream` al estilo `module-loopback`** | captura con `media.class=Audio/Sink` (las apps la ven como salida) y reproducción de N canales al sink combinado; los dos con `PW_STREAM_FLAG_RT_PROCESS` y el mismo `node.group`/`node.link-group`; la captura dispara a la reproducción | **Recomendada.** Un solo reloj y un solo ciclo; si el `process` no llega, aparece en el ERR de `pw-top`, no como silencio. VERIFICADO el patrón en `module-loopback.c` 1.6.9 |
| `pw_filter` detrás de un null-sink (EasyEffects) | un nodo con puertos | no aporta nada sobre dos streams, y el null-sink es otro driver con su reloj |
| Plugin LV2 en Rust cargado por `module-filter-chain` | corre en el hilo RT del grafo | **no**: el crate `lv2` está quieto desde 2020, nih-plug no exporta LV2, los controles son floats sueltos (no entran 2048 coeficientes de EQ) y la medición queda afuera |
| CamillaDSP 4.1.3 como motor | Rust, backend PipeWire, websocket, FIR con NEON | **no como motor**: sus nodos no son `Audio/Sink`, y le faltan el extractor de Avendaño-Jot, el retardo con rampa, el corte de 80+80 ms y la medición integrada. **Sí como referencia de código** |
| cpal 0.18 | abstracción de dispositivos (PipeWire, ALSA, CoreAudio) | no puede crear un nodo `Audio/Sink`; sirve para el micrófono y para macOS |

**Bindings:** `pipewire-rs` 0.10.1 expone `Stream` con `RT_PROCESS`, `DRIVER` y `TRIGGER`, pero no
`pw_filter` ni `pw_stream_set_rate` (solo en `pipewire-sys`, `unsafe`). Su único usuario conocido
que procesa audio en tiempo real es CamillaDSP. Se fija la versión exacta, como Bumble, y si se
rompe, la alternativa es un *shim* en C de ~200 líneas sobre las ~15 funciones que hacen falta.
Compila con bindgen contra los headers del sistema: **la parte de E/S se compila en cada equipo**
(clang, pkg-config, libpipewire-dev); no puede ser una rueda portable. VERIFICADO.

### 2.2 Tiempo real

- **Reglas del callback** (`stream.h` l. 150, VERIFICADO): nada de reservar memoria, `Mutex`, E/S
  ni funciones de PipeWire no marcadas como RT-safe.
- **Parámetros en vivo:** las rampas de `dsp/ramps.py` se portan al hilo RT; los objetivos llegan
  por un anillo SPSC (`rtrb` 0.4.0). Lo que necesita corte (EQ nueva, banco del decorrelador) se
  arma **fuera** del hilo RT y entra como un puntero; el estado viejo vuelve por otro anillo para
  liberarse afuera (o `basedrop`). Instantáneas hacia el panel con `triple_buffer`.
- **Verificación:** `assert_no_alloc` en los tests; un histograma de la duración del callback
  contra el inicio del ciclo; `pw-top` (WAIT, BUSY, ERR), `pw-profiler`, y `chrt -p` para
  comprobar SCHED_FIFO (no se supone: regla del repo).

### 2.3 DSP en Rust

`rustfft` 6.4 + `realfft` 3.5 (SIMD, NEON en aarch64), `fft-convolver` 0.4 o una convolución
particionada propia (~150 líneas, ya existe en `dsp/eq.py`), `rubato` 5.0 (remuestreo para
Auracast), `ebur128` 0.1.10 (pasa EBU Tech 3341/3342, con true peak), biquads propios para el
crossover. **Sin rendimiento medido en Rust**: la estimación para una Pi Zero 2 W, escalando la
cifra publicada de CamillaDSP en una Pi 4, es 20–30 % de un núcleo (INFERIDO).

### 2.4 Cómo se unen Python y Rust

| | (a) Extensión PyO3 en el proceso | (b) Proceso Rust aparte |
|---|---|---|
| GIL | el hilo RT nunca lo toma | no existe del lado del audio |
| Un pánico | con `panic = "unwind"` y `catch_unwind` en cada callback: silencio, marca `failed`, el servicio sigue. **Ojo:** `PanicException` de PyO3 deriva de `BaseException` y el `except Exception` de `service._step` no la atrapa (VERIFICADO) | muere solo el motor; el servicio lo relanza |
| Un segfault (`unsafe`, libpipewire) | **tumba el servicio y el panel** | muere solo el motor |
| Control | llamadas directas, µs | socket Unix: p50 14 µs, p99 80 µs (MEDIDO en Python↔Python) |
| Señal para el lazo y la calibración | vistas numpy sin copia | memoria compartida: 64 KiB en 1,7 µs (MEDIDO); ~1,15 MB/s en total |
| Tests y simulación | `process()` síncrono: el golden, `--simular` y los tests de navegador sin cambios | necesita un modo sin E/S del binario |
| Empaquetado | rueda maturin (`abi3-py312`) | un binario de `cargo` |
| Antecedente en este repo | — | pasar la medición del lazo a otro proceso salió **peor** (experimentos/10 §9.2), aunque por serializar, no por compartir memoria |

**El protocolo es el mismo en los dos casos**, así que se puede empezar con uno y pasar al otro
sin rediseñar (INFERIDO):

- **Python → Rust:** `Configure` (la cadena ya validada, con los coeficientes que diseña Python),
  `SetSpeaker`, `SetGlobal`, `SetDelayTargets {Ramp | CutIfSlow}`, `Cut {acciones atómicas}`,
  `Inject` (el estímulo de calibración), `Tone`, `SetRate` (Auracast) y `Shutdown`.
- **Rust → Python:** eventos (`CutDone`, `Xrun {Late|Underrun}`, `StreamState`, `Failed`), una
  instantánea a 20–50 Hz (métricas por etapa, niveles, histograma de jitter) y dos anillos con
  contador de muestras: **lo que recibió exactamente cada parlante** (la referencia del lazo) y
  la entrada estéreo.
- **La cadena la sigue definiendo Python** (`chain.py`: descriptores, textos, validación, diseño
  de filtros). Rust exporta `capabilities()`, y un test la compara con los algoritmos de `CHAIN`.
- **Un `RustMotor` con la misma API pública que `Motor`** deja `service.py`, `sincronia.py` y
  `control.py` sin cambios.

### 2.5 Empaquetado y equipos

- Un paquete **aparte y opcional** (maturin), con dos crates: `aurasync-dsp` (Rust puro, rueda
  portable, compilable en cruzado desde el Mac con `--zig`) y `aurasync-io-pw` (libpipewire, se
  compila en cada equipo Linux).
- Se instala como miembro de un **workspace de hatch** (≥1.16). En hatch 1.18.1 las dependencias
  por ruta no quedan editables (issue #2448); **que un miembro con backend maturin funcione en un
  workspace de hatch no está probado: es lo primero que hay que verificar.**
- El motor numpy queda como **oráculo y respaldo** (`engine = "numpy" | "rust"`): el Mac y los
  tests siguen sin toolchain de Rust.
- `scripts/check.sh` agrega `cargo fmt --check`, `cargo clippy -D warnings` y `cargo test`.

### 2.6 macOS, la Pi y Bumble

- Un *trait* de backend de E/S (`PipeWire`, `Alsa` para el gadget UAC2 de la Fase 3, `CoreAudio`,
  `Offline`, `Auracast`): "mismo núcleo, otro backend emisor".
- **En el Mac**, A2DP no da un stream por parlante: el motor Rust ahí solo serviría para Auracast.
  cpal 0.18.2 ya captura el audio del sistema con un *process tap* de Core Audio sin BlackHole,
  pero deja sonando la salida del Mac (VERIFICADO, `loopback.rs` l. 104).
- **Bumble necesita los canales en Python**: Rust los deja en memoria compartida y Python los
  codifica en LC3. Si el jitter de asyncio no alcanza con tramas de 10 ms (riesgo ya anotado en
  08 §5.1), Rust codifica LC3 con liblc3 y entrega los paquetes hechos.

## 3. Donde discreparon los dos investigadores

| | Proceso aparte | Extensión PyO3 |
|---|---|---|
| Lo defendió | el de la E/S en PipeWire | el de la frontera y la migración |
| Por qué | un pánico o un segfault no tumban el panel; el motor podría correr en la Pi sin Python | testabilidad máxima, la simulación y el golden sin cambios, sin IPC ni supervisión nueva |
| Lo que concede | más piezas, un protocolo más, un modo sin E/S para tests | un segfault tumba todo |

Mi lectura (INFERIDA): **empezar como extensión PyO3** (rinde más mientras se porta y valida el
DSP, que es la mayor parte del trabajo), con el protocolo de §2.4 independiente del transporte, y
**pasar a proceso aparte cuando llegue la E/S nativa** si los segfaults o la Pi lo piden. Es una
decisión del usuario.

## 4. El plan, por pasos y con criterios de terminado

| Paso | Qué | Criterio de terminado | Esfuerzo (INFERIDO) |
|---|---|---|---|
| 0 | **Lectura sinc rápida en numpy** | golden ≤1e-9; motor ≥20× en el Mac | **hecho** (2026-10-02) |
| 0' | **Medir experimentos/12** (radio, motor tarde, y el reloj con `pw-top`) | cortes por causa en 2 × 10 min | ya planificado |
| **Compuerta** | Si no quedan cortes de motor ni de reloj, y la Pi y Auracast no aprietan, **Rust se posterga** | — | — |
| 1 | Andamio: `engine/` con dos crates, maturin, miembro del workspace de hatch, `check.sh`, `capabilities()` | `hatch test` verde en el Mac y en PC-Ryzen5 con y sin la extensión | 0,5–1 semana |
| 2 | DSP etapa por etapa, en f64: rampas, corte, línea sinc, FIR por FFT, decorrelador, extractor STFT, limitadores, crossover, bajo psicoacústico, cola difusa, sonoridad | cada etapa **inyectada en el `Motor` numpy** (motor híbrido) pasa el golden ≤1e-9 y se ve fallar con una falla plantada; costo por etapa medido | 3–5 semanas |
| 3 | Motor completo en Rust + fachada `RustMotor`, sin E/S | golden ≤1e-9; `test_chain*`, `--simular` y los tests de navegador verdes con `engine=rust`; ≥100× en PC-Ryzen5 | 1–2 semanas |
| 4 | E/S nativa en PipeWire (patrón `module-loopback`) y la sesión como un *tick* | con 3 Go 4, 2 × 20 min: **0 eventos "motor tarde", "tubería vacía" o "casi vacía"**; callback p99,9 < 25 % del cuantum; la prueba de cierre de la calibración sigue en ~0; la latencia medida baja; tras `kill -9`, el nodo no queda (P1) | 2–3 semanas + sesiones con parlantes |
| 5 | La Pi (ALSA/UAC2), Auracast (driver del grafo, LC3) y macOS | según la Fase 3 y E4 | 2–5 semanas |

**Variante para reducir el riesgo antes del paso 1** (la propuso el investigador de la E/S): una
**prueba de concepto desechable** de E/S nativa con un motor mínimo (matriz de ganancias y retardo
entero), ~500–800 líneas, comparada 2 × 10 min contra la tubería de hoy. Dice en pocos días si la
E/S nativa elimina los cortes de motor y de reloj, antes de invertir semanas en portar el DSP.

**Si se pasa a f32** (para SIMD en la Pi): residuo ≤1e-6 (−120 dBFS), |Δ| ≤ 0,01 dB por tercio,
igual con 2 semillas y 2 tamaños de bloque. −120 dB deja 30 dB bajo el LSB de los 16 bits que
recibe el codificador SBC.

**Lo que se pierde:** velocidad de iteración (una perilla nueva toca dos lenguajes; se prototipa
en numpy y se porta lo aprobado de oído), dos motores que mantener, y un toolchain más (Rust,
clang y los headers de PipeWire en cada equipo Linux).

## 5. Dudas para el usuario, y sus respuestas (2026-10-02)

| Duda | Respuesta |
|---|---|
| ¿Qué motiva el cambio? | todo: eliminar los cortes del motor, bajar la latencia, correr en la Raspberry Pi, Auracast, y aprender Rust |
| ¿Cuándo empezar? | **ya, con la prueba de concepto de E/S nativa** (no se espera a experimentos/12) |
| ¿Mismo proceso o aparte? | **PyO3 ahora, proceso aparte después** si la E/S nativa o los fallos lo piden |
| ¿Plataformas? | PC Linux x86_64 y la Raspberry Pi; el Mac solo para desarrollar, testear y simular |
| ¿Tolerancia de sonido? | f64 exacto (golden ≤1e-9) durante la migración; f32 a ≤ −120 dBFS después si hace falta |
| ¿El motor numpy? | queda como oráculo y lugar de prototipos |
| ¿Toolchain? | `check.sh` exige `cargo`; Rust, clang y los headers de PipeWire en los equipos Linux, rustup también en el Mac (el usuario lo instaló con Homebrew el 2026-10-02) |
| ¿Dónde va el código? | `engine/` en la raíz; la prueba de concepto, desechable, en `probes/` |

Decisión: d-7c8794-36dde5.

## Fuentes

- PipeWire 1.6.9 — https://gitlab.freedesktop.org/pipewire/pipewire/-/tree/1.6.9 : `stream.h`
  (l. 150, 464–501), `filter.h`, `context.c` (l. 1049–1063, 1470–1555), `module-loopback.c`
  (l. 341–420, 740–775, 907–910), `module-combine-stream.c` (l. 1606–1610),
  `module-filter-chain.c`, `module-rt.c`, `client.conf.in`, `media-sink.c` (l. 647–702)
- pipewire-rs — https://gitlab.freedesktop.org/pipewire/pipewire-rs (README; commits 76d589e,
  7102242) y `pipewire-sys` en docs
- cpal — https://github.com/RustAudio/cpal (0.18.x: `host/pipewire/device.rs`,
  `host/coreaudio/macos/loopback.rs`)
- CamillaDSP — https://github.com/HEnquist/camilladsp (README, CHANGELOG 4.x,
  `src/pipewire_backend/device.rs`)
- EasyEffects — https://github.com/wwmm/easyeffects (`plugin_base.cpp`, `pw_node_manager.cpp`)
- PyO3 — https://pyo3.rs/latest/parallelism.html ,
  https://docs.rs/pyo3/latest/pyo3/panic/struct.PanicException.html ; Rust 1.81 (abort en
  `extern "C"`) — https://blog.rust-lang.org/2024/09/05/Rust-1.81.0/
- maturin — https://www.maturin.rs/project_layout.html , https://www.maturin.rs/distribution.html ;
  hatch workspaces — https://hatch.pypa.io/1.16/how-to/environment/workspace/ ; issue #2448 —
  https://github.com/pypa/hatch/issues/2448
- Crates: rtrb, basedrop, triple_buffer, assert_no_alloc, rustfft, realfft, rubato, ebur128,
  fft-convolver, audio_thread_priority (crates.io, consultado el 2026-10-02)
- Mediciones propias: `scratchpad/rust2/` (perfil del motor, IPC) y `host/tests/test_interpolation.py`

## 6. Estado de la E/S nativa y siguiente paso (revisión del 2026-10-09)

Revisión de lectura, en `HP-O16`, sin tocar PipeWire ni el servicio ni el código. Marcas: VERIFICADO
(leído ahora en código o documentos), INFERIDO (razonado), REPORTADO (del encargo de esta revisión).

### 6.1 Qué hay

**La prueba de concepto (paso 4 adelantado, `probes/17-e-s-nativa-rust/`) está escrita y nunca corrió
en Linux** (a la hora de esta revisión; corrió esa tarde: §6.5). VERIFICADO:
- Se escribió el 2026-10-02 en el Mac (un solo commit, `c1e0338`); no hay `target/` y
  [experimentos/13](experimentos/13-e-s-nativa-en-rust.md) dice «Resultados: Pendiente. Veredicto:
  Pendiente». No probó nada de lo que pregunta (cortes, un solo driver, latencia).
- Lo que sí probó, en el Mac: 26 tests del motor mínimo (pan, retardo entero, ganancia, silencio; sin
  reservas en `process`), `--dry-run` contra numpy a 7,9e-9, costo ~10–27 µs por llamada (2000× tiempo
  real), y que el código de `pw_io.rs` (649 líneas, `pipewire` 0.10.1 fijado) compila y pasa clippy
  contra bindings generados de los headers de PipeWire 1.6.9 (README §2). No se enlazó nunca con
  `libpipewire`.
- Tiene dos `pw_stream` al estilo `module-loopback` (sink `aurasync_poc` + salida hacia
  `aurasync_salida`), `same_driver`, histogramas del callback, xruns propios y `verificar.py`. El supuesto
  central sigue **sin comprobar**: que dos streams de un *cliente* con `node.link-group` queden bajo el
  driver del combinado (README §9; INFERIDO de `context.c`).
- **No cubre** `separado` (un `pw-play` por parlante), el monitor, el parlante que se va y vuelve, ni las
  entradas y salidas en caliente (README §9: «la PoC no lo repara»). Hoy `HP-O16` tiene `cargo`, `clang`
  y `libpipewire` 1.6.9 (VERIFICADO con `which` y `pkg-config`): ya no falta el toolchain, y el equipo
  sin parlantes (sin los Go 4) solo sirve para compilar, medir el callback y ver el driver con un sink de
  prueba, no para los criterios de radio.

**Lo que cambió desde la pausa** (la PoC se pausó el 2026-10-02, antes de experimentos/12 y de todo esto):
- **Puente API 2** (`capabilities()["api"] == {"version": 2}`, d-7c8794-ef6117; VERIFICADO): `EnginePanic` y
  `EngineError`, la etapa Rust se llama desde Python por bloque. VERIFICADO por `grep` que el puente **no
  suelta el GIL** (sin `allow_threads`/`detach` en `engine/crates/aurasync-engine/src`): una llamada Rust
  tiene al intérprete tomado todo el bloque. Ninguna etapa de `RustMotor` (paso 15) existe: siguen
  pendientes las 13 (rampas), 14 (medidores) y 15 (`RustMotor`, «con su propio plan», roadmap
  i-7c8794-fd9732). El motor sigue siendo el `Motor` de numpy con etapas Rust inyectadas.
- **Costo por bloque de 85 ms (REPORTADO en el encargo: ~5 ms con Rust, ~11 ms con numpy).** Calza con lo
  medido: 25–35× tiempo real con Rust contra 11–14× con numpy (experimentos/20 §5, VERIFICADO), ~9 ms en
  reposo y 13,9 ms medidos en el servicio (experimentos/23 §4 y §4.1). El motor no es el problema; sobra
  margen por 6–15×.
- **Prioridad del hilo del motor en −15 por RealtimeKit** (`engine_nice`, d-7c8794-923eed, experimentos/23
  §4.1, MEDIDO): con CPU saturada baja el trabajo por bloque de 37–41 a 18 ms. Lo hereda lo que el hilo
  lanza (los `pw-play`). **No es** tiempo real: sigue siendo `TS`.
- **Re-enganche del monitor** (experimentos/23 §7, VERIFICADO el 2026-10-09 16:00): el `pw-play` del monitor
  lleva `node.dont-reconnect` (`monitor.py`, `NO_MOVE`), WirePlumber 0.5.18 lo **destruye** cuando su sink
  cambia, y el servicio lo reabre solo desde `MonitorController.watch` (~3 s). El costo es una reapertura,
  no un parche en el audio.
- **El estirador del colchón (etapa 4 de transiciones)** se está construyendo: `dsp/stretch.py` aún no
  existe (plan `2026-10-09-seamless-transitions-stage-4.md`, spec §4b). Cambia el relleno con silencio por
  un remuestreo `1+ε` (≤ +0,5 %) sobre `interpolation.read`, uno por grupo de salida. Corrige un
  desajuste de relojes que **nunca se midió** (experimentos/12 §1.1 y Veredicto: pendientes).

### 6.2 Qué resolvería la E/S nativa y qué no

| Problema | ¿La E/S nativa lo resuelve? | Evidencia |
|---|---|---|
| Latencia y tubería: ~300 ms, tubería de `pipe_size_ms` = 50 + 2 bloques hoy | **sí, por construcción** (~1 cuantum + algoritmo). Pero la latencia importa sobre todo al lip-sync; hoy la calibración la mide y la compensa | `session.py` l. 92–102, research/12 §1; INFERIDO el ~75 ms |
| Colchón (`cushion.py`) y rellenos | **cambia de naturaleza, no desaparece**: sin tubería de SO el colchón es un anillo propio, y con captura y salida en el mismo driver deja de existir el desajuste *captura–salida*. Queda el de los relojes de cada sink bluez (lo adapta cada sink con su remuestreo, research/12 §1 correcciones) | INFERIDO; nunca se midió con `pw-top` si hoy son dos drivers (exp. 12 §1.1) |
| Xruns y «motor tarde» | **solo si el hilo era la causa.** Con el motor a 5–14 ms de 85, la causa medida es CPU ajena (179 `late` con carga 9,2, exp. 23 §4); la prioridad −15 ya recorta el trabajo a la mitad | MEDIDO (exp. 23 §4, §4.1); en el servicio entero no se midió después de −15 |
| Inanición de CPU | **parcial**: el callback en el hilo de datos de PipeWire corre en `SCHED_FIFO` 83 (VERIFICADO el diseño, `rtprio-client`; el README §4.5 pide comprobarlo con `ps -L`/`chrt`), así que le gana a la carga que el nice no vence. Costo: el callback no puede esperar a Python | research/12 §2.2; INFERIDO que alcanza |
| GIL | **sí, el del camino de audio**: el `process` no toca el intérprete. Mientras el motor siga en Python+PyO3 el GIL queda (el puente no lo suelta). Es el argumento más fuerte de la E/S nativa y exige el motor **entero** en Rust | grep del puente; exp. 23 §4.1 punto 3 («lo que rodea al motor» es lo que se retrasa, INFERIDO) |
| Reconexión tras un cambio de sink (monitor) | **sí en el diseño, no gratis**: un `pw_stream` propio ve `state_changed` y decide él, sin el `dont-reconnect` que WirePlumber destruye. Pero hay que reimplementar a mano lo que ya hace `watch` y la regla de exp. 09 (nunca terminar en otro sink) | INFERIDO; el arreglo actual funciona (exp. 23 §7.1) y su única falta es medir cuánto tarda |
| Alineación entre parlantes | **no**: en `combinado` ya es un solo stream y en `separado` es un `pw-play` por nodo; el desfase que importa viene de la radio y de cada sink bluez, después de la E/S | exp. 12 §1; research/12 §1 («Descartes de radio: no») |
| Descartes de radio (SBC) | **no**; es la causa más probable de los microcortes y queda después del motor | exp. 12 §1; tabla de research/12 §1 |

### 6.3 Opciones para el siguiente paso

Las opciones se calculan en sesiones (una sesión = un día de trabajo con el usuario, INFERIDO).

| | Opción | Esfuerzo | Riesgo | Qué destraba |
|---|---|---|---|---|
| **A** | **Cerrar la PoC como estaba, medida en `HP-O16`, sin parlantes** (compilar, callback en `SCHED_FIFO`, `same_driver` con un sink de prueba, kill -9, latencia con el micrófono) y recién después decidir | 1–1,5 | bajo: no toca los Go 4, desechable (d-7c8794-3208b7); el riesgo es el de siempre, instalar rustup en `PC-Ryzen5` | responde con un número el supuesto central (§9 del README) y el costo del callback; no responde por cortes de radio |
| **B** | **Un `pw_stream` por salida desde Rust detrás de `outputs.py`/`PlayerLike`** (reemplaza `pw-play`; el motor y la sesión no cambian: `escribir(blocks)` entrega a un anillo `rtrb`, el callback lo consume) | 3–4 (primero el monitor: un stream, no sincronizado; luego `combinado`; `separado` al final) | **medio**: se pierde la tubería del SO como colchón (hay que portar `SharedCushion` a un anillo), WirePlumber y `dont-reconnect` cambian de reglas, y el hilo Python que escribe sigue con GIL, así que gana poco frente a los xruns medidos | menos procesos, una reconexión controlada por el programa, y el camino a Auracast; **no** baja latencia a lo que prometió research/12 mientras el motor siga en Python |
| **C** | **Esperar al `RustMotor` (paso 15) y hacer la E/S nativa después**, como en el plan original (paso 3 y luego 4) | 0 ahora (+1–2 del paso 15, que ya tiene su plan por escribir; +2–3 de E/S) | bajo, pero el cuello (CPU ajena, GIL) sigue hasta entonces | evita construir dos veces el colchón: el estirador (`dsp/stretch.py`) y `SharedCushion` nacen y mueren en Python si la E/S pasa a Rust |

**Dependencias.** Con **C**: el paso 4 de este documento exige el paso 3 (research/12 §4): la «sesión como
un *tick*» no se puede hacer con un motor que llama a Python. Con **B**: sirve a `Motor` y a `RustMotor`
por igual (INFERIDO: la interfaz es la de `PlayerLike`), pero **duplica** el trabajo del colchón de la etapa
4 (spec §4b): el estirador toma sus señales del nivel de la tubería (`FIONREAD`, `sonido.bytes_en_tuberia`),
que con un anillo propio pasa a ser otra lectura. El estirador mismo (`interpolation.read` con `ε`) **sí se
conserva**: ya hay `interpolation.rs`. Con **A**: ninguna; corre aparte del servicio y aprende cuál de las dos
suposiciones del estirador es cierta.

**Recomendación: A ahora, y C como orden de obra.** La E/S nativa de verdad vale (GIL, hilo RT, un reloj)
solo con el motor entero en Rust; B hoy sería gastar 3–4 sesiones en el borde con un hilo Python en el
medio. A cuesta ~1 sesión, no arriesga lo que suena y entrega lo único que falta para decidir: si los
nodos quedan en un driver y cuánto pesa el callback. No hace falta esperar a la etapa 4 del estirador
para hacerla. Si A sale mal (`same_driver` falso o callback p99,9 ≥ 25 %), la alternativa es el *shim*
en C (README §10). B se retoma **solo para el monitor** si se quiere cerrar la reconexión antes del
`RustMotor`.

### 6.4 Cuándo está listo el siguiente paso

**Opción A (la recomendada).** Los criterios de experimentos/13 §2 que no dependen de los Go 4, con
`HP-O16` y un sink nulo como destino:
1. Compila y enlaza con `cargo test --workspace --locked` en Linux (sin cambios de sistema: `cargo` y
   `clang` ya están).
2. 2 × 10 min con 0 xruns propios y el ERR de `pw-top` quieto; `rt_alloc_violations` = 0.
3. `callback_p999_of_min_quantum` < 0,25 (hoy ~0,05 % en el Mac).
4. `same_driver` siempre `true` (`pw-top` y el registro), con **el hilo de datos en `FF` 83**
   (`ps -L`/`chrt -p`, no se supone: regla del repositorio).
5. El nodo nunca queda como sink por defecto (`pw-metadata` antes y después) y desaparece tras `kill -9`.
6. Latencia de punta a punta menor que la de la tubería de hoy, medida igual en las dos (`latencia.py`,
   el valor es la diferencia).
7. El resultado MEDIDO y el veredicto escritos en experimentos/13; la entrada i-7c8794-fd9732 del roadmap
   cambia de estado en el mismo cambio, y `probes/17` se borra después (d-7c8794-3208b7).

**El paso 4 completo** (research/12 §4) agrega, con 3 Go 4 y el `RustMotor`: 2 × 20 min con **0 eventos
«motor tarde», «tubería vacía» o «casi vacía»**, la prueba de cierre de la calibración en ~0, y los
descartes de radio de B dentro de un factor 2 de los de A. Y si los cortes oídos coinciden con descartes
de radio, Rust no los arregla: se justifica por latencia, Pi y Auracast (compuerta del §4).

**Qué no se puede decir hoy:** que la E/S nativa arregle los cortes. Los números de esta revisión son
latencia, GIL y CPU; los cortes de radio (la causa más probable) no se miden desde que se escribió
experimentos/12, y esa medición va primero que cualquier gasto de la opción B.

### 6.5 Resultado de la opción A (2026-10-09, `HP-O16`, sin parlantes)

Corrió el mismo día, contra un sink nulo de prueba y con el servicio sonando al lado sin tocarlo
([experimentos/13](experimentos/13-e-s-nativa-en-rust.md) §4 y Veredicto; MEDIDO salvo donde dice):
- **Criterio 1:** compila y enlaza con `libpipewire` 1.6.9 al primer intento, sin cambiar `pw_io.rs`;
  26 tests, clippy y fmt limpios (VERIFICADO). `pipewire-rs` 0.10.1 sirve: el *shim* en C no hace falta.
- **Criterios 2 a 4:** 2 × 10 min, sin carga y con 12 hilos de carga a nice 19: 0 xruns propios,
  `rt_alloc_violations` = 0, `same_driver` = `true` en 1198 de 1198 intervalos (`node.driver-id` igual en
  `pw-dump`) y `data-loop.0` en `FF` 83. Callback p99,9 = 0,81 % del cuántum sin carga y **0,33 % con
  carga**: la CPU ajena no lo alcanza.
- **Criterio 5:** nunca quedó como sink por defecto, y tras `kill -9` sus nodos desaparecen en ≤ 2 s sin
  mover nada del usuario.
- **Criterio 6, a medias:** la E/S nativa agrega **0 muestras** frente a tocar directo en el sink (280 de
  280 clics, en cuántum 256, 1024 y 2048, con un retardo de control de 480 muestras que sale exacto). La
  tubería de hoy no se midió con ese método en `HP-O16` (estaba en uso): sigue el ~300 ms INFERIDO de §1.
- **Pendiente:** todo lo que pide los Go 4 (el driver con `aurasync_salida` y los bluez, el A/B, la radio,
  la latencia acústica), y borrar `probes/17` después (criterio 7).

No cambia la recomendación de §6.3: confirma con números que el hilo RT resuelve el retraso por CPU
ajena y la latencia de la tubería, y nada sobre los cortes de radio. El orden de obra sigue siendo C.
