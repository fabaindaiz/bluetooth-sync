# 20 · Costo por bloque de la lectura sinc: numpy contra Rust

**Pregunta:** la lectura sinc del retardo (`interpolation.read`) es la etapa más cara de la cadena
(experimentos/12 §1.1). Portada a Rust (i-7c8794-fd9732, pasos 1 y 2), ¿cuánto cuesta por bloque
frente a numpy, con 1, 3 y 8 parlantes, con el retardo quieto y en movimiento?

**Veredicto: MEDIDO. Rust lee unas 8,3 a 10,5 veces más rápido que numpy** en las seis
condiciones, en dos corridas independientes que coinciden. Con 8 parlantes y el retardo en
movimiento (el peor caso) baja de ~31 ms a ~3,7 ms por bloque de 85,3 ms. **No dice nada sobre
los microcortes**: no son del motor (experimentos/12); esto es solo el costo de una etapa.

**Segunda etapa, el upmix espacial / frente intacto (§2, MEDIDO): Rust es 3,2 a 3,8 veces más
rápido** que numpy (2,6–2,7 en frente intacto con 3 parlantes), en dos corridas que coinciden:
con 3 parlantes en espacial, de ~2,9 a ~0,85 ms por bloque; con 8, de ~4,9 a ~1,3 ms. Menos que en
la lectura sinc porque el grueso son FFT, que numpy ya hace en C (pocketfft). **Hallazgo
(MEDIDO, §2.3):** en un punto el upmix espacial está mal condicionado en sí mismo (antifase a
igual nivel): numpy cambia 0,349 su salida por un cambio de 1 ulp en la entrada, así que ningún
motor puede igualarlo a 1e-9 ahí.

**Tercera etapa, el extractor de ambiente (§3, MEDIDO): Rust es 1,4 a 2,0 veces más rápido**
que numpy, en dos corridas limpias: de ~0,9–1,0 a ~0,5 ms por bloque, igual con 1, 3 u 8
parlantes porque el extractor corre una vez por entrada. Golden ≤ 5,4e-14; sin un punto mal
condicionado como el del upmix.

**Cuarta etapa, los filtros FIR por convolución FFT (§5, MEDIDO, 2026-10-08): Rust es 2,6 a 3,0
veces más rápido** que numpy por filtro, en dos corridas que coinciden: el EQ de 3 parlantes baja de
~0,3 a ~0,1 ms por bloque, las dos ramas del crossover de ~0,2 a ~0,07, el filtro de banda de los
graves virtuales de ~0,13 a ~0,05. Debajo de esos filtros están el EQ, el crossover, la protección
de graves, los graves virtuales y la cola difusa, así que **la cadena entera con todo encendido
pasa de 11–14× tiempo real con numpy a 25–35× con Rust** (antes de esta etapa, 21–27×). Golden ≤
3,1e-13.

**Sexta etapa, el generador de armónicos de los graves virtuales (§7, MEDIDO, 2026-10-08): Rust es
2,4 a 2,7 veces más rápido** que numpy por bloque con armónicos encendidos (3 parlantes de ~0,80 a
~0,31 ms), **pero 1,0 veces frente a los dos FIR ya en Rust llamados desde Python**: portar el
objeto entero no ahorra tiempo medible (lo que quedaba fuera de la convolución son ~0,01 ms por
parlante). Golden ≤ 2,4e-15. La cadena con `protect` + armónicos queda en 32× tiempo real con Rust
contra 14× con numpy.

**Quinta etapa, el crossover y la protección de graves (§6, MEDIDO, 2026-10-08): no había nada más
que portar** sobre los FIR en Rust: lo que la etapa hace por bloque fuera de la convolución son
0,02–0,05 ms y el diseño del filtro sigue en numpy. Golden ≤ 1e-9 contra numpy en `engine=rust` en
los dos órdenes LR y cortes de 40 a 200 Hz; 2,4–2,6× numpy por etapa. De paso se quitó de
`VirtualBass` el rearmado de dos `PartitionedFIR` por bloque con los armónicos apagados (0,64 de
0,94 ms por bloque con 3 parlantes), en numpy y sin tocar Rust.

## Entorno (MEDIDO)

- Equipo `HP-O16` (i5-11400H, 12 hilos), CachyOS, kernel 7.2.8-1-cachyos; python 3.12.12, numpy
  2.5.3; rustc/cargo 1.99.0, PyO3 0.29.3, numpy (crate) 0.29.0, maturin 1.15.0.
- Extensión compilada en `--release` por el entorno `hatch-test` (con la feature `test-panic`;
  INFERIDO que no cambia el costo de la lectura: sus funciones solo existen bajo `#[cfg(feature =
  "test-panic")]`, aparte de `read`; no se midió una compilación sin ella). Sin parlantes ni Bluetooth: es CPU pura.
- **Energía (MEDIDO, leído después de las corridas, a las 16:24, no durante ellas):** red conectada
  (`ADP1/online` = 1; el coordinador la leyó en 1 a las 12:22 y el adaptador no se desconectó),
  gobernador `powersave` (intel_pstate), perfil de energía `performance` (`powerprofilesctl get`),
  turbo permitido (`intel_pstate/no_turbo` = 0). Que no cambiaron durante las corridas es
  INFERIDO.
- Sin cambios de sistema. Se corrió con el portátil en uso normal (otras sesiones lo cargan a
  ratos), por eso se anota `uptime` antes y después de cada corrida.

## Método

`probes/20-costo-sinc-rust/costo.py`. Un **bloque** = una lectura de 4096 muestras (85,3 ms a
48 kHz) por parlante, cada parlante con su propio retardo. **Quieto**: retardo fijo (una sola
fracción por lectura, 2–3 distintas entre parlantes; el camino de la fórmula). **En movimiento**: rampa de
recalibración de 1,7 muestras dentro del bloque (≫ 64 fracciones, el camino de la tabla). Cada
condición: 50 bloques de calentamiento y **500 bloques medidos**, mediana y percentil 95, con
`time.perf_counter`. Cada bloque desplaza la posición, para no leer siempre la misma memoria.
Ambos motores se llaman directo (`interpolation.read_numpy` y `aurasync_engine.read`), sin pasar
por `backend.py`. El golden (≤ 1e-9) lo comprueba `host/tests/test_engine_rust.py`, no esta sonda.

## Resultado (ms por bloque; `x` = mediana numpy / mediana Rust)

Corrida 1 (`uptime` antes: carga 1,45; después: 1,97):

