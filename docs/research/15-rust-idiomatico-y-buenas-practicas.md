# 15 · Rust idiomático en el motor: buenas prácticas y dónde se aparta `engine/`

**Pregunta (usuario, 2026-10-09):** «investiga y documéntate sobre Rust para corregir la
implementación con las mejores prácticas y usos idiomáticos». El alcance que eligió incluye la API y
la estructura: cómo se exponen los objetos a Python, los errores entre lenguajes y la organización de
los crates.

**Cómo se hizo:** una investigación del 2026-10-09 en fuentes primarias, sobre la pila fijada. Esa
pila es Rust 1.99.0, edition 2024, pyo3 =0.29.3 (abi3-py312), numpy (rust-numpy) =0.29.0, rustfft
=6.4.1, realfft =3.5.0 y maturin 1.15.0.
- La guía de PyO3, rust-numpy, rustfft y realfft se leyeron en las fuentes **de la versión exacta**,
  las que Cargo ya había bajado; las URL son las públicas equivalentes.
- El texto de Clippy sale de `cargo clippy --explain` en 1.99.0.
- Además se corrieron clippy (`pedantic`, `nursery`, `missing_docs`) y `cargo doc` sobre el código,
  fuera del repositorio.

Marcas:
- **VERIFICADO**: leído en la fuente primaria.
- **REPORTADO**: de una fuente secundaria.
- **SUPUESTO**: de memoria o inferido sin fuente; se comprueba antes de usarlo.
- **INFERIDO**: el análisis del repositorio.

Ninguna fuente aporta código: son ideas.

## Resumen

- **La forma general es la recomendada y se mantiene**:
  - un crate núcleo en Rust puro (`aurasync-dsp`) y un crate delgado de PyO3 (`aurasync-engine`).
    Es el patrón que describe la guía de PyO3 (`advanced/sharing-types`), y el de tokenizers y Polars
    (VERIFICADO);
  - el núcleo ya hace bien varias cosas: errores como `enum` con `Display` y `Error`, buffers del
    llamador reutilizados, `process_with_scratch`, un camino por bloque que no asigna memoria
    (probado con un asignador contador) y sumas en el orden de numpy.
- **Lo que más vale corregir está en el puente con Python** (INFERIDO, §B):
  1. `set_params` toma 6 o 7 números por posición y el host los pasa con `*tupla`: un cambio de orden
     da audio equivocado sin ningún error;
  2. toda falla que no es un pánico ni un `ValueError` del núcleo sale como `RuntimeError` pelado, y
     el host toma cualquier `RuntimeError` como «falló el motor», también los errores de uso;
  3. dos constructores del núcleo hacen `assert!` (pánico) en vez de devolver `Result`, y el puente
     repite esas validaciones a mano;
  4. la lectura sinc vive en un `static Mutex` global oculto, la única etapa que no es de su objeto
     numpy;
  5. el módulo se declara a la antigua (una función) y no hay `.pyi`.
- **Lo que NO se aplica aunque Clippy lo proponga:** `mul_add` (`suboptimal_flops`, 34 casos) y
  `midpoint` (`manual_midpoint`, 3 casos). Cambian el redondeo, y el oráculo numpy multiplica y suma
  por separado: romperían el golden ≤ 1e-9 (VERIFICADO: `f64::mul_add` redondea una sola vez).
- **Rust nunca fusiona `a*b + c` en un FMA por su cuenta** (RFC 3514, VERIFICADO). Por eso
  `target-cpu=native` no amenaza el golden; queda como experimento medido.
- **Activar flush-to-zero desde Rust es comportamiento indefinido** (documentación de `_mm_setcsr`,
  VERIFICADO). Si los denormales llegan a importar, el arreglo va a la vez en numpy y en Rust, y antes
  se mide.

## A. Lo que dicen las fuentes

### A.1 Rust API Guidelines

Fuente: <https://rust-lang.github.io/api-guidelines/>, VERIFICADO.

- **C-CASE y C-GETTER.** Los tipos van en UpperCamelCase y un acrónimo cuenta como una palabra
  (`StreamingFir`); los getters no llevan `get_`. *Aquí ya se cumple*: `StreamingFir` se expone como
  `StreamingFIR` con `#[pyclass(name=…)]`.
- **C-CONV.** `as_` es gratis, `to_` es caro (de prestado a propio) e `into_` consume. *Aquí:*
  `state()` copia todos los buffers con nombre de getter.
