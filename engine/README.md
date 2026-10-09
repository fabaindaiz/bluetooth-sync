# engine/ · el motor en Rust

El camino crítico del audio, en Rust, **opcional y detrás de numpy**: el código numpy de
`host/src/aurasync/dsp/` sigue siendo el oráculo y el respaldo. Diseño:
`docs/superpowers/specs/2026-10-05-rust-engine-scaffold-and-sinc-design.md`; la investigación,
`docs/research/12-motor-de-audio-en-rust.md`. Decisiones: d-7c8794-dc712e, d-7c8794-196e0c y
d-7c8794-0d2a1e (`docs/decisions.md`).

## Qué hay

| Crate | Qué es |
|---|---|
| `crates/aurasync-dsp` | Rust puro (`#![forbid(unsafe_code)]`, sin Python). Hoy: `interpolation`, la lectura sinc del retardo (`Reader`), contra la fórmula de `dsp/interpolation.py`; `spatial`, el upmix espacial / frente intacto (`SpatialUpmix`, con su estado), contra `dsp/spatial.py`; `ambience`, el extractor de ambiente (`Extractor`, con su estado), contra `dsp/ambience.py`; y `fir`, los filtros por convolución FFT (`StreamingFir`, solapar y sumar con cola; `PartitionedFir`, particionado uniforme sin latencia), contra `StreamingFIR` y `PartitionedFIR` de `dsp/eq.py`, para que el código Rust que venga los use directo; y `virtual_bass`, el generador de armónicos de los graves virtuales (`VirtualBass`: banda, rectificador, banda de armónicos, calibración y rampa de la ganancia; posee dos `PartitionedFir` y se llama una vez por bloque), contra `VirtualBass` de `dsp/virtual_bass.py`. FFT con `realfft` 3.5.0 sobre `rustfft` 6.4.1, fijadas exactas |
| `crates/aurasync-engine` | La extensión de Python (PyO3 0.29.3, numpy 0.29.0, maturin 1.15.0), módulo `aurasync_engine`: `read(data, position)`, las clases `SpatialUpmix`, `AmbienceExtractor`, `StreamingFIR`, `PartitionedFIR` y `VirtualBass`, y `capabilities()` |

Hoy hay **cinco** piezas portadas: la lectura sinc, el upmix espacial, el extractor de ambiente,
los filtros FIR (`StreamingFIR` y `PartitionedFIR`, debajo del EQ, el crossover, la protección de
graves y la cola difusa) y el generador de armónicos (`VirtualBass`); en el plan son las etapas 5,
6, 7, 10 y 11 más la lectura. Una etapa con estado (el upmix, el extractor, los filtros FIR, el generador de armónicos)
es de su objeto numpy: cuando el motor es Rust, el objeto numpy tiene el de Rust y le pasa su
estado entero al cambiar de motor en el fondo de un corte, en las dos direcciones, exacto; si Rust
falla, la etapa vuelve a empezar en numpy (el upmix con latencia y entrada suave, como un render
nuevo; el extractor como lo deja `reiniciar`; un filtro con la cola o la historia en cero). Los
filtros FIR están debajo del EQ, el crossover, la protección de graves, los graves virtuales y la
cola difusa: todos pasan a Rust sin cambiar quien los usa. Un filtro arma su objeto Rust en su
primer bloque (uno que se construye y nunca corre, como los filtros que `VirtualBass` deja en reposo
mientras está apagado, no cuesta nada) y el primer bloque de cada largo nuevo arma su FFT y
el espectro de los coeficientes, una vez, como numpy. El generador de armónicos (`VirtualBass`) tiene un
objeto Rust propio con sus dos filtros adentro, que arma en el primer bloque que los corre (con los
armónicos apagados no arma ni registra nada); sus `PartitionedFIR` de numpy quedan para el camino numpy
y para recibir el estado de Rust al volver a numpy. El plan es portar todas las de numpy por
orden de costo y después un `RustMotor` (d-7c8794-0d2a1e, i-7c8794-fd9732).

## Qué hace falta (cada equipo)