| parlantes | retardo | numpy med | numpy p95 | Rust med | Rust p95 | x |
|---|---|---|---|---|---|---|
| 1 | quieto | 1,045 | 1,228 | 0,105 | 0,111 | 10,0 |
| 1 | movimiento | 3,906 | 4,604 | 0,436 | 0,477 | 9,0 |
| 3 | quieto | 3,322 | 4,244 | 0,326 | 0,373 | 10,2 |
| 3 | movimiento | 12,048 | 14,710 | 1,343 | 1,538 | 9,0 |
| 8 | quieto | 8,812 | 11,145 | 0,939 | 1,225 | 9,4 |
| 8 | movimiento | 31,104 | 39,500 | 3,610 | 4,042 | 8,6 |

Corrida 2, independiente, 20 s después (carga 1,54 antes; 2,48 después):

| parlantes | retardo | numpy med | numpy p95 | Rust med | Rust p95 | x |
|---|---|---|---|---|---|---|
| 1 | quieto | 1,099 | 1,404 | 0,105 | 0,114 | 10,5 |
| 1 | movimiento | 4,227 | 5,856 | 0,469 | 0,613 | 9,0 |
| 3 | quieto | 3,248 | 3,702 | 0,318 | 0,336 | 10,2 |
| 3 | movimiento | 11,473 | 14,410 | 1,371 | 1,540 | 8,4 |
| 8 | quieto | 8,705 | 11,370 | 0,853 | 0,924 | 10,2 |
| 8 | movimiento | 31,964 | 40,823 | 3,844 | 5,142 | 8,3 |

La carga de 1 minuto quedó siempre por debajo de 4 (el umbral para dar los números por sucios),
así que no hubo que repetir. Datos crudos: la salida impresa de la sonda, en
[datos/20/corrida-1.txt](datos/20/corrida-1.txt) y [datos/20/corrida-2.txt](datos/20/corrida-2.txt).

## Qué dice

- **Las dos corridas coinciden** dentro de ~10 % en las medianas (la mayor diferencia, 8 parlantes
  quieto en Rust, 0,939 contra 0,853 ms; la del p95 de numpy es mayor, por la carga ajena, INFERIDO).
  La relación se sostiene en las seis condiciones, y no depende del número de parlantes: el costo
  es lineal en ellos en ambos motores.
- **El costo sigue siendo un tercio del bloque en numpy en el peor caso** (31 de 85 ms con 8
  parlantes en movimiento) y baja a un 4 % con Rust. Con 3 parlantes quietos, el caso de uso
  actual, pasa de 3,3 a 0,3 ms.
- **El movimiento cuesta ~4× el quieto en ambos**: el camino de la tabla (4 puntos de Lagrange por
  muestra) pesa más que el de la fórmula, que calcula pocos pesos y los reutiliza (la causa es INFERIDA; no se perfiló).
- **Lo que NO se midió**: la lectura dentro del servicio (con el hilo del motor, la copia de
  `backend.py` y el GIL), el costo del resto de la cadena, y nada con parlantes. INFERIDO: el
  costo de `backend.read` sobre `read` es de microsegundos; falta medirlo cuando se porte la
  segunda etapa. Una sola máquina: el Ryzen 5 de `PC-Ryzen5` puede dar otras razones.

## Cómo reproducirlo

```bash
cd host && hatch test -k engine_rust   # deja compilada la extensión en el entorno hatch-test
$(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo.py
```
Compara `uptime` antes y después; si la carga pasa de 4, no se tomen los números por limpios.

## 2 · El upmix espacial / frente intacto (`dsp/spatial.py`), 2026-10-05

La segunda etapa portada (plan Task 5, orden de costo de spec §5): `SpatialUpmix` en
`engine/crates/aurasync-dsp/src/spatial.rs`, con su estado (STFT, solapes, suavizados, línea Haas)
en Rust, dueño el objeto numpy cuando el motor es Rust. FFT con `realfft` 3.5.0 sobre `rustfft`
6.4.1 (fijadas exactas). El golden (`host/tests/test_spatial_rust.py`, 84 tests) da **≤ 2,2e-15**
de diferencia absoluta con numpy en todos sus casos (señales a plena escala, 11 disposiciones,
bloques de tamaños impares, barridos de parámetros, cambios en vivo, el cambio de motor a mitad de
la corrida y el motor completo); el umbral es 1e-9. El cambio de motor mueve el estado entero y es
exacto (≤ 4,4e-16 contra una corrida que nunca cambió).

### 2.1 Entorno y método (MEDIDO)

El mismo equipo, versiones y extensión que §Entorno (rustc 1.99.0, compilada en `--release` con
`test-panic` por el entorno `hatch-test`). **Energía, leída por la sonda antes y después de cada
corrida (MEDIDO, 16:46):** red conectada (`ADP1/online` = 1), gobernador `powersave`, perfil
`performance`, `no_turbo` = 0, igual antes y después. Sonda:
`probes/20-costo-sinc-rust/costo_espacial.py`. Un **bloque** = `SpatialUpmix.process` de 4096
muestras estéreo (85,3 ms), que da directo y ambiente por parlante; parlantes en un anillo
repartido (como `tests/test_spatial.py::test_cost_per_block_is_flat`), todos principales; render
espacial (1, 3 y 8) y frente intacto (3 y 8; con 1 no hay par de adelante). Señal: centro más
sala, 0,1 de escala. 50 bloques de calentamiento y **500 medidos**, mediana y p95. "numpy" y
"Rust" pasan por la etapa como la llama el motor (`backend.use`; Rust incluye la conversión a
float64 y armar el diccionario por parlante); "solo Rust" llama al objeto de la extensión directo.

### 2.2 Resultado (ms por bloque; `x` = mediana numpy / mediana Rust)

Corrida 1 (carga 1,49 antes; 1,50 después):

| parlantes | render | numpy med | numpy p95 | Rust med | Rust p95 | x | solo Rust med |
|---|---|---|---|---|---|---|---|
| 1 | espacial | 1,524 | 1,833 | 0,451 | 0,485 | 3,4 | 0,449 |
| 3 | espacial | 2,984 | 3,443 | 0,848 | 0,893 | 3,5 | 0,889 |
| 3 | frente | 1,278 | 1,524 | 0,499 | 2,059 | 2,6 | 0,502 |
| 8 | espacial | 5,071 | 5,874 | 1,317 | 1,613 | 3,8 | 1,299 |
| 8 | frente | 2,504 | 3,965 | 0,749 | 1,018 | 3,3 | 0,746 |