- **C-COMMON-TRAITS y C-DEBUG.** Todo tipo público implementa `Debug`. *Aquí faltan* en `Reader`,
  `StreamingFir`, `PartitionedFir`, `Extractor`, `SpatialUpmix` y `VirtualBass`. Guardan
  `Arc<dyn RealToComplex>`, así que el `Debug` va a mano. Los detecta el lint de rustc
  `missing_debug_implementations`.
- **C-GOOD-ERR.** Los errores implementan `Error`, con mensajes en minúscula y sin punto final. *Aquí:*
  la forma está bien, pero `BadShape(String)` lleva prosa con la que no se puede hacer `match`.
- **C-VALIDATE.** Se prefiere, en orden: los tipos, después `Result`, después `debug_assert!`.
  *Aquí:* `Extractor::new` y `SpatialUpmix::new` hacen `assert!`.
- **C-FAILURE.** Toda función que falla documenta `# Errors` y `# Panics`. *Aquí faltan* 17 y 5.
- **C-CRATE-DOC y C-EXAMPLE.** *Aquí:* la documentación del crate `aurasync-dsp` es una línea y no
  hay doctests.
- **C-NO-OUT.** Se prefiere devolver un valor, salvo para reutilizar un buffer del llamador. *Aquí
  ya se cumple*: `process(x, out)` es esa excepción.
- **C-CUSTOM-TYPE.** Tipos propios en vez de varios `usize` o `bool` sueltos. *Aquí, valor bajo:*
  `SpatialUpmix::new(speakers, sr, n_fft, hop, max_block)`.
- **`#[must_use]`** (Referencia de Rust). Clippy encuentra 22 candidatos (`must_use_candidate`, de
  `pedantic`).

### A.2 Errores y la frontera con Python

Fuentes: guía de PyO3 0.29.3, `function/error-handling` y `exception`, y el código de `pyo3-0.29.3`.
Todo VERIFICADO.

- **`From` en vez de `map_err`.** Un método que devuelve `Result<T, E>` levanta una excepción
  cuando existe `impl From<E> for PyErr`, y entonces `?` funciona. Para un error ajeno, la regla de
  huérfanos pide un tipo local intermedio; la alternativa es `map_err` en cada uso, que la guía
  llama «boilerplate».
- **Excepciones propias.** `create_exception!(módulo, Nombre, Base)` crea excepciones, y un módulo
  declarativo las exporta con `#[pymodule_export]`.
- **Los pánicos.** PyO3 los atrapa con `catch_unwind` y levanta `PanicException`, que deriva de
  **`BaseException`** (`src/panic.rs`). Por eso el `guard` propio del motor está justificado: el lazo
  del servicio no atrapa `BaseException`. El `guard` exige `panic = "unwind"`.
- **Qué atrapa `catch_unwind`.** Según la documentación de std, solo atrapa pánicos que desenrollan,
  no los que abortan.
- **`thiserror`.** Es un crate de derive; con 4 o 5 enums chicos, std alcanza (INFERIDO).

### A.3 Modismos de PyO3 0.29

Fuente: `pyo3-0.29.3/guide/src`, VERIFICADO.

- **Renombres.** Desde 0.26, `with_gil` → `Python::attach` y `allow_threads` → `Python::detach`; desde
  0.27, `downcast` → `cast`. *Aquí:* el código no usa nada deprecado.
- **`Bound` y `Py<T>`.** `Bound<'py, T>` sirve para trabajar y `Py<T>` para guardar. *Aquí* basta con
  `Bound`.
- **Opciones de `#[pyclass]`.** `frozen` quita el contador de préstamos, pero no admite `&mut self`.
  `unsendable` se desaconseja. *Aquí:* las clases mutan, así que `frozen` no aplica.
- **Firmas.** `#[pyo3(signature = (*, a, b))]` admite parámetros solo-keyword, que es el arreglo de
  la debilidad 1.
- **Soltar el GIL.** `Python::detach` conviene para trabajo de «varios milisegundos». El cierre tiene
  que ser `Send`, así que no puede capturar `Bound` ni el préstamo de un arreglo numpy (los préstamos
  siguen activos y un préstamo en conflicto produce un pánico). *Aquí:* experimento aparte.
- **Free-threading.** Desde 0.28 los módulos declaran `gil_used = false` por defecto, pero **una wheel
  abi3 no carga en CPython free-threaded**, así que el GIL existe para este motor. El comentario
  «el lock nunca se disputa» de `lib.rs` depende de eso.