`cargo`. Se instala `rustup` (en Arch/CachyOS: `sudo pacman -S rustup`; en el Mac, `brew install
rustup`) y `engine/rust-toolchain.toml` fija la toolchain **1.99.0**, que rustup baja sola la
primera vez que se corre `cargo` dentro de `engine/`. Sin cargo, `hatch test` y
`scripts/check.sh` fallan con un mensaje que lo dice, y el servicio corre igual con numpy.
`PC-Ryzen5` lo tiene desde el 2026-10-07
([00-inventario-linux](../docs/research/experimentos/00-inventario-linux.md)).

## Cómo se usa

```bash
cd engine && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check
cd host && hatch test                  # compila la extensión en el entorno de tests antes de pytest
cd host && hatch run engine-build      # la extensión de producción, en el entorno por defecto
cd host && hatch run aurasync …        # con "engine": "rust" en service.json, o AURASYNC_ENGINE=rust
```

- `"engine": "numpy" | "rust"` en `service.json` (`numpy` por defecto); `AURASYNC_ENGINE` lo
  sobrescribe en tests y CLI. La orden `engine_set` y el selector del panel lo cambian en vivo,
  **solo en el fondo de un corte** (cuando la salida está en cero).
- Sin la extensión, `rust` queda en numpy con el motivo en `state.engine`; nunca es un error.
- **Si Rust falla** (un pánico se atrapa en la frontera y sube como `EnginePanic`, un `RuntimeError`), ese bloque es
  silencio, Rust queda desactivado y numpy entra desde el siguiente corte: la música no se para.
  Al **construir o configurar** el objeto Rust (constructor, `set_params`, `set_layout`,
  `set_taps`, `replace_taps`, `state`, `set_state`, `reset`) cualquier `Exception` cuenta igual
  (`backend.built`): un `ValueError` o `TypeError` de la conversión de argumentos, o un
  `AttributeError` de una extensión vieja sin la clase o el método, no tumban la sesión. En el
  bloque (`backend.guarded`) solo `RuntimeError`: un `TypeError` ahí es un error de quien llama y
  se deja ver.
- `hatch test` compila con la feature `test-panic` (puntos para provocar un pánico en los tests);
  `engine-build` de producción no, y `--check-production` lo comprueba.

## Un detalle de hatch (MEDIDO en `HP-O16`, hatch 1.16.2)

Un miembro de workspace de hatch con backend maturin **no funciona** si queda fuera de `host/`
("No members could be derived"), así que la extensión se compila con un script
(`engine-build`) y no como miembro. Responde la duda abierta de research/12 §2.5.

## Constantes compartidas con numpy

Lectura sinc: `HALF = 16`, `BETA = 8.0`, 2048 pasos de tabla, `_FEW = 64`. Upmix espacial: el
Haas máximo (30 ms), la entrada suave (4096), los pisos (1e-8, 1e-30), el realce del frente (6 dB)
y la curva de ambiente (`mu0`, `mu1`, `sigma`, `energia_minima`). Extractor de ambiente: el
piso de la suma de ventanas (1e-8); su curva y su STFT llegan como parámetros. Filtros FIR:
ninguna (los coeficientes y la partición llegan como parámetros); sus claves `"fir"` y
`"virtual_bass"` llevan `{"version": 1}` (en `lib.rs` y en `dsp/backend.py`) y dicen que la
extensión trae los filtros y el generador de armónicos; la versión se sube cada vez que cambia el
comportamiento en Rust de esa etapa, y una compilada antes se rechaza al cargarla (hay que volver a
correr `hatch run engine-build`). `capabilities()` las devuelve y
`dsp/backend.py` rechaza una extensión compilada con otras. El golden exige ≤ 1e-9 de diferencia
absoluta con numpy en señales a plena escala (d-7c8794-36dde5); su costo está medido en
[experimentos/20](../docs/research/experimentos/20-costo-de-la-lectura-sinc-en-rust.md). Una
excepción medida: en antifase a igual nivel el upmix espacial está mal condicionado en el propio
numpy (1 ulp de entrada cambia 0,349 su salida), y el golden la evita (experimentos/20 §2.3).