Corrida 2, independiente, 6 s después (carga 1,62 antes; 1,78 después):

| parlantes | render | numpy med | numpy p95 | Rust med | Rust p95 | x | solo Rust med |
|---|---|---|---|---|---|---|---|
| 1 | espacial | 1,453 | 1,709 | 0,448 | 0,648 | 3,2 | 0,439 |
| 3 | espacial | 2,894 | 3,237 | 0,850 | 0,953 | 3,4 | 0,864 |
| 3 | frente | 1,268 | 1,454 | 0,476 | 0,520 | 2,7 | 0,480 |
| 8 | espacial | 4,767 | 5,402 | 1,279 | 1,507 | 3,7 | 1,310 |
| 8 | frente | 2,306 | 2,695 | 0,703 | 0,738 | 3,3 | 0,698 |

Carga siempre bajo 4. Datos crudos: [datos/20/espacial-corrida-1.txt](datos/20/espacial-corrida-1.txt)
y [datos/20/espacial-corrida-2.txt](datos/20/espacial-corrida-2.txt).

**Qué dice:**
- Las corridas coinciden dentro de ~7 % en las medianas; el p95 de Rust con 3 en frente de la
  corrida 1 (2,06 ms) es un salto aislado que la corrida 2 no repite (0,52 ms; INFERIDO: carga ajena).
- **3,2–3,8×** en espacial y 2,6–3,3× en frente: menos que la lectura sinc (8–10×) porque el
  grueso del costo son FFT de 2048 (cada 512 muestras, 2 directas y hasta 2 inversas por parlante),
  que numpy ya hace en C; lo que Rust ahorra es el trabajo por bin y los arreglos temporales
  (INFERIDO; no se perfiló).
- El paso por `backend` y el armado del diccionario no se notan: "Rust" y "solo Rust" difieren en
  menos de ~0,04 ms, dentro del ruido.
- Con 3 parlantes en espacial (el caso de uso), la etapa baja de ~2,9 a ~0,85 ms por bloque de 85 ms.

### 2.3 Hallazgo: un punto mal condicionado del upmix espacial (MEDIDO)

La entrada de `tests/test_spatial.py::_panned` con `phi = 3π/4` es antifase a igual nivel salvo
por redondeo (`cos(3π/4) = -0,7071067811865475`, `sin(3π/4) = 0,7071067811865476`). Ahí `L + R` se
anula y `_frame` toma la fase "del canal más fuerte", `np.where(el >= er, fl, fr)`; `el` y `er`
difieren en ~1 ulp en cada bin, y como `fl ≈ -fr`, la elección invierte el signo del directo de
ese bin. Medido en `HP-O16` el 2026-10-05 (numpy 2.5.3; 1,5 s de ruido a 0,1; anillo de 3; salida
de RMS 0,043), diferencia absoluta máxima de la salida:

| comparación | máx \|diff\| |
|---|---|
| Rust contra numpy, esta entrada | 0,203 |
| **numpy contra numpy**, la entrada izquierda × (1 + 2⁻⁵²) | **0,349** |
| Rust contra numpy, `L = -R` exacto (empate exacto: los dos eligen igual) | 2,2e-16 |
| Rust contra numpy, \|L\| = \|R\|·(1 + 1e-12), (1 + 1e-9), (1 + 1e-6) | ≤ 1,7e-16 |

**Qué significa:** es un punto mal condicionado del propio upmix, no un error del puerto: numpy
mismo cambia 0,349 por un cambio de 1 ulp, así que ningún motor (ni otro numpy con otra FFT) puede
igualarlo a 1e-9 ahí; el redondeo distinto de pocketfft y realfft basta para inclinarlo. El golden
usa `phi = 3π/4 + 0,01` y comprueba aparte el empate exacto (`L = -R`: los dos motores eligen la
misma fuente de fase) y un nivel a 1e-9 del empate. Suavizar esa discontinuidad (un margen en el
desempate o una fuente de fase continua) cambiaría el sonido de numpy en los bins casi empatados:
queda pendiente aparte, sin tocar `spatial.py` (i-7c8794-fd9732), junto con la otra superficie
donde cambia la fuente de fase, el umbral `et > 0.01 * energy` (INFERIDO del código). El 0,349 lo
reproduce y fija `tests/test_spatial_rust.py::test_numpy_itself_changes_0_349_for_one_ulp_at_the_anti_phase_tie`
(numpy contra numpy, con los dos arcos del golden). Si se oye, sería en material en
antifase a igual nivel, raro en música (INFERIDO; no se escuchó).

### 2.4 Cómo reproducirlo

```bash
cd host && hatch test tests/test_spatial_rust.py
$(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_espacial.py
```

## 3 · El extractor de ambiente (`dsp/ambience.py`), 2026-10-05

La tercera etapa portada (plan Task 6): `Extractor` en
`engine/crates/aurasync-dsp/src/ambience.rs`, con su estado (la STFT en flujo, el solape, la suma
de ventanas, los suavizados de las correlaciones y lo calculado sin entregar) en Rust, dueño el
objeto numpy cuando el motor es Rust. Las mismas FFT fijadas que §2. El golden
(`host/tests/test_ambience_rust.py`, 50 tests) da **≤ 5,4e-14** de diferencia absoluta con numpy
en sus 50 comparaciones (los casos de `tests/test_ambience.py`, señales a plena escala, bloques
de tamaños impares y al azar, 10 barridos de parámetros, cinco tamaños de STFT —impares
incluidos—, señales extremas, cambios de parámetros en vivo, `reiniciar` a mitad de la corrida y
entradas con paso o float32); el cambio de motor a mitad de la corrida y el motor completo (render
clásico con un cambio de cadena) también quedan bajo 1e-9. Dos fallas plantadas en Rust, vistas
fallar y retiradas: `<` por `<=` en el criterio de energía (1 test rojo, 2,02: el canal callado
con `energia_minima = 0`, donde numpy compara `0 < 0`) y el codo de la curva corrido un 0,1 %
(40 de 50 rojos, de 1,6e-6 a 2,5e-3).

**El criterio de energía es el único borde duro del extractor** (`flojo / fuerte <
energia_minima` pone el índice en 0). INFERIDO del código y comprobado en el golden: sobre el
borde exacto solo cae material coherente (R = L/2 da la razón 0,25 exacta en los dos motores,
porque dividir por 2 conmuta con todo redondeo), y ese material tiene índice 0 a ambos lados,
así que no hay un punto mal condicionado como el de §2.3; el empate que sí cambia el sonido es
`0 < 0`, y los dos motores lo deciden igual.