- **Módulo declarativo.** `#[pymodule] mod aurasync_engine { #[pymodule_export] use super::X; }` es
  la forma documentada; según la guía pone `module` en cada clase sola, **pero aquí no pasó: sin `module = …` el `__module__` de las clases queda en `builtins` (MEDIDO al aplicarlo, 2026-10-09), así que se mantiene en cada `#[pyclass]`**; admite miembros con `#[cfg]` y es la única
  que la generación de stubs soporta.
- **Tipos para Python.** Con PyO3 estable, «la mejor solución» es mantener a mano un `.pyi`; maturin
  lo toma de la raíz del proyecto y agrega `py.typed` (<https://www.maturin.rs/project_layout.html>).
  `experimental-inspect` lo genera, pero es experimental.
- **`extension-module`.** Está deprecado: maturin ≥ 1.9.4 pone `PYO3_BUILD_EXTENSION_MODULE`. *Aquí
  ya se cumple*.
- **`cast` en vez de `extract`.** Se prefiere `cast` cuando el error se descarta. *Aquí ya se
  cumple*.

### A.4 rust-numpy 0.29

Fuente: `numpy-0.29.0/src`, VERIFICADO.

- **Leer sin copiar.** `PyReadonlyArray::as_slice()` solo resulta con arreglos contiguos y alineados.
  `as_array()` sirve con cualquier disposición. *Aquí:* `samples()` (presta si es contiguo y copia si
  no) está bien.
- **Crear sin copiar.** `PyArray1::from_vec` entrega el buffer sin copiar; `from_slice` copia.
  `zeros` seguido de `readwrite().as_slice_mut()` llena una salida sin copia.
- **Préstamos.** El chequeo es global y aproximado por exceso, y no se sincroniza entre hilos.

### A.5 Rendimiento por bloque

Fuentes: Rust Performance Book (VERIFICADO), documentación de std y de realfft/rustfft (VERIFICADO),
RFC 3514 (VERIFICADO).

- **Chequeos de límites.** Se evitan con iteradores, `zip` y `chunks_exact`, y tomando las rebanadas
  antes del lazo; `get_unchecked` es el último recurso.
- **`#[inline]`.** Sugiere, no obliga. Que sin `#[inline]` o LTO no se inlinea entre crates es un
  SUPUESTO.
- **El perfil de release.**
  - `lto` y `codegen-units=1` aceleran a cambio de compilar más lento.
  - `panic="abort"` sería apenas más rápido, pero **rompe `catch_unwind`**, del que dependen PyO3 y el
    `guard`.
  - `target-cpu=native` ayuda si no importa la portabilidad. Aquí cada equipo compila lo suyo
    (`engine-build`).
- **rustfft y realfft.**
  - Se recomienda un solo planner reutilizado; rustfft elige AVX, FMA o NEON en tiempo de ejecución.
  - `process_with_scratch` evita asignar memoria.
  - **El buffer de entrada queda con basura** después del FFT.
  - Ninguna dirección normaliza.
  - La inversa exige la parte imaginaria de los bins 0 y N/2 en cero.
- **El redondeo.**
  - `f64::mul_add` redondea una vez.
  - Rust garantiza IEEE estricto y nunca contrae `a*b + c` (RFC 3514).
  - SUPUESTO: en x86_64 sin la feature `fma`, `mul_add` termina en una llamada a libm.
- **Denormales.** Cambiar el MXCSR (FTZ/DAZ) desde Rust es comportamiento indefinido inmediato, y
  `_MM_SET_FLUSH_ZERO_MODE` está deprecado desde 1.75.

### A.6 Tiempo real

Fuente: Ross Bencina, «Real-time audio programming 101: time waits for nothing», 2011, VERIFICADO.

- **Lo que no va en el camino de audio:** asignar o liberar memoria, tomar un mutex, hacer E/S (ni
  siquiera imprimir), llamar al sistema si puede bloquear, o usar algoritmos con mal peor caso.
- **Lo que va en su lugar:** preasignar, precalcular fuera del camino de audio, usar O(1) en el peor
  caso y pasar mensajes por colas sin lock.

*Aquí* (INFERIDO):
- No es un callback de tiempo real duro: un hilo de Python llama a Rust una vez por bloque de 85 ms.
- El núcleo cumple: no asigna después del primer bloque.
- Lo que falta:
  - el `Mutex` global de la lectura sinc está en el camino del bloque;
  - el puente asigna un arreglo numpy de salida por bloque.

Los crates de tiempo real (`rtrb`, `basedrop`, `triple_buffer`) solo importan si Rust llega a tener
su propio hilo de audio (la E/S nativa de research/12 §4).

### A.7 Lints y herramientas

Fuentes: Cargo (tablas `[lints]`), la documentación de Clippy, la Referencia y el libro de rustc.
Todo VERIFICADO.

- **Las tablas.** `[workspace.lints]` se define una vez y cada miembro lo toma con
  `[lints] workspace = true`. `priority` ordena un grupo (`-1`) frente a un lint suelto.
- **Los grupos de Clippy.** `pedantic` tiene «falsos positivos ocasionales»; `nursery` está en
  desarrollo; `restriction` no se activa entero.
- **`expect` y `reason`.** `#[expect(lint, reason = "…")]` avisa cuando el lint deja de dispararse;
  `allow_attributes_without_reason` exige el motivo.
- **Lo que mide `pedantic` en `aurasync-dsp`:**
  - 28 `cast_precision_loss`, 22 `must_use_candidate`, 17 `missing_errors_doc`;
  - 5 `missing_panics_doc`, 4 `float_cmp` y 3 `manual_midpoint`;
  - casts de truncado y signo.
- **Lo que mide `nursery`:** 34 `suboptimal_flops` y 14 `missing_const_for_fn`, más un clon
  redundante (`spatial.rs`).
- **`cargo doc`:** 1 aviso, un enlace a un ítem privado (`guard`).

### A.8 Tests

Fuentes: Rust Book cap. 11.3, el libro de rustdoc y la FAQ de PyO3. Todo VERIFICADO.

- **Dónde va cada test.** Los unitarios van en `#[cfg(test)] mod tests` del mismo archivo y pueden
  probar lo privado. Los de integración van en `tests/`; lo compartido, en `tests/common/mod.rs`.
- **Doctests.** Prueban la API pública y admiten `?`.
- **Comparar floats.** Con margen (`clippy --explain float_cmp`).

### A.9 Organización

Fuentes: la guía de PyO3 (`sharing-types`), tokenizers y Polars, la Referencia (visibilidad) y el
Rust Book cap. 14.2. Todo VERIFICADO.

- **El patrón.** Núcleo puro y envoltorios `#[pyclass]` delgados.
- **Visibilidad.** `pub(crate)` para lo compartido internamente, y `pub use` para una API estable.

## B. Dónde se aparta el código

Ordenado por valor: primero el riesgo de salida equivocada sin aviso o de una falla mal leída,
después lo que cada port futuro repetiría, y al final la documentación y los lints. Las líneas son del
árbol al 2026-10-09.

1. **Parámetros por posición** (`aurasync-engine/src/lib.rs` `SpatialUpmix.set_params`, 7 valores, y
   `AmbienceExtractor.set_params`, 6). El host los pasa con `*tupla` (`dsp/spatial.py`,
   `dsp/ambience.py`). → Firma solo-keyword.
2. **Excepciones.** Todo lo que no es pánico ni error del núcleo sale como `RuntimeError`, y
   `backend.py` lee cualquier `RuntimeError` del bloque como «falló el motor». → Un `enum` de error del
   puente con `From` para cada error del núcleo y de numpy, un solo `From<…> for PyErr`, y excepciones
   propias que heredan de `RuntimeError`, para que el host siga funcionando igual y pueda afinar después.
3. **Validación doble y constructores que hacen pánico** (`ambience.rs` `Extractor::new`,
   `spatial.rs` `SpatialUpmix::new`). → `Result` con una variante de configuración inválida, y `?` en el
   puente.
4. **`static READER: Mutex<Option<Reader>>`** con recuperación de envenenamiento. → Una clase `Reader`
   que pertenece a su etapa, como todas las demás.
5. **Módulo a la antigua y sin tipos.** → Módulo declarativo y un `aurasync_engine.pyi` mantenido a
   mano.
6. **Interiores duplicados.**
   - `root_hann`, `scaled`, `times_conj` y la curva de ambiente están en `ambience.rs` y en
     `spatial.rs`, igual que la maquinaria del STFT por flujo.
   - `Complex` se reexporta tres veces.
   - Hay un planner por objeto en ambiente y espacial, frente a uno por hilo en `fir.rs`.
   - Hay literales sueltos (`1e-20`, `1e-40`, `1e-30`).
   - → Módulos `pub(crate)` `stft` y `complex`, un solo planner y constantes con nombre.
7. **Ayudantes de test copiados cinco veces** (el asignador contador, `allocations_in` y `Rng`, ~90
   líneas por archivo), y ningún test unitario de lo numérico privado. → `tests/common/mod.rs` y
   `#[cfg(test)]`.
8. **Sin `Debug`** en los tipos de proceso.
9. **Faltan `# Errors`, `# Panics` y la documentación del crate.**
10. **Sin tabla de lints**, y un `#[allow]` sin motivo.
11. **`Vec<Vec<f64>>` por parlante** en el estado espacial y en el FDL particionado, con copias fila a
    fila en el puente. Valor de claridad medio; de rendimiento, bajo.
12. **Lazos con índices en las partes calientes** (`ambience.rs`, `spatial.rs` y la rampa de
    `virtual_bass.rs`). Se mide antes de tocarlos: es probable que dominen `tanh`, `hypot` y los FFT.
13. **Un arreglo numpy nuevo por salida y por bloque.** Un `out=` opcional lo quitaría.
14. **El GIL tomado durante todo el bloque.** `Python::detach` pide copiar la entrada y un
    experimento.
15. **El perfil de release está incompleto**: solo `codegen-units=1`. → `lto`, y `panic="unwind"`
    explícito con su motivo.
16. **Denormales** en los suavizados recursivos tras ~71 s de ceros exactos (aritmética INFERIDA).
    Se mide antes.
17. **Nombres y fugas menores.**
    - `state()` copia todo con nombre de getter.
    - `VirtualBass` devuelve `FirError`.
    - `weights_from_formula(&self)` no usa `self`.
    - El puente guarda una copia de `block` porque el núcleo no la da.
18. **`BadShape(String)`.** → Que lleve `{campo, recibido, esperado}`.
19. **Falta `#[must_use]`** en 22 candidatos.
20. **Menores.**
    - La plomería del pánico de prueba está repetida en cuatro clases.
    - Hay un clon redundante.
    - La descripción de `crates/aurasync-engine/pyproject.toml` quedó vieja.

**Lo que ya está alineado y se mantiene:**
- la división en dos crates y `forbid(unsafe_code)`;
- los nombres;
- los parámetros de salida para reutilizar buffers;
- los errores con `Error` y `Display`;
- el uso de scratch de realfft;
- el camino sin asignaciones probado con tests;
- `cast` en vez de `extract`;
- el `guard` de pánicos;
- las sumas de ocho carriles en el orden de numpy;
- nada de API deprecada.

## C. Crates de terceros que ayudarían (cada uno pide aprobación del usuario)

Versiones y licencias del API de crates.io al 2026-10-09 (VERIFICADO). **Ninguno entra en este
trabajo**: todo lo de §D se hace con std.

| Crate | Licencia | Para qué | Veredicto |
|---|---|---|---|
| `thiserror` 2.0.21 | MIT/Apache-2.0 | derive de `Error` | no hace falta |
| `proptest` 1.11.0 | MIT/Apache-2.0 | tests por propiedades (cortes de bloque, ida y vuelta del estado) | útil |
| `criterion` 0.8.2 | Apache-2.0/MIT | microbenchmarks para §B.12–16 | útil para experimentos |
| `assert_no_alloc` 1.1.2 | BSD-1-Clause | reemplaza al asignador contador | sin release desde 2021; el propio basta |
| `rtrb`, `basedrop`, `triple_buffer` (MPL-2.0) | — | colas y diferidos de tiempo real | solo con un hilo de audio en Rust |
| `ndarray` 0.17.2 | MIT/Apache-2.0 | buffers 2-D (§B.11) | ya es transitivo; hacerlo directo pide aprobación |
| `no_denormals` 0.3.0 | MIT | FTZ por alcance | **no**: cambiar el MXCSR desde Rust es UB |
| `experimental-inspect` (feature de pyo3) | — | stubs con `maturin generate-stubs` | experimental: decisión del usuario |

## D. Qué se aplica (2026-10-09) y qué queda como experimento

**Estado (2026-10-09, sesión s-7c8794-816f05):**
- **Aplicado:** §B.1–10, 15 y 17–20, en las tareas 1–5 del plan, cada una revisada y sin cambiar ningún número.
- **La tanda final de arreglos se re-revisó en el cierre:** los 13 puntos quedaron resueltos.

El plan es `docs/superpowers/plans/2026-10-09-rust-idioms-and-decorrelation.md`.

- **Se aplica, sin cambiar ningún número:** el golden de numpy sigue en la misma tolerancia y los
  tests de Rust siguen verdes. Son §B.1–10, 15 y 17–20.
- **Queda en el roadmap como experimento medido** (i-7c8794-b4b8b1):
  - §B.11, los buffers planos;
  - §B.12, los lazos sin índices;
  - §B.13, la salida `out=`;
  - §B.14, `Python::detach`;
  - §B.16, los denormales;
  - `target-cpu=native`.

  Cada uno cambia el rendimiento y no la forma, y se mide antes de tocarlo.
