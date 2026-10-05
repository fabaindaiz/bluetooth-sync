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