### 3.1 Entorno y método (MEDIDO)

El mismo equipo, versiones y extensión que §Entorno. Sonda:
`probes/20-costo-sinc-rust/costo_ambiente.py`. **El extractor corre una vez por entrada, no por
parlante**: su costo no depende de cuántos parlantes haya. Para mostrarlo se mide dentro del motor
(`Motor.procesar`, render clásico, cadena por defecto) con 1, 3 y 8 parlantes, cronometrando solo
la llamada del motor al extractor: 4096 muestras estéreo (85,3 ms) que dan el ambiente en mono.
"numpy" y "Rust" son esa llamada con cada motor (Rust incluye `backend` y la conversión a
float64); "solo Rust" llama al objeto de la extensión directo. Señal: centro más sala, 0,1 de
escala. 50 bloques de calentamiento y **500 medidos**, mediana y p95. Energía leída por la sonda
antes y después de cada corrida: red conectada, gobernador `powersave`, perfil `performance`,
`no_turbo` = 0, igual en todas.

### 3.2 Resultado (ms por bloque; `x` = mediana numpy / mediana Rust)

Corrida 1 (carga de 1 minuto 2,82 antes; 3,45 después):

| parlantes | numpy med | numpy p95 | Rust med | Rust p95 | x |
|---|---|---|---|---|---|
| 1 | 0,984 | 1,395 | 0,499 | 0,741 | 2,0 |
| 3 | 1,028 | 1,506 | 0,645 | 0,905 | 1,6 |
| 8 | 0,972 | 1,556 | 0,542 | 0,840 | 1,8 |

Solo Rust: mediana 0,490.

Corrida 2, independiente, 3 minutos después (carga 2,99 antes; 2,77 después):

| parlantes | numpy med | numpy p95 | Rust med | Rust p95 | x |
|---|---|---|---|---|---|
| 1 | 0,898 | 1,058 | 0,636 | 0,818 | 1,4 |
| 3 | 0,901 | 1,094 | 0,512 | 0,684 | 1,8 |
| 8 | 0,903 | 1,344 | 0,475 | 0,545 | 1,9 |

Solo Rust: mediana 0,456.

Una corrida entre las dos se **descartó**: empezó con carga 5,42 (otra sesión corría tests de
navegador), sobre el umbral de 4; sus números (Rust 1,7–1,9×) no contradicen los de arriba. La
carga de 5 minutos quedó en ~4,3–4,9 durante todas: la máquina estuvo cargada a ratos, y la fila
de 1 parlante de la corrida 2 en Rust (0,636 contra 0,48–0,50 en las otras) es probablemente eso
(INFERIDO). Datos crudos: [datos/20/ambiente-corrida-1.txt](datos/20/ambiente-corrida-1.txt),
[datos/20/ambiente-corrida-2.txt](datos/20/ambiente-corrida-2.txt) y la descartada,
[datos/20/ambiente-descartada-carga-5.txt](datos/20/ambiente-descartada-carga-5.txt).

**Qué dice:**
- **Rust es 1,4–2,0 veces más rápido** que numpy (1,6–2,0 sin la fila ruidosa): de ~0,9–1,0 a
  ~0,5 ms por bloque de 85 ms. El
  número de parlantes no lo mueve (las tres filas son la misma etapa), como se esperaba.
- Menos que el upmix (3,2–3,8×) porque aquí numpy ya hace casi todo en vectores de C (dos FFT
  directas y una inversa por trama, y unas veinte operaciones sobre 1025 bins); lo que queda en
  Rust lo pesan las funciones por bin (`hypot` ×3, `tanh`, `sqrt`), que se copian de numpy para
  redondear igual: una medición suelta (no guardada) les dio ~0,19 de los ~0,5 ms (INFERIDO; no
  se perfiló).
- "Rust" y "solo Rust" coinciden dentro del ruido: el paso por `backend` no se nota.
- En el total de la cadena el extractor pesa poco (~1 ms de 85 ms en numpy): portarlo importa
  para el motor completo en Rust (i-7c8794-fd9732), no por su costo.

### 3.3 Cómo reproducirlo

```bash
cd host && hatch test tests/test_ambience_rust.py
$(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_ambiente.py
```

## 4 · Estado del motor Rust para probarlo en `HP-O16` (2026-10-08)

Pedido del usuario: verificar si el motor Rust está listo para probarse en este equipo y con todas las
funciones del motor numpy.

**Cobertura (VERIFICADO en el código y el plan):** el plan porta 13 etapas más el `RustMotor`, en orden de
costo (`docs/superpowers/plans/2026-10-05-rust-engine-scaffold-and-sinc.md`, Tasks 5–15). **Están hechas
3 (superado 2026-10-08: ver §5 a §7, ahora también los FIR bajo el EQ y el crossover, el crossover y la
protección de graves, y los graves virtuales):**
- la lectura sinc del retardo (`interpolation`);
- el upmix espacial / frente intacto (`spatial`);
- el extractor de ambiente (`ambience`).

**Faltan** (superado 2026-10-08: el EQ, el crossover, los graves virtuales y la cola difusa ya
corren en Rust, §5 a §7; siguen sin portar la decorrelación, el limitador, las rampas y el corte, los
medidores y el `RustMotor`):
- el EQ (convolución FFT);
- la decorrelación;
- el limitador / true peak;
- el crossover;
- los graves virtuales;
- la cola difusa;
- las rampas y el corte;
- los medidores de sonoridad;
- el `RustMotor`.

Con `engine = rust`, el motor sigue siendo el `Motor` de Python: las 3 etapas portadas llaman a Rust y el
resto corre en numpy. **No hay paridad completa, pero no falta ninguna función:** lo no portado sigue
funcionando en numpy dentro del mismo motor.

**Listo para probar (MEDIDO en `HP-O16`):**
- **Toolchain:** rustc 1.99.0.
- **Rust:** `cargo test` da 23 tests ok, y `clippy -D warnings` y `fmt --check` pasan.
- **Extensión de producción:** `hatch run engine-build` compila, carga y resuelve `rust` como disponible.
  Sus constantes coinciden con numpy, y `--check-production` confirma que no trae los puntos de pánico de
  los tests.
