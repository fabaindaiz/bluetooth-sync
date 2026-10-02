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