- **Selector de motor del panel:** 4 de 4 tests de navegador.
- **La suite entera con `AURASYNC_ENGINE=rust`: 1510 de 1514.** Los 4 fallos son de los tests, no del motor:
  - 3 de `test_engine_backend` comprueban que el valor **por defecto** sea numpy, y la variable de entorno
    lo cambia;
  - `test_motor::test_la_ganancia_se_aplica_en_decibeles` divide dos salidas que en las primeras 16
    muestras valen 0 en los dos motores. numpy da ceros con signo y la división no explota; Rust da 0,0
    exacto y queda 0/0. Hay que comparar `b` con `0,5·a`.

**El costo de la cadena completa** (`probes/18-costo-de-la-cadena/costo.py`, 3 parlantes, bloque de 4096 =
85 ms, dos corridas por motor, load ~3,5):

| Configuración | numpy (mediana) | Rust (mediana) | Tiempo real numpy → Rust |
|---|---|---|---|
| por defecto | 4,8 ms | 1,5–1,7 ms | 18× → 51–56× |
| todo encendido (`protect` + armónicos) | 6,1–6,5 ms | 3,9–4,1 ms | 13–14× → 21–22× |
| todo encendido (crossover a un Charge 6) | 5,3–5,8 ms | 3,1–3,3 ms | 15–16× → 26–27× |
| medidores de sonoridad (aparte) | 0,79–0,87 ms | 0,63–0,75 ms | (siguen en numpy) |

- **Por defecto, Rust deja el motor unas 3 veces más rápido.** Con todo encendido, ~1,6 veces: lo que
  queda es de las etapas sin portar (EQ, graves virtuales, crossover, decorrelación, limitador).
- **Con numpy, el motor no llega en `HP-O16` al presupuesto de la spec 2026-10-02 §5** (≥ 20× tiempo real
  con todo encendido): da 13–16×. **Con Rust, sí** (21–27×).

**Lo que falta antes de usarlo en serio:**
- el defecto abierto (superado 2026-10-08: corregido, `backend.built` atrapa cualquier `Exception` al
  construir o configurar y vuelve a numpy; ver el roadmap i-7c8794-fd9732): una etapa Rust que falla **al construirse** (no por pánico) tumba la sesión en vez de
  volver a numpy (`backend.guarded` solo atrapa `RuntimeError`; roadmap i-7c8794-fd9732);
- los menores que dejó la revisión de R6 (ledger del plan);
- **nada se ha oído todavía con el motor Rust**: falta escucharlo con los audífonos o los parlantes, y un
  A/B frente a numpy.

## 5 · Los filtros FIR por convolución FFT (`dsp/eq.py`), 2026-10-08

La cuarta etapa portada (plan Task 7): `StreamingFIR` (solapar y sumar con cola, un espectro de
los coeficientes por tamaño de FFT) y `PartitionedFIR` (particionado uniforme, solapar y guardar,
con línea de retardo en frecuencia, `skip` y el camino exacto del bloque corto) en
`engine/crates/aurasync-dsp/src/fir.rs`, como structs que el código Rust que venga (el crossover,
los graves virtuales) puede usar directo. Las clases numpy son dueñas del objeto Rust cuando el
motor es Rust, así que **todos sus usuarios pasan a Rust sin cambiar**: el EQ por parlante
(`motor.py`), las ramas del crossover (`dsp/crossover.py`), la protección de graves y su pasa-todo
(`chain_stages.py`), los graves virtuales (`dsp/virtual_bass.py`) y la cola difusa
(`dsp/diffuse.py`). El estado que se mueve en el fondo de un corte, en las dos direcciones y
exacto: los coeficientes y la cola; la historia, la línea de retardo, su cabeza y si es válida (los
espectros cacheados se rehacen).

**Golden (MEDIDO, `host/tests/test_eq_rust.py`, 118 tests): ≤ 3,1e-13** de diferencia absoluta con
numpy en los 62 tests que comparan los dos motores directo (el máximo, con entradas enteras de
hasta ±100; con las demás entradas, ≤ 1,4e-13): los casos de `tests/test_eq.py` (bloques de 1500, la secuencia del
caché con `set_taps` del mismo largo y de otro, 2^19 muestras de una vez, las particiones 4096,
1024 y 512, `skip` y seguir), las curvas de `eq.fir` con los 2048 coeficientes reales (con y sin
presupuesto y tope de agudos), bloques de tamaños impares (más cortos que los coeficientes, igual a
la partición, más largos, 0, 1) y al azar, `set_taps` y `taps = ...` a mitad de la corrida con el
mismo largo, más largo y más corto, los impulsos del crossover (60–250 Hz, LR4 y LR8; paso bajo,
paso alto y pasa-todo), bloques y `skip` al azar sobre siete combinaciones de coeficientes y
partición (impares incluidas), entradas con paso, enteras y coeficientes float32. El motor completo
(el EQ con un cambio de curvas en un corte; `protect` con armónicos; crossover con cola difusa) y
`VirtualBass` y `Diffuse` enteros, ≤ 1,9e-15. El cambio de motor a mitad de la corrida, en los dos
sentidos, iguala a la corrida que nunca cambió. **Dos fallas plantadas en Rust**, vistas fallar y
retiradas: el orden de la línea de retardo al rehacerla (`head + k` en vez de `head - k`) dejó 17 de
118 rojos (de 1,2e-3 a 204) y la cola sumada una muestra corrida dejó 44 rojos (de 0,054 a 33,8,
el motor incluido).

**Hallazgo menor (MEDIDO):** `StreamingFIR` de numpy no convierte su entrada, y numpy ≥ 2 hace la
FFT de un bloque float32 en precisión simple: 8,9e-8 de diferencia con el mismo bloque en float64.
El camino Rust convierte todo bloque a float64 (como en las otras etapas), así que con float32 los
dos motores difieren en eso; con el bloque en float64 coinciden. Ningún usuario pasa float32 (los
bloques del motor ya son float64 cuando llegan a un filtro; `PartitionedFIR` y el crossover
convierten), así que no se tocó numpy; lo fija
`test_float32_blocks_are_filtered_in_double_precision`.

**Asignación de memoria (MEDIDO, `engine/crates/aurasync-dsp/tests/fir.rs`):** `process` y `skip`
no piden memoria una vez que existe el tamaño de FFT de ese bloque (un contador de asignaciones lo
comprueba, con un control que muestra que las ve); un cambio de coeficientes del mismo largo
recalcula los espectros en su lugar. El primer bloque de un largo nuevo arma su plan, sus búferes y
el espectro de los coeficientes, como numpy arma su caché. En una sesión hay un solo largo, el
bloque del servicio (4096), más a lo sumo uno por cada bloque corto distinto (el último de un
archivo); el filtro particionado no pide nada en sus bloques llenos, ni en el primero. Los planes
de FFT salen de un planificador por hilo, compartido entre filtros. **Un filtro arma su objeto Rust
en su primer bloque**, no al construirse: `VirtualBass` construía dos `PartitionedFIR` nuevos en
cada bloque mientras sus armónicos estaban apagados (superado 2026-10-08, §6: ya solo los rehace si
corrieron) y nunca los corría, y así no cuestan ni un objeto
Rust ni un registro en `backend` (que además ahora barre los registros muertos al registrar).

### 5.1 Entorno y método (MEDIDO)

El mismo equipo `HP-O16` (i5-11400H, 12 hilos), ahora con kernel 7.2.9-1-cachyos; python 3.12.12,
numpy 2.5.3; la extensión del entorno `hatch-test` (`--release`, feature `test-panic`). Energía
leída por la sonda antes y después de cada corrida: red conectada, gobernador `powersave`, perfil
`performance`, `no_turbo` = 0, igual en todas. Sonda: `probes/20-costo-sinc-rust/costo_fir.py`.
Bloque de 4096 muestras (85,3 ms), ruido de 0,1 de escala, 50 bloques de calentamiento y **500
medidos**, mediana y p95. "numpy" y "Rust" pasan por la clase numpy que despacha (con `backend` y
la conversión a float64); "solo Rust" llama al objeto de la extensión directo.

- **EQ por parlante:** un `StreamingFIR` de 2048 coeficientes (una curva distinta por parlante)
  por parlante, como el motor; el tiempo es el de todos juntos en un bloque.
- **Crossover:** sus dos ramas (`HighPass` y `LowPass` a 100 Hz, LR4, 2048 coeficientes), un
  bloque cada una.
- **Graves virtuales:** un `PartitionedFIR` como lo usa `VirtualBass`: el filtro de banda a 90 Hz
  (10 239 coeficientes), particiones de 4096.

### 5.2 Resultado (ms por bloque; `x` = mediana numpy / mediana Rust)

Corrida 1 (carga de 1 minuto 0,72 antes y después):

| caso | numpy med | numpy p95 | Rust med | Rust p95 | x | solo Rust |
|---|---|---|---|---|---|---|
| EQ, 1 parlante | 0,095 | 0,101 | 0,033 | 0,036 | 2,8 | 0,033 |
| EQ, 3 parlantes | 0,282 | 0,302 | 0,100 | 0,109 | 2,8 | 0,102 |
| EQ, 8 parlantes | 0,769 | 0,993 | 0,285 | 0,321 | 2,7 | 0,274 |
| crossover, 2 ramas | 0,190 | 0,204 | 0,071 | 0,081 | 2,7 | – |
| graves virtuales, 1 filtro | 0,126 | 0,135 | 0,046 | 0,056 | 2,7 | 0,044 |

Corrida 2, independiente, medio minuto después (carga 1,34 antes; 1,71 después):

| caso | numpy med | numpy p95 | Rust med | Rust p95 | x | solo Rust |
|---|---|---|---|---|---|---|
| EQ, 1 parlante | 0,108 | 0,161 | 0,036 | 0,043 | 3,0 | 0,035 |
| EQ, 3 parlantes | 0,321 | 0,515 | 0,113 | 0,197 | 2,8 | 0,115 |
| EQ, 8 parlantes | 0,891 | 1,207 | 0,328 | 0,529 | 2,7 | 0,311 |
| crossover, 2 ramas | 0,206 | 0,386 | 0,078 | 0,101 | 2,6 | – |
| graves virtuales, 1 filtro | 0,139 | 0,192 | 0,046 | 0,055 | 3,0 | 0,048 |

Lo que cuesta armar (mediana de 30, ms; corrida 1 / corrida 2):

| | numpy | Rust |
|---|---|---|
| construir un `StreamingFIR` de 2048 | 0,002 / 0,002 | 0,002 / 0,003 |
| su primer bloque de 4096 (Rust: arma el objeto, copia el estado y el espectro) | 0,138 / 0,156 | 0,059 / 0,063 |
| un bloque de 1000 por primera vez (un tamaño nuevo) | 0,069 / 0,077 | 0,025 / 0,026 |
| el mismo bloque de 1000 otra vez | 0,048 / 0,053 | 0,015 / 0,016 |
| construir el `PartitionedFIR` de banda de `VirtualBass` y su primer bloque | 0,260 / 0,302 | 0,306 / 0,317 |

Datos crudos: [datos/20/fir-corrida-1.txt](datos/20/fir-corrida-1.txt) y
[datos/20/fir-corrida-2.txt](datos/20/fir-corrida-2.txt).

### 5.3 La cadena entera (MEDIDO)

`probes/18-costo-de-la-cadena/costo.py` (3 parlantes con EQ, 120 bloques de 4096, mediana de los
últimos 110), con `AURASYNC_ENGINE=numpy` y `=rust`, dos corridas de cada uno alternadas, con la
extensión de `hatch-test` (carga de 1 minuto 0,64–1,02). Con Rust corren ahora en Rust la lectura
sinc, el upmix, el extractor y los filtros FIR; el resto (decorrelación, limitador, rampas,
medidores) sigue en numpy.

| configuración | numpy (mediana) | Rust (mediana) | tiempo real numpy → Rust | Rust antes de esta etapa (§4) |
|---|---|---|---|---|
| por defecto | 5,13 / 6,11 ms | 1,55 / 1,68 ms | 14–17× → 51–55× | 1,5–1,7 ms |
| todo encendido (`protect` + armónicos) | 7,70 / 7,77 ms | 2,92 / 3,44 ms | 11× → 25–29× | 3,9–4,1 ms |
| todo encendido (crossover a un Charge 6) | 6,21 / 6,93 ms | 2,44 / 2,96 ms | 12–14× → 29–35× | 3,1–3,3 ms |

Datos crudos: [datos/20/cadena-con-fir.txt](datos/20/cadena-con-fir.txt).

**Qué dice:**
- **Por filtro, Rust es 2,6–3,0 veces más rápido**, en las dos corridas y en los tres usos. Como
  en el upmix, el grueso son FFT que numpy ya hace en C; lo que Rust ahorra es la sobrecarga de
  numpy por llamada (crear arreglos, el producto y la suma en pasos separados) más que la FFT
  (INFERIDO; no se perfiló). "Rust" y "solo Rust" coinciden dentro del ruido: el paso por la clase
  numpy y `backend` no llega a 0,002 ms por filtro.
- Armar el objeto Rust en el primer bloque cuesta menos que el primer bloque de numpy. El
  `PartitionedFIR` cuesta algo más de armar con Rust (0,31 contra 0,26–0,30 ms), porque hoy se
  calculan las particiones dos veces (en numpy y en Rust); pasa una vez por filtro, y los que
  `VirtualBass` rehace apagado no lo pagan (superado 2026-10-08, §6: ya no los rehace cada bloque).
- **En la cadena con todo encendido, Rust baja de ~3,9–4,1 ms (§4) a ~2,4–3,4 ms**: con
  `protect` + armónicos el motor queda en 25–29× tiempo real con Rust, contra 11× con numpy.
  numpy midió ahora más lento que en §4 (7,7 contra 6,1–6,5 ms) con menos carga; la sonda de la
  cadena tiene pocos bloques (110) y varía entre corridas, así que la comparación vale dentro de
  cada corrida, no entre días.
- Por defecto la diferencia (~0,2 ms que ahorra el EQ de 3 parlantes) queda dentro del ruido de
  la sonda.

### 5.4 Cómo reproducirlo

```bash
cd host && hatch test tests/test_eq_rust.py
$(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_fir.py
AURASYNC_ENGINE=rust $(hatch env find hatch-test.py3.12)/bin/python ../probes/18-costo-de-la-cadena/costo.py
```

## 6. La etapa de graves (crossover y protección) sobre los FIR en Rust (MEDIDO)

La tarea 10 midió primero adónde se va el tiempo de `chain_stages.BassStage` por bloque, con los
FIR ya en Rust (§5), para portar solo lo que siguiera pesando en numpy.

**Condiciones** (las mismas que §5): HP-O16, kernel 7.2.9-1-cachyos, python 3.12.12, numpy 2.5.3,
extensión de `hatch test` (release, `test-panic`), corriente alterna, gobernador powersave, perfil
performance, `no_turbo` 0 (leídos antes y después de cada corrida). Bloques de 4096, 500 medidos
tras 50 de calentamiento; dos corridas (carga de 1 minuto 0,90 y 0,79). La etapa se llama como la
llama el motor: `feed` una vez por bloque y, por parlante, `before_delay` y `process`. `protect`
usa Go 4 sin armónicos (-24 dB, apagado, el valor por defecto); `crossover` usa un Charge 6 y
los demás Go 4. Sonda: `probes/20-costo-sinc-rust/costo_graves.py`.

### 6.1 Dónde se iba el tiempo

| paso por bloque (3 parlantes) | numpy | Rust |
|---|---|---|
| convolución (`StreamingFIR.process`), `crossover` | 0,372 ms | 0,134 ms |
| lo demás de `crossover`: la media `0,5 (L+R)`, dos energías por rama, un logaritmo, el retardo | 0,024 ms (6 %) | 0,022 ms (14 %) |
| lo demás de `protect` (tras el arreglo de abajo) | 0,023 ms | 0,022 ms |

Cada paso suelto cuesta 0,0002 a 0,0035 ms (`0,5 * (L + R)` 0,0034; dos `np.dot` 0,0019; el
`log10` escalar 0,0002; el retardo con `concatenate` 0,0015; sumar la alimentación 0,0022). **Con
8 parlantes lo que no es convolución son 0,045 ms de ~0,35 ms en Rust.** No queda nada del paso
por bloque que valga portar: **no se portó nada más a Rust** en esta tarea. El diseño del filtro
(`impulses`, `_butterworth`, `response`, `group_delay_dc_ms`) es de diseño, no de bloque, y sigue
en numpy.

**Un hallazgo que sí pesaba, y no era convolución:** con los armónicos apagados (el valor por
defecto de `protect`), `VirtualBass._reset()` armaba **dos `PartitionedFIR` nuevos en cada
bloque** (el constructor de numpy calcula el espectro de las particiones: ~0,2 ms por parlante).
Eso eran 0,64 ms de 0,94 ms por bloque con 3 parlantes y escondía casi toda la ganancia de Rust
en `protect` (1,2× en vez de 2,5×). Se arregló en numpy, sin tocar Rust: `_reset()` solo rehace
los filtros si corrieron desde la última vez (los que quedan están en reposo), así que la salida
es idéntica bit a bit y el costo apagado desaparece en los dos motores.

### 6.2 Costo por bloque (ms; mediana y p95; corrida 1 / corrida 2)

| caso | numpy med | numpy p95 | Rust med | Rust p95 | aceleración |
|---|---|---|---|---|---|
| `protect`, 1 parlante | 0,103 / 0,103 | 0,111 / 0,111 | 0,041 / 0,040 | 0,043 / 0,042 | 2,5–2,6× |
| `protect`, 3 parlantes | 0,299 / 0,301 | 0,311 / 0,314 | 0,123 / 0,122 | 0,131 / 0,126 | 2,4–2,5× |
| `protect`, 8 parlantes | 0,804 / 0,807 | 0,832 / 0,831 | 0,326 / 0,323 | 0,340 / 0,332 | 2,5× |
| `crossover`, 3 parlantes (2 pasa-altos, all-pass, alimentación) | 0,394 / 0,394 | 0,404 / 0,409 | 0,156 / 0,155 | 0,164 / 0,162 | 2,5× |
| `crossover`, 8 parlantes (7 pasa-altos, all-pass, alimentación) | 0,890 / 0,886 | 0,916 / 0,910 | 0,358 / 0,349 | 0,372 / 0,385 | 2,5× |

(Con 1 parlante `crossover` no tiene parlantes chicos: queda el Charge 6 solo con el all-pass y
la alimentación, por eso no se mide.)

Antes del arreglo de `VirtualBass`, la misma sonda dio `protect` con 1 / 3 / 8 parlantes: numpy
0,314 / 0,943 / 2,569 ms, Rust 0,259 / 0,776 / 2,122 ms (1,2×). `crossover` no cambió. Datos
crudos: [datos/20/graves-corrida-1.txt](datos/20/graves-corrida-1.txt),
[datos/20/graves-corrida-2.txt](datos/20/graves-corrida-2.txt) y
[datos/20/graves-antes-del-arreglo.txt](datos/20/graves-antes-del-arreglo.txt).

**Qué dice:** la etapa gana lo mismo que cada filtro (§5: 2,6–3,0×), porque casi todo su tiempo es
convolución; con 8 parlantes la etapa de graves pasa de 0,89 a 0,35 ms por bloque de 85,3 ms
(0,4 % del tiempo real). La sonda comprueba, además, que bajo `engine=rust` cada filtro de la
etapa tiene su objeto Rust, y ninguna corrida falló.

### 6.3 Cómo reproducirlo

```bash
cd host && hatch test tests/test_crossover_rust.py
$(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_graves.py
```

## 7 · El generador de armónicos (`dsp/virtual_bass.py`, `VirtualBass`) en Rust (MEDIDO, 2026-10-08)

La tarea 11 porta el trabajo por bloque de `VirtualBass`: el filtro de banda de graves, el
rectificador (`abs`), el filtro de armónicos, la calibración y la rampa de la ganancia cuando
`harmonics_db` cambia en vivo. El diseño (`_filters`) sigue en numpy. El objeto Rust **posee
directamente sus dos `PartitionedFir`** (una llamada por bloque, sin pasar por Python entre un
filtro y otro); la clase numpy lo arma en el primer bloque que corre los filtros y conserva
`_current` (la posición de la rampa) y `_dirty` (la regla de la tarea 10: rehacer los filtros solo
si corrieron).

**Condiciones** (las mismas que §5 y §6): HP-O16, kernel 7.2.9-1-cachyos, python 3.12.12, numpy
2.5.3, extensión de `hatch test` (release, `test-panic`), corriente alterna, gobernador
powersave, perfil performance, `no_turbo` 0 (leídos antes y después). Carga de 1 minuto 0,85–0,90.
Bloques de 4096, 500 medidos tras 50 de calentamiento, dos corridas. Armónicos a 0 dB, 90 Hz,
ruido de amplitud 0,1; un `VirtualBass` por parlante (el tiempo es el de todos juntos). Sonda:
`probes/20-costo-sinc-rust/costo_graves_virtuales.py`.

### 7.1 Costo por bloque (ms; mediana; corrida 1 / corrida 2)

| parlantes | numpy | FIR en Rust llamados desde Python (estado tras la tarea 7) | Rust (el generador entero) | numpy → Rust | FIR-Rust → Rust |
|---|---|---|---|---|---|
| 1 | 0,266 / 0,262 | 0,097 / 0,097 | 0,098 / 0,101 | 2,6–2,7× | 1,0× |
| 3 | 0,802 / 0,793 | 0,305 / 0,302 | 0,301 / 0,316 | 2,5–2,7× | 1,0× |
| 8 | 2,188 / 2,168 | 0,860 / 0,857 | 0,855 / 0,904 | 2,4–2,6× | 0,9–1,0× |

(p95 y datos crudos: [datos/20/virtual-bass-corrida-1.txt](datos/20/virtual-bass-corrida-1.txt),
[datos/20/virtual-bass-corrida-2.txt](datos/20/virtual-bass-corrida-2.txt).)

La parte fija, en mediana de 200 veces: armar el objeto Rust con su estado 0,18 ms, leer su estado
0,07 ms, cargarlo en numpy 0,10 ms. Pasa una vez por cambio de motor y por parlante.

**Qué dice:**
- **Portar el generador entero no gana nada sobre lo que ya daba la tarea 7** (1,0×, dentro del
  ruido en las dos corridas). El 2,5–2,7× contra numpy es de los dos `PartitionedFIR` en Rust; lo
  que quedaba en numpy por bloque (el `abs`, un producto, la rampa, dos `np.dot`) es del orden de
  0,01 ms por parlante, y una llamada de Python a Rust cuesta lo mismo que las que reemplaza.
  Esto repite lo de §6.1 (la convolución es 86–95 % de la etapa).
- La razón para tener el objeto entero en Rust no es la velocidad de hoy: el generador queda **en
  Rust de punta a punta** sin pasar por Python entre filtros (lo que pide un `RustMotor`) y con
  su ausencia de asignaciones comprobada (`engine/crates/aurasync-dsp/tests/virtual_bass.rs`).
- Con los armónicos apagados no se arma ni se registra nada (el valor por defecto de `protect`),
  como en la tarea 7.

### 7.2 La cadena entera (MEDIDO)

`probes/18-costo-de-la-cadena/costo.py` (3 parlantes con EQ, 120 bloques de 4096, mediana de los
últimos 110), con `AURASYNC_ENGINE=numpy` y `=rust`, dos corridas de cada uno alternadas, con la
extensión de `hatch-test` (carga de 1 minuto 0,88–0,90). Datos crudos:
[datos/20/cadena-con-graves-virtuales.txt](datos/20/cadena-con-graves-virtuales.txt).

| configuración | numpy (mediana) | Rust (mediana) | tiempo real numpy → Rust | Rust en §5.3 | numpy en §4 |
|---|---|---|---|---|---|
| por defecto | 4,82 / 4,81 ms | 1,34 / 1,33 ms | 18× → 64× | 1,55 / 1,68 ms | 4,8 ms |
| todo encendido (`protect` + armónicos) | 6,10 / 6,09 ms | 2,63 / 2,68 ms | 14× → 32× | 2,92 / 3,44 ms | 6,1–6,5 ms |
| todo encendido (crossover a un Charge 6) | 5,29 / 5,28 ms | 2,29 / 2,25 ms | 16× → 37–38× | 2,44 / 2,96 ms | 5,3–5,8 ms |
| medidores de sonoridad (aparte) | 0,736 / 0,744 ms | 0,571 / 0,629 ms | (siguen en numpy) | – | 0,79–0,87 ms |

**Qué dice:** los números de numpy de hoy coinciden con los de §4 (4,8 / 6,1 / 5,3 ms) y no con
los de §5.3 (más lentos, 5,1–7,8 ms): la comparación vale dentro de una sesión de medida, no
entre días. Con Rust la cadena baja de 2,9–3,4 ms (§5.3) a 2,6–2,7 ms con `protect` + armónicos y
de 1,55–1,68 a 1,33–1,34 ms por defecto; parte de esa baja es la que trajo la tarea 10
(`VirtualBass` ya no rehace sus filtros cada bloque) y no el generador en Rust, que no ahorra
nada medible por sí mismo (§7.1). Presupuesto de la spec 2026-10-02 §5 (≥ 20× tiempo real con
todo encendido): numpy 14–16× (no llega), Rust 32–38× (sí).

### 7.3 Cómo reproducirlo

```bash
cd host && hatch test tests/test_virtual_bass_rust.py
$(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_graves_virtuales.py
AURASYNC_ENGINE=rust $(hatch env find hatch-test.py3.12)/bin/python ../probes/18-costo-de-la-cadena/costo.py
```
