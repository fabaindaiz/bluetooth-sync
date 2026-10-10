# 23 · Ensayo de las pruebas A/B del motor y de la ecualización con el monitor, 2026-10-08

**Pregunta:** ¿funcionan de punta a punta las dos pruebas A/B que el producto ofrece —el cambio de motor
(numpy ↔ Rust) y las perillas de la ecualización— escuchadas por el monitor en audífonos, sin parlantes?
Es un **ensayo del procedimiento**, no una medición de calidad: los parlantes de la instalación son
virtuales y la curva de ecualización es sintética, así que de aquí no sale si la ecualización mejora un
parlante real (eso sigue en PC-Ryzen5 con los JBL, roadmap i-7c8794-d9c64a).

**Veredicto (parcial, MEDIDO; la prueba se detuvo a pedido del usuario antes del ABX en el panel):**
- **La ecualización hace lo que dice**, medida en la salida del monitor: con los medios como referencia, el
  tope de agudos coincide con lo esperado con un error mediano de 0,23 dB, y el encendido/apagado con un
  error de 0,61 dB (el ruido de la medición, visto en la repetición, es de 0,89 dB). El presupuesto de 3 dB
  sobre esta curva baja solo ~0,5 dB, por debajo del ruido: no se puede medir aquí.
- **Los dos motores no se distinguen en la salida** (0,19 dB de diferencia mediana, menos que la
  repetición), y **Rust cuesta la mitad**: 5,0 ms por bloque contra 10,8 ms de numpy (bloque de 85,3 ms,
  4 parlantes virtuales, con la difusión y el limitador de pico verdadero encendidos).
- **El cambio de motor se oye, y por el corte**: el usuario lo notó por los cortes de 80 + 80 ms, no por el
  sonido. De ahí la propuesta del §3, que el usuario pidió implementar (así era hasta la etapa 1; ver §3: ahora el cambio de motor es entre bloques, sin corte).

## 0. Entorno

| Cosa | Valor |
|---|---|
| Equipo | `HP-O16` (CachyOS, Intel AX210) |
| Salida | monitor en modo `mix` a los audífonos WH-CH520 (`bluez_output.14_06_A7_6B_E3_F0.1`), ganancia 0 dB |
| Instalación | 4 parlantes virtuales (`Virtual 1`..`Virtual 4`, sin sink) |
| Servicio | `aurasync service` del árbol en `0832684`, extensión Rust reconstruida con `hatch run engine-build` |
| Fuente | la música que elija el usuario, al sink `aurasync` |
| Sonda | `probes/24-ab-monitor/ab.py` (desechable, d-7c8794-3208b7) |

## 1. Método (escrito antes de medir)

**Preparación** (`ab.py preparar`): respalda en `~/.local/share/aurasync/ab-ensayo-respaldo.json` las
curvas `eq_db` de los parlantes, el EQ encendido o no, los parámetros de la etapa `eq` y el motor pedido.
Después pone en los 4 parlantes una **curva sintética, marcada como de prueba**: +6 dB de 50 a 125 Hz que
bajan a 0 en 315 Hz, plana en medios, y de +2 dB en 5 kHz a +5 dB sobre 8 kHz (el máximo es
`eq.MAX_BOOST_DB` = 6). Sin una curva, las perillas de la ecualización no cambian nada audible. Guarda
tres pares de presets, cada uno con una sola variable:

| Par | A | B | Qué se espera oír |
|---|---|---|---|
| `ensayo-eq` | EQ encendido | EQ apagado | B más opaco y con menos graves |
| `ensayo-presupuesto` | presupuesto 0 (sin tope) | presupuesto 3 dB | B con la curva bajada entera |
| `ensayo-agudos` | tope de agudos 6 dB | tope 0 dB | B sin el brillo sobre 8 kHz |

La curva tipo Harman no existe todavía como perilla: queda fuera del ensayo.

**A/B de la ecualización:** para cada par, `ab_start` con `match_loudness: true`, y el usuario hace el
ABX en el panel, con lo que también se ensaya la interfaz del A/B (la auditoría del 22 la midió en 7 o más
acciones). Se anotan los aciertos (`state.ab`), la compensación de volumen que aplicó el servicio, y si la
diferencia queda en el timbre y no en el volumen.

**A/B del motor:** no se hace como ABX. Cada cambio pasa por un corte de 80 + 80 ms (`engine_set` cambia en
el próximo corte) que se puede oír, y eso revela cuándo hubo un cambio aunque no cuál motor quedó. Además,
las dos salidas son iguales a menos de 1e-12 (tests de paridad), así que lo esperado es **no
distinguirlas**. Lo que sí sirve medir: si el corte del cambio se oye, cuánto le cuesta cada motor
(`motor_ms`, `realtime_x`) y si aparecen cortes o xruns (así era hasta la etapa 1; ver §3: ahora el cambio de motor es entre bloques, sin corte).

**Medición objetiva, además de lo que se oye** (para sacarle el máximo a la sesión):

- `ab.py registrar` corre en segundo plano y anota el estado cada segundo (motor, `motor_ms`, xruns, cortes,
  colchón, contadores del monitor, A/B, latencia, calidad) en `registro.jsonl`, junto con una marca por
  cada acción del asistente (preset cargado, motor cambiado, captura).
- `ab.py barrido`: carga cada preset, espera 3 s y graba 20 s de la entrada (monitor del sink `aurasync`) y
  de la salida (monitor del sink de los audífonos), y comprueba con `pw-link` que cada grabador quedó
  conectado donde se pidió. Graba también el preset base otra vez (la **repetición**, que dice cuánto es
  ruido de la medición) y el preset base con cada motor.
- `analizar.py` calcula de cada captura el retardo entrada → salida, los niveles y la función de
  transferencia por tercio (Welch, con su coherencia). Después compara la diferencia B − A de cada par con
  la que predice `eq.limited` para la curva de prueba. Probado antes con capturas sintéticas de curvas
  conocidas: error máximo 0,68 dB y retardo exacto.
- `ab.py pesado`: con los graves en `crossover` (la difusión y el limitador de pico verdadero ya están
  encendidos en esta instalación), 30 s con numpy, 30 s con Rust y otros 30 s con numpy, para medir el costo
  del motor cuando tiene más que hacer.
- `ab.py cambios 6`: seis cambios de motor en momentos al azar (cada 8–20 s); el usuario cuenta los que
  oye, y la cuenta se compara con las marcas.

**Al terminar:** `ab.py restaurar` vuelve a dejar las curvas, la etapa `eq`, el motor y los presets como
estaban, y se comprueba con el estado.

## 2. Resultados (MEDIDO, 2026-10-08, 14:47–14:59)

Datos: `datos/23/registro.jsonl` (el estado cada segundo, con las marcas de cada acción) y
`datos/23/analisis.json` (las curvas por tercio de cada captura y cada comparación). Las grabaciones
(~150 MB) quedan fuera del repositorio.

**Barrido** (9 capturas de 20 s, todas conectadas donde se pidió, comprobado con `pw-link`):

| Comparación | Desplazamiento | Error de forma, mediano | Máximo |
|---|---|---|---|
| EQ apagado − encendido | +7,30 dB | 0,61 dB | 2,75 dB (50 Hz) |
| Presupuesto 3 − 0 dB | −1,35 dB | 0,67 dB | 3,59 dB |
| Tope de agudos 0 − 6 dB | +0,08 dB | 0,23 dB | 0,94 dB |
| Repetición de la base (ruido) | −0,23 dB | 0,89 dB | 1,83 dB |
| Rust − numpy | −0,13 dB | 0,19 dB | 1,02 dB |

- El **desplazamiento** es el cambio en los medios, donde la curva es plana. Con el EQ apagado, la salida
  queda 7,3 dB más fuerte. Lo más probable es que venga del emparejamiento de volumen por render
  (`render_match.py`: `front` se ajusta al volumen de `classic` y el realce de graves sube el volumen
  medido) y del limitador. Esto es **INFERIDO**: ese ajuste no aparece en el estado, así que no se pudo
  verificar.
- La coherencia entre la entrada y la salida es casi nula (< 0,15), porque el ambiente adaptativo, la
  decorrelación y la cola de difusión no son lineales e invariantes. Por eso se comparan razones de
  energía por tercio, no H1, que daba valores sesgados hacia abajo.
- El **retardo** entrada → monitor varió entre 283 y 416 ms de una captura a otra. Va con el nivel del
  colchón del monitor (`level_ms` pasó de 128 a 85 y luego a 213 ms tras un relleno).

**Motor:** `motor_ms` mediano de 10,7–11,2 ms con numpy (p95 11,4–13,1) y de 5,0–5,1 ms con Rust
(p95 6,1–6,6), en cuatro tramos de cada uno. La configuración "pesada" no agregó nada: `bass=crossover`
no está disponible sin un parlante apto para graves, así que fue otra repetición de la base.

**Fallas en la sesión** (`state.health.cuts`): dos xruns del `pw-play` del monitor (al arrancar y tras
`monitor_set`) y una entrega tardía del motor de 79 ms a las 14:48:58, durante la captura de
`presupuesto-a`, que coincide con un relleno del monitor. Los cambios de motor y de preset aparecen todos
como cortes intencionales.

**Cambios de motor ocultos:** 6 cambios entre las 14:57:19 y las 14:58:43. El usuario avisó a las 14:57:26
(7 s después del primero; el aviso se anota cuando llega al chat) y dijo que "lo noto por los cortes". La
prueba se detuvo ahí, sin contar el resto.

## 3. Propuesta que salió de la prueba: cambio de motor sin corte

Pedida por el usuario. El motor nuevo corre en sombra sobre la misma entrada hasta llenar su estado
(filtros, líneas de retardo, extractor), y en un borde de bloque la salida pasa a él con un fundido
cruzado corto, en lugar de bajar a cero. Como las dos salidas son iguales a menos de 1e-12, el fundido no
debería oírse. Durante el traspaso corren los dos motores (10,8 + 5,0 ms por bloque de 85 ms). El
diseño va en una spec propia.

**Estado de la etapa 1 (2026-10-08):** construida. Spec:
[superpowers/specs/2026-10-08-seamless-transitions-design.md](../../superpowers/specs/2026-10-08-seamless-transitions-design.md);
plan: [superpowers/plans/2026-10-08-seamless-transitions-stage-1.md](../../superpowers/plans/2026-10-08-seamless-transitions-stage-1.md).
Decisión: d-7c8794-cdc30f.

- **MEDIDO** (tests del motor con ruido, `HP-O16`, 2026-10-08): un cambio de motor en caliente sobre la
  cadena completa difiere ≤ 6,5e-14 de no haber cambiado nunca.
- **MEDIDO** (mismos tests): en un movimiento puro de retardo, el fundido `equal_gain` cae 2,5–3,2 dB;
  `equal_power` se queda dentro de 0,8 dB. **Fallo:** los fundidos de retardo usan siempre `equal_power`;
  `shape` vale para los fundidos de la etapa 2.
- **Escucha de la etapa 1 (spec §7), pendiente:** (1) los cambios de motor ocultos, contados otra vez;
  (2) A/B de los presets de prueba con `crossfade` contra `cut`; (3) la pregunta del peine: un cambio de
  retardo de 10–30 ms con fundido de 80 y de 200 ms; (4) el posible +3 dB en los graves bajos de un
  fundido de retardo `equal_power` (las dos lecturas están correlacionadas allí).

## 4. Cortes por CPU ajena mientras corrían los tests (MEDIDO / VERIFICADO, 2026-10-08, 16:38)

Durante la implementación de las transiciones sin corte, la suite de tests completa (~1870 tests, unos 13 min,
carga media 9,2 en 12 núcleos) corrió en `HP-O16` mientras la sesión seguía sonando con el monitor. El
usuario oyó cortes. Medido en el estado del servicio (`health.cuts`):

- **179 entregas tardías del motor en 10 min** (`late`, de 45 a 98 ms tarde) y 1 xrun; el monitor llevaba 84
  rellenos y 7 recortes. **El motor no era lento:** `motor_ms` = 13,9 ms de un presupuesto de 85,3 ms. Le
  faltó CPU, es decir, llegó tarde porque otros procesos le ganaron el procesador. **MEDIDO.**
- **El servicio corre con prioridad normal, y un poco menos:** todos sus hilos tienen `nice` +1 y clase
  `TS` (heredado del shell que lo lanzó). PipeWire, en cambio, pide tiempo real a RealtimeKit. **VERIFICADO**
  con `ps -L`.
- **Lo que se puede pedir sin root en este equipo:**
  - `RLIMIT_NICE` es 31, así que un hilo puede bajar su `nice` hasta −11 por sí mismo.
  - RealtimeKit está activo, con `MinNiceLevel` −15, `MaxRealtimePriority` 20 y `RTTimeUSecMax` 200 ms.
  - CachyOS corre además `ananicy-cpp`.
  
  **VERIFICADO.**

Opciones, de menor a mayor riesgo (la decisión está en la conversación del 2026-10-08):

1. Correr el trabajo pesado propio (tests, compilaciones) con `nice -n 19`. Es inmediato y no toca el
   servicio.
2. Que el hilo del motor pida más prioridad: `nice` −11 por sí mismo, o −15 con RealtimeKit. Es por hilo y
   se revierte al reiniciar. Un límite: los otros hilos de Python comparten el GIL con el motor, así que esto
   ayuda contra *otros procesos* (el caso medido aquí), no contra el propio servicio.
3. Tiempo real (`SCHED_FIFO` vía RealtimeKit), como PipeWire. Tiene dos riesgos:
   - Un hilo de Python en tiempo real que espera el GIL de un hilo normal produce inversión de prioridad.
   - Si pasa 200 ms sin ceder, el kernel lo castiga.

   Solo se haría después de medir la opción 2.

Cómo medirlo: con la misma carga (la suite de tests o `stress-ng`), contar las entregas tardías en 10 min
con y sin la prioridad.

### 4.1 La prioridad del hilo del motor, medida (MEDIDO, 2026-10-09)

**Qué se construyó** (roadmap i-7c8794-246f79):
- La opción `"engine_nice"` de `service.json`, en -15 por defecto desde el 2026-10-09 (d-7c8794-923eed);
  `null` no toca nada. Al arrancar, el hilo del motor pide esa prioridad.
- **Lo que hereda la prioridad:** en Linux, un hilo o un proceso nuevo toma la prioridad de quien lo crea
  (VERIFICADO en `HP-O16`). Por eso lo que el hilo del motor lanza después también queda en -15: el monitor
  del radio, los hilos de trabajo y los `pw-play`. Los hilos HTTP no, porque los crea el hilo del servidor.
- Un hilo que ya tiene más prioridad que la pedida no se toca.
- El orden de los intentos (`host/src/aurasync/priority.py`):
  1. `setpriority` dentro de `RLIMIT_NICE`;
  2. si no alcanza, RealtimeKit por `busctl`;
  3. si RealtimeKit se niega, el piso que permita `RLIMIT_NICE`.
- El resultado se lee de vuelta en el sistema y se publica en `state.health.engine_priority`.
- **Un hallazgo al probarlo de verdad:** `busctl` tomaba el «-15» como una opción. Sin un `--`, el pedido
  nunca llegaba a RealtimeKit y el hilo caía a -11. Los tests con dobles no lo veían; lo mostró la primera
  corrida real. Se arregló, y en `HP-O16` el hilo obtiene -15 por RealtimeKit. **MEDIDO**: la salida de la
  corrida fallida y la de la corregida están en `datos/23/prioridad-motor-cargas.txt`.

**Método** (sonda `probes/25-prioridad-motor/medir.py`):
- La sonda no toca PipeWire ni el servicio. Corre el `Motor` real, con 4 parlantes, la cadena de `HP-O16` y
  el motor numpy.
- Un hilo tiene que entregar un bloque de 4096 muestras cada 85,3 ms.
- Se alterna cada 60 s entre prioridad normal (`nice` 0) y -15, para que una carga que cambia con el tiempo
  pese igual en las dos condiciones.
- Se cuentan los bloques que terminan después de su plazo y el tiempo de trabajo de cada bloque.

**Resultado** (datos en `datos/23/prioridad-motor-*.jsonl`). La sonda solo registra la carga (`load1`); qué
la generaba lo anotó el asistente que la lanzó, en `datos/23/prioridad-motor-cargas.txt`:

| Carga | Condición | Bloques | Tarde | Trabajo, mediana | Trabajo, p99 |
|---|---|---|---|---|---|
| La suite de tests sola (carga 1,2–2,8), 10 min | normal | 3517 | 0 | 9,1–9,8 ms | 10,4–26,2 ms |
| | -15 | 3515 | 0 | 9,2–10,0 ms | 10,5–23,7 ms |
| CPU saturada (14 procesos ocupados, carga 20–27), 4 min | normal | 1407 | 0 | 36,9 / 40,7 ms | 51,9 / 57,8 ms |
| | -15 | 1406 | 0 | 17,8 / 18,5 ms | 21,1 / 21,4 ms |

**Qué dice:**

1. **La suite de tests sola no reproduce el incidente del §4.** Carga poco (1–2 núcleos de 12), y no hubo
   ningún bloque tarde en ninguna condición. El incidente del 2026-10-08 ocurrió con carga 9,2, con varias
   suites y compilaciones a la vez. **MEDIDO.**
2. **Con la CPU saturada, la prioridad deja más del doble de margen.** El tiempo de trabajo por bloque baja
   de 37–41 ms a 18 ms en la mediana, y de 52–58 ms a 21 ms en el p99. La diferencia se repite en los dos
   tramos de cada condición. Ni siquiera así llegó un bloque tarde a la sonda: el motor numpy necesita unos
   9 ms en reposo, así que aun con prioridad normal cumplió su plazo. **MEDIDO.**
3. **La sonda no reproduce todo el servicio**, y por eso el número de entregas tardías del servicio no
   queda medido aquí. Al servicio se le suman:
   - la entrega a PipeWire (el `pw-play`) y la espera de la tubería;
   - el GIL compartido con los hilos HTTP;
   - lo que hay alrededor del motor en cada paso de la sesión.

   Que en el §4 hubo 179 entregas tardías con un motor de 13,9 ms indica que lo que más se retrasa es lo
   que rodea al motor. **INFERIDO.** Medirlo en el servicio exige arrancarlo, lo que crea su sink en
   PipeWire, así que queda pendiente y con permiso del usuario.

## 5. Escucha de la etapa 1 de transiciones sin corte (protocolo escrito antes de escuchar, 2026-10-09)

Roadmap i-7c8794-93f50c. La etapa 1 está en `HP-O16`, en la rama `seamless-transitions`. Se escucha con los
audífonos WH-CH520 por el monitor en modo `mix`, con los 4 parlantes virtuales, música elegida por el usuario
y el servicio en uso real (`engine_nice` -15 por defecto). La sonda es `probes/24-ab-monitor/ab.py`:
`escucha-preparar`, `alternar`, `modo`, `cambios` y `escucha-restaurar`. Las transiciones se anotan con hora
en `registro.jsonl` (`ab.py registrar`). El usuario avisa en el chat lo que oye, y la hora de llegada de cada
aviso se anota.

| # | Qué | Cómo | Qué se espera |
|---|---|---|---|
| E1 | Cambios de motor ocultos | `cambios 6`, momentos al azar cada 8–20 s | No se oye ninguno. Antes se oían todos, por el corte |
| E2 | Preset de rampas, fundido | `alternar escucha-rampas` con `crossfade` de 80 ms: pan, ganancia y ambiente cambian; ningún retardo se mueve | Cambio de imagen suave, sin hueco |
| E3 | El mismo, con corte | `modo cut` y `alternar escucha-rampas` | El hueco de antes, como referencia |
| E4 | Retardo, fundido de 80 ms | `modo crossfade 80` y `alternar escucha-retardo`: el retardo trasero va de 0 a 25 ms (5–20 ms por parlante) | La pregunta del filtro peine: ¿se oye un barrido o un «flanger» al cambiar? ¿Suben los graves un instante (igual potencia)? |
| E5 | Retardo, fundido de 200 ms | `modo crossfade 200` y lo mismo | ¿Mejor o peor que con 80 ms? |
| E6 | Comparar E4 y E5 con el corte | `modo cut` y `alternar escucha-retardo` | Referencia |

Al terminar: `escucha-restaurar` (deja los parlantes, el retardo trasero y la transición como estaban, y borra
los presets `escucha-*`). Con el servicio corriendo, se anota también `state.health.cuts` y
`state.health.engine_priority` de toda la sesión: las entregas tardías del servicio real con la prioridad
(i-7c8794-246f79).

### 5.1 Resultado de la escucha (MEDIDO, 2026-10-09, 14:16–14:36, `HP-O16`)

Datos: `datos/23/escucha-etapa-1-registro.jsonl`, con el estado del servicio cada segundo y una marca por cada
cambio y por cada aviso del usuario. Los avisos se anotan a la hora en que llegan al chat.

| # | Resultado |
|---|---|
| E1 | Hubo **6 cambios de motor ocultos y 2 avisos del usuario**. El primer aviso llegó 2,8 s después del cambio de las 14:17:30 y pudo ser ese cambio. El segundo llegó **0,15 s antes** del cambio siguiente, así que ese cambio no pudo causarlo. Los otros 4 cambios no se notaron. Un tercer aviso llegó a las 14:19:12, **sin ninguna prueba corriendo**. El usuario describió lo que oía como «un cambio de volumen o timbre». El servicio no registró ningún corte, entrega tardía ni relleno. El ajuste de volumen del monitor se movió apenas entre 0,1 y 0,8 dB, de forma lenta, así que tampoco lo explica. Conclusión: **los cambios de motor ya no se distinguen de lo que el oyente nota sin ellos**. En la prueba del 2026-10-08, en cambio, se oían todos por el corte |
| Control | Spotify fue directo a los audífonos durante unos 5 min. El usuario notó el paso al control **por el cambio de retardo** entre los dos caminos: aurasync suma 300–400 ms, lo que es inevitable. Su propuesta: usar el render `direct` del motor como control, sin salir del motor (queda para el A/B final) |
| E2 | Rampas con fundido de 80 ms: «**suaves, sin huecos**; apenas lo noté por los cambios de volumen». El servicio no registró ningún corte |
| E3 | Rampas con corte: «**se nota el hueco**». El servicio registró los 6 cortes |
| E4 | Retardo con fundido de 80 ms (5–20 ms por parlante). Repetido, con aviso previo: en el instante del cambio, «**nada, solo el cambio de color**». No hubo barrido, golpe de graves ni clic. Entre los dos ajustes estables, el usuario sí oyó la diferencia esperada de la mezcla en audífonos («más apagado, más amplio, más agudo, más completo») |
| E5 | Retardo con fundido de 200 ms: «tampoco se nota el momento del cambio» |
| E6 | Retardo con corte: «**se nota el hueco**» |

**Veredicto de la etapa 1, oído:** el fundido funciona en las rampas y en los retardos. El largo de 80 ms y
el fundido de retardo a igual potencia no dejan ningún rastro audible en el cambio. Las dos preguntas que la
spec dejó para la escucha, el filtro peine y los +3 dB en graves, quedan respondidas: no se oyen.

**El usuario pidió** que la perilla del tiempo de transición vaya **de 0 a 500 ms, con 80 por defecto**
(antes iba de 10 a 500; primero pidió hasta 200 y después lo dejó en 500).

**Las entregas tardías del servicio real con la prioridad** (i-7c8794-246f79):
- El hilo del motor obtuvo -15 por RealtimeKit (`state.health.engine_priority`).
- En los 20 minutos de escucha no hubo ninguna entrega tardía. El registro de cortes solo tuvo las
  transiciones en modo `cut` y un hueco de 68 ms en la entrada, que vino de la aplicación de música.
- Pero esto pasó **sin carga**, así que no compara nada. Medir el servicio bajo carga, con y sin la
  prioridad, sigue pendiente.

## 6. Preferencia del usuario entre presets, con audífonos (2026-10-09, `HP-O16`)

No es una prueba ciega ni una medición de calidad: el usuario elige qué le gusta, con los WH-CH520, por el
monitor en `mix`, con los 4 parlantes virtuales y la curva de EQ de prueba (+6 dB en graves y +5 en agudos).
La sonda es `probes/24-ab-monitor/ab.py` (`serie-preparar` y `serie 8`): 14 presets numerados, 8 s cada uno,
que el usuario clasificó sin saber qué era cada uno. Todo es **REPORTADO** por el usuario. **La primera
vuelta se corrió**: el usuario contaba los cambios por el hueco y avisó que sus opiniones pudieron
desplazarse. La segunda vuelta, con 1 s de silencio antes de cada preset y 10 s de escucha, es la que vale
(§6.1).

Datos: `datos/23/preferencia-presets-registro.jsonl`, con el estado del servicio cada segundo de 14:16 a
15:10, durante las vueltas de §6 a §6.4.

| # | Qué era | Opinión |
|---|---|---|
| 1 | `front` como estaba (difusión `noise_tail`, decorrelación, ambiente 0,7, EQ de prueba, graves `protect`) | referencia |
| 2 | `direct` (estéreo puro) | bueno, fuerte y semicompleto |
| 3 | `front` sin difusión | normal, lejano y semicompleto |
| 4 | `front` con difusión fuerte (-6 dB, rt60 1,2 s) | igual al anterior, pero menos completo |
| 5 | `front` con ambiente 0,3 | lejano, no le gusta |
| 6 | `front` con ambiente 1,0 | igual al anterior |
| 7 | `front` sin decorrelación | ecos raros, pero suena bien |
| 8 | `front` sin EQ | mucho eco, demasiado bajo |
| 9 | `front` con armónicos de graves en 0 dB | apagado pero bueno; le gustaría más completo |
| 10 | `spatial` | igual al anterior |
| 11 | `spatial` sin difusión | más lejano y difuso, no le gusta |
| 12 | `front` seco: sin difusión, sin decorrelación, ambiente 0,4 | **fuerte y completo, le gusta mucho** |
| 13 | `direct` sin EQ | muy lejano, se pierde demasiado |
| 14 | `classic` cercano (sin difusión, ambiente 0,4) | sin opinión |

Antes, en el torneo de a dos, opinó de `classic`: «envolvente pero muy lejano» (3, regular). Además, el
mismo preset sonó distinto la segunda vez («apagado y suave» frente a «referencia»). Durante una carga, el
makeup del monitor subió de -0,8 a +0,6 dB en 20 s; es una causa posible (**INFERIDO**).

**Qué dice (REPORTADO):**
- Al usuario le molesta lo «lejano». Prefiere un sonido seco, cercano y lleno: el 12, y después el 2.
- Con estos audífonos, la curva de EQ de prueba le ayuda.

El 12 quedó guardado como `mi-escucha`.

### 6.1 Segunda vuelta, alineada (10 s cada uno, 1 s de silencio antes; REPORTADO)

Los mismos 14 presets. El número 1 sonó sin silencio previo, porque el usuario lo contó como «lo que estaba
sonando».

| # | Qué era | Opinión |
|---|---|---|
| 1 | `front` como estaba | normal, no tan bueno |
| 2 | `direct` | normal, claro y semicompleto |
| 3 | `front` sin difusión | normal, menos graves |
| 4 | `front` con difusión fuerte | bueno, completo y fuerte |
| 5 | `front` con ambiente 0,3 | bueno, completo, menos fuerte |
| 6 | `front` con ambiente 1,0 | bueno, completo, algo de eco |
| 7 | `front` sin decorrelación | **muy bueno, completo** |
| 8 | `front` sin EQ | lo mismo |
| 9 | `front` con armónicos de graves en 0 dB | «demasiado bajo», pero le gusta la idea si todo el espectro fuera así |
| 10 | `spatial` | balanceado, demasiado eco detrás |
| 11 | `spatial` sin difusión | normal, no nota diferencia |
| 12 | `front` seco | bueno, completo |
| 13 | `direct` sin EQ | bueno, pero el eco es raro, como que resuena |
| 14 | `classic` cercano | malo, demasiado lejano y apagado |

**Qué dice:**
- El preferido es el 7: `front` sin decorrelación, con difusión y ambiente 0,7. La difusión y el ambiente
  suman sensación de «completo».
- Lo lejano (`classic`) y el eco detrás (`spatial`) no le gustan.
- Que el EQ de prueba no cambie nada (8 igual a 7) contradice la primera vuelta y hay que comprobarlo.

El 7 quedó como `mi-escucha`.

### 6.2 Tercera vuelta: los mejores, repetidos y mezclados en orden revuelto (REPORTADO)

| # | Qué era | Opinión |
|---|---|---|
| 1 | 7 | bueno, completo; quiere menos eco |
| 2 | 7 + difusión fuerte (-6 dB, rt60 1,2 s) | bueno; cree que tiene menos eco |
| 3 | 12 (seco) | nota más los graves; le gusta, pero sin exagerar |
| 4 | 4 (`front` con difusión fuerte) | bueno, le gusta |
| 5 | 7 + ambiente 1,0 | demasiado efecto, se oye un ruido raro |
| 6 | **7, repetido** | demasiado amplificadas algunas frecuencias |
| 7 | 5 (`front` con ambiente 0,3) | completo, más balanceado, pero todavía no |
| 8 | 7 + ambiente 0,3 | eco medio, está bueno: guardarlo, pero no como el principal (`eco-medio`) |
| 9 | **4, repetido** | completo y con eco, algo menos lejano |
| 10 | 7 + difusión fuerte + ambiente 1,0 | le gusta, está completo |

**Qué dice:**
- La opinión sobre el 7 **no se repitió**: «bueno» la primera vez, «frecuencias amplificadas» la segunda.
- La del 4 sí se repitió: positiva las dos veces.
- La difusión fuerte gustó en todas sus formas (2, 4, 9 y 10), y el usuario la percibe con menos eco.
- El ambiente al máximo sin la difusión fuerte trae un «ruido raro». Una causa posible son los artefactos
  del extractor de ambiente al máximo (**INFERIDO**).
- El usuario pidió comparar los finalistas con `direct`.

### 6.3 Final contra `direct` y desempate (REPORTADO), y por qué el procesado no gana en audífonos

**Final contra `direct`** (8 números):
- El 12 dio «bueno, más balanceado, me gusta mucho».
- `direct` sonó «demasiado amplificado» las dos veces.
- El 10 dio «bueno» las dos veces.
- El 4 sonó «lejano» las dos veces.

**Desempate entre el 12, el 10 y `direct`** (13 números):
- El orden cubrió cada par ordenado dos veces. El usuario comparó cada número con el anterior: «mejor»,
  «peor» o «igual», y una descripción. **Sus juicios son relativos**, no absolutos (lo advirtió él).
- Dio 14 opiniones para 13 números. La lectura con corrimiento, contando lo que sonaba antes como 1, es la
  única coherente: `direct` sale «amplificado» en 3 de 4, y «harto fondo y ambiente» cae en el 10, el único
  con difusión fuerte y ambiente al máximo. **INFERIDO.**
- Resultado con esa lectura:
  - el 10 fue positivo en 3 de 4 («balanceado» en 3);
  - `direct` sonó «amplificado» en 3 de 4, y el usuario lo llamó «secundario» o «modo alternativo»;
  - el 12 sonó «apagado» en 4 de 5, aunque antes había gustado.

**Por qué el procesado no gana claramente a `direct` con audífonos:**
- **El destino del procesado no son los audífonos** (VERIFICADO por diseño). El reparto en 4 parlantes, con
  ambiente, Haas, decorrelación y difusión, busca el envolvimiento con parlantes en una pieza
  (research/09). El monitor `mix` suma esos 4 canales en 2, y esa suma de copias retardadas y filtradas
  produce un filtro peine. **INFERIDO**: eso explica «frecuencias amplificadas», «eco de fondo» y «lejano».
- **La mezcla original ya está pensada para estéreo y audífonos.**
- **La serie no emparejó el volumen** (se cargaban presets; no se usó el A/B con `match_loudness`). El
  emparejamiento por render puede haber dejado `direct` más fuerte. **INFERIDO**: explicaría
  «amplificado».

**Conclusión:** esta prueba elige cómo escuchar en audífonos. No juzga si el procesado cumple su objetivo, lo
que se mide con los JBL en una pieza. Para audífonos, la idea es un monitor binaural (HRTF), anotado en el
roadmap.

### 6.4 A/B ciego 10 contra `direct` con `match_loudness`: un defecto del emparejamiento de volumen (MEDIDO / VERIFICADO)

Se hicieron 2 intentos ABX (A = el 10, B = `direct`; 12 s cada uno).

| Intento | Respuesta del usuario | X era | Preferencia |
|---|---|---|---|
| 1 | X «apagado»; le gusta el otro, se anotó X = B | A (error) | A |
| 2 | X = B | A (error) | B |

**El emparejamiento de volumen del A/B no mide bien:**
- La compensación pasó de **-1,86 dB a A** en el intento 1 a **-5,17 dB a B** en el intento 2, un minuto
  después. **MEDIDO** en `state.ab.compensation_db`.
- La causa: `Service._ab_measure` toma la sonoridad de corto plazo **de la salida**
  (`meter.outputs_short_term`) mientras suena A y la compara con la de la salida mientras suena B. **No resta
  la sonoridad de la entrada**, así que mide sobre todo cuán fuerte venía la música en ese tramo, no la
  diferencia entre los presets. **VERIFICADO** en el código. `render_match` sí mide la ganancia neta (salida
  menos entrada).
- Hay además una pista: en el intento 1, el 10 sonaba **más fuerte** que `direct` (se le bajaron 1,86 dB). El
  «amplificado» de `direct` no venía entonces del volumen. **INFERIDO**, de un solo intento.

El A/B se detuvo y el arreglo quedó en el roadmap, antes de la etapa 2. Los presets quedaron así:
- `principal` y `amplio`: el 10;
- `alternativo`: `direct`;
- `seco`: el 12;
- `eco-medio`: el 7 con ambiente 0,3.

Todos usan la curva de EQ de prueba, que el usuario prefirió a no tener EQ (§6.1), así que la curva **queda
puesta**: no se restaura el respaldo del ensayo.


### 6.5 Lo que aprendimos para el A/B final (después de las etapas 2–4)

Lo pidió el usuario: «considera el feedback de esta etapa para las pruebas A/B del final».

1. **Avisar antes de cada tanda** y esperar la confirmación del usuario. Él necesita leer qué escuchar antes de que empiece.
2. **Un silencio claro de 1 s antes de cada número** (los parlantes en silencio con fundido) y **10 s de
   escucha**. Con 5 s y sin marca, el usuario perdió la cuenta y la primera vuelta se corrió.
3. **Lo que suena antes de empezar el usuario lo cuenta como el 1.** O se carga el 1 sin silencio, o se le
   avisa que el 1 empieza después del primer silencio.
4. **Los juicios son relativos al anterior.** El orden se arma para que cada par ordenado salga al menos dos
   veces, con repetidos escondidos. Solo vale lo que se repite.
5. **Siempre `direct` como control dentro del motor.** Sacar la música por fuera del motor se delata por el
   retardo (lo propuso el usuario).
6. **El volumen emparejado de verdad** (i-7c8794-50caa5): sin ese arreglo, `match_loudness` sigue la música
   y no los presets.
7. **No revelar qué era cada número hasta el final.** Y cuando la respuesta es ambigua (por ejemplo «me
   gusta el que no es este apagado»), preguntar en vez de interpretar.
8. **Con audífonos se juzga cómo escuchar en audífonos, no el envolvimiento** (§6.3). El A/B que decide sobre
   el procesado se hace con los JBL.
9. **Desde la etapa 2** las diferencias de EQ, decorrelador y extractor entre presets se funden en vez de
   cortar, así que el A/B final puede compararlas sin el hueco (el render sigue cortando hasta la etapa 3).

### 6.6 Preferencia después de escuchar más rato (REPORTADO, 2026-10-09, ~15:20)

El usuario borró varios presets y se quedó con, en orden: **1. `alternativo` (`direct`), «por lejos»; 2.
`principal` (el 10)**. Con audífonos, después de escuchar más rato, gana la mezcla original sin procesar.
Calza con §6.3: el procesado busca envolvimiento con parlantes y, sumado en dos canales, colorea el sonido.


## 7. El monitor se calla después de «Calibrar»: el micrófono de los propios audífonos (2026-10-09, `HP-O16`)

**Síntoma (REPORTADO por el usuario, tres veces).** El monitor (modo `mix`, destino
`bluez_output.14_06_A7_6B_E3_F0.1`, los WH-CH520) se quedaba mudo y solo volvía al pasar el modo a `stereo` y
de vuelta a `mix`. Unos 20 s antes, cada vez, el registro del servicio tenía `mic_check` («micrófono: abierto
para medir su nivel, 8 s»): entrar a la vista Calibrar pide abrir el micrófono.

**Causa, en dos partes.**

1. **El micrófono elegido era el de los propios audífonos** (VERIFICADO): `microphone` en `service.json` es
   `bluez_input.14:06:A7:6B:E3:F0`, que WirePlumber lista aun mientras suenan en A2DP (`pactl list sources
   short`: id 2073, con el sink A2DP 11643 sonando). Abrirlo pasa los audífonos al perfil de manos libres
   (HFP), y PipeWire rehace su sink con el mismo nombre y otro id: de 2075 a 11643 (VERIFICADO con `pactl list
   sinks/sources short`). El diario de WirePlumber muestra el ida y vuelta: «Failure in Bluetooth audio
   transport …/sep2/fd0» (A2DP) a las 15:14:36, 15:16:54 y 15:18:08, y el de HFP (`fd45`) al cerrar (VERIFICADO,
   `journalctl --user -u wireplumber`).
2. **El monitor no se enteraba** (VERIFICADO en el código). Su `pw-play` pide `node.dont-reconnect = true`
   (para que WirePlumber nunca lo mueva a un parlante, experimentos/09). Con eso, cuando su destino desaparece,
   WirePlumber 0.5.18 **destruye el stream** en vez de esperar (`/usr/share/wireplumber/scripts/linking/
   prepare-link.lua`: «if the stream has dont-reconnect … destroy it instead», `node:request_destroy()`). El
   servicio leía dónde quedó el monitor una sola vez, al abrir (`MonitorController._opened`), y nunca más:
   `routed_to` y `reached: true` quedaban congelados; `Writer.failed` (la tubería rota) se marcaba y nadie lo
   leía. Solo un `monitor_set` lo volvía a abrir, que es lo que hacía el cambio de modo. Si `pw-play` sale al
   perder su nodo o queda vivo sin enlace no se pudo comprobar sin tocar PipeWire (INFERIDO: sale); el arreglo
   cubre los dos casos.

**Reproducción (VERIFICADO con un test, sin PipeWire).** `test_a_target_that_vanishes_and_comes_back_reattaches_the_monitor`
(`host/tests/test_monitor_service.py`): con el destino fuera de los sinks observados, el estado seguía
diciendo `reached: true` (falló así antes del arreglo).

**Arreglo.**

- **El monitor se vuelve a enganchar solo.** En cada observación del sistema (~3 s) el servicio llama
  `MonitorController.watch`. Actúa al tiro si su `pw-play` salió o rompió la tubería, si el destino no está entre
  los sinks, si su id cambió (los sinks del observador ahora llevan `id`) o si la apertura misma no llegó al
  destino; y si dos `pw-dump` seguidos leídos después de abrir (los del observador, sin leer el grafo otra vez)
  muestran el stream sin enlace o enlazado a otro sink (el peligro de experimentos/09). Entonces `reached` pasa a
  `false` al instante y se vuelve a abrir y a verificar como en cualquier apertura (a lo más una vez cada 5 s).
  El cierre de la salida vieja va al hilo de trabajo del monitor, nunca al del motor: cerrar espera al
  escritor y a `pw-play` hasta segundos (revisión del 2026-10-09). Mientras el destino no está, el monitor queda `waiting` con el porqué, y
  el panel dice «desapareció de PipeWire; el monitor vuelve solo cuando reaparezca».
- **El micrófono de una salida en uso nunca se abre** (decisión del usuario, 2026-10-09). El micrófono
  Bluetooth de un equipo que es el destino del monitor o un parlante de la instalación (se compara por
  dirección Bluetooth, en sus dos escrituras) no lo abre `mic_check`, ni una calibración, ni el lazo, tampoco
  cuando el lazo vuelve solo (`AudioSession.microphone_guard`). El servicio responde `unavailable` con el
  porqué («es el micrófono de WH-CH520, que es la salida del monitor: abrirlo pasa sus audífonos a manos libres
  y corta el sonido; elegí otro micrófono»), `state.microphones[].blocked_reason` lo marca, el selector lo muestra
  deshabilitado y la vista Calibrar muestra el porqué en vez de abrirlo. El micrófono configurado no se cambia.

**Pendiente** (i-7c8794-6c2a37). Comprobar en `HP-O16`, con el servicio reiniciado, que entrar a Calibrar ya no corta el monitor,
y que un cambio de perfil provocado de otra forma (desconectar y reconectar los audífonos) lo trae de vuelta
solo, con el número de segundos que tarda (MEDIDO pendiente).

### 7.1 Comprobado en `HP-O16` (VERIFICADO, 2026-10-09, 16:00)

El servicio corría con el arreglo, en modo `mix` hacia los WH-CH520. El usuario los desconectó y los
volvió a conectar desde el Bluetooth del sistema.

**Qué pasó:**
- El journal de WirePlumber registra a las 16:00:31 que se cortó el transporte A2DP (`sep2/fd0`) y que el
  sink pasó a error.
- El servicio anotó `monitor: lost bluez_output.14_06_A7_6B_E3_F0.1 (gone)`.
- **El monitor volvió solo**: el usuario no tocó el modo. Al terminar, el estado era `on`, conectado a los
  audífonos (`reached: true`), sin caídas ni rellenos.
- **Spotify se pausó al desconectarse los audífonos.** Eso lo hace el escritorio (el reproductor
  reacciona al Bluetooth) y no aurasync, porque Spotify estaba enviando su audio al sink `aurasync`.

**Qué no quedó medido:**
- **Cuánto tardó en volver el sonido.** La línea `lost` sale del logger del monitor sin hora y no llega al
  registro del panel. Queda como menor: darle hora y llevarla al registro del panel.
- **La regla del micrófono**: el panel muestra el del WH-CH520 bloqueado, con su motivo, y al entrar a
  Calibrar ya no se abre. Eso se ve en el estado (`microphones[].blocked_reason`); no se probó entrando a
  Calibrar.


## 8. Etapa 4 de transiciones sin corte: el colchón por estiramiento (2026-10-09)

Construida el 2026-10-09 (spec `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` §4b,
plan `docs/superpowers/plans/2026-10-09-seamless-transitions-stage-4.md`): un colchón que se vacía ya no
recibe silencio, la salida suena un poco más lenta (`dsp/stretch.py`, uno para los parlantes y otro para el
monitor). Las perillas son `transition.start_stretch_ppm` (1000) y `max_stretch_ppm` (5000); en 0 vuelve el
relleno con silencio de antes.

### 8.1 Calidad del estiramiento, fuera de línea (MEDIDO, 2026-10-09, `HP-O16`, sin audio en vivo)

Un tono puro de 0,5 de amplitud pasa por el estirador con `ε` constante, 30 bloques de 4096 después de la
rampa; la frecuencia sale de la pendiente de la fase de la señal analítica y el THD+N es lo que queda al
quitar el mejor seno a esa frecuencia. numpy (la lectura es la misma en Rust, dentro de 1e-9).

| `ε` (ppm) | 100 Hz | 440 Hz | 1 kHz | 5 kHz | 10 kHz |
|---|---|---|---|---|---|
| +1000 | −112,6 dB | −100,0 dB | −94,5 dB | −96,3 dB | −90,7 dB |
| +5000 | −112,6 dB | −100,0 dB | −94,5 dB | −96,3 dB | −90,7 dB |
| −1000 | −112,6 dB | −100,0 dB | −94,5 dB | −96,3 dB | −90,7 dB |

El error relativo de la frecuencia contra `f/(1+ε)` quedó bajo 1,2e-10 en todos. **Una primera medición
dio −83 dB a 1 kHz y −65 dB a 100 Hz, y era el estimador, no el estirador:** medía la frecuencia con
trozos de 4800 muestras que no contenían ciclos enteros, erraba en 1e-6 y el ajuste a esa frecuencia
dejaba un residuo que crecía con la duración. Cambiar cómo se mide (la fase de la señal analítica) movió
el resultado 30 dB a 100 Hz; eso delató al estimador (CLAUDE.md, "lo que se mide no sobrevive a cambiar un
parámetro que no debería importar"). El techo que queda lo pone el núcleo de la lectura (Kaiser de 32
coeficientes, β = 8): su error en el peor punto fraccionario es −85 dB a 1 kHz y −82 dB a 10 kHz (MEDIDO
con la respuesta del núcleo), y al recorrer todas las fracciones el promedio da lo de la tabla.

**Costo** (MEDIDO, `HP-O16`, una lectura de 4096 posiciones que se mueven): 3,9 ms en numpy y 0,54 ms en
Rust por canal y bloque, solo mientras se estira; con `ε = 0` el bloque pasa sin copia. La revisión
(2026-10-09) midió que los pesos de la lectura son 2,45 de los 3,49 ms por canal: ahora se calculan una
vez para todo el grupo, y 4 parlantes en numpy bajaron de 14,0 a 4,2 ms por bloque (MEDIDO, mismo equipo;
la salida es la misma cuenta que `read_numpy`, comprobado contra la lectura por canal dentro de 1e-12).

**El lazo de control, revisado (2026-10-09).** La revisión no encontró oscilación: la confirmación toma
`LOW_BLOCKS` lecturas, la tolerancia es un cuantum (lo que salta la lectura) y la línea de relleno (el
objetivo) está a dos bloques de la de drenaje. Dos salvedades, INFERIDO (sin medir con parlantes):
1. Una sola lectura saltona en el objetivo o por encima aterriza el estiramiento antes: un relleno puede
   quedar cerca de medio cuantum corto del objetivo. La consecuencia es solo más episodios, más cortos;
   no es una falla.
2. La línea de drenaje de los parlantes (objetivo + `BACKLOG_BLOCKS`·bloque) queda apenas medio bloque
   sobre el nivel estable INFERIDO de una tubería al ritmo de la escritura (≈ objetivo + 1,5 bloques).
   Sería inofensivo (drenajes chicos), pero no está verificado con parlantes reales.

**Lo que la revisión corrigió** (2026-10-09). Con la tubería que miente (`LyingPipe`), después de que el
colchón se rindiera el estirador quedaba activo 2995 de 3000 bloques, fijo en 5000 ppm: un corrimiento
permanente de 8,6 cents. Ahora el estiramiento cede al último recurso: con un corte pendiente, después de
rendirse el colchón, y cuando la tubería lleva `LOW_BLOCKS` lecturas bajo un cuantum. Además tiene su
propio freno: un episodio que mueve más de un objetivo de muestras sin que la tubería suba aterriza y no se
pide más en la sesión. Con la misma tubería que miente, el estirador ya no arranca.

### 8.2 Escucha (pendiente, protocolo escrito antes de escuchar)

**Pendiente, en `HP-O16` y con permiso del usuario** (toca la sesión en uso). Con el monitor encendido:
1. Un relleno por estiramiento: el panel muestra `stretched_frames` y `stretch_ppm` del monitor
   (`state.monitor`) y de los parlantes (`health.output_cushion`). Se anota cuántos frames se estiraron en
   10 min y si el usuario oyó algo (el tono no debería moverse de forma audible: 1,7 a 8,6 cents).
2. El mismo tramo con `start_stretch_ppm = 0` (el relleno con silencio de antes): cuántos `refills` y si se
   oyen.
3. Una nota sostenida (piano o voz) mientras `stretch_ppm` está en su máximo, para la pregunta de si 0,5 %
   se nota.

Veredicto pendiente: hasta esta escucha, la etapa 4 queda "A medias" en el roadmap (i-7c8794-1b74ad).

## 9. Etapa 3 de transiciones sin corte: el render (2026-10-10)

Construida el 2026-10-10 en un contenedor (spec `2026-10-08-seamless-transitions-design.md`, "Stage 3 as
built"; roadmap i-7c8794-da4172). Cambiar de render con `transition = crossfade` arma una rama entera nueva
(upmix, decorrelador, difusión, graves, líneas de retardo y ecualización), la calienta a la sombra hasta 1 s
y la mezcla por parlante con la que se va, antes de la ganancia.

### 9.1 La forma del fundido, fuera de línea (MEDIDO, 2026-10-10, contenedor)

**Entorno:** contenedor de la nube (Linux 6.18.44 x86_64, python 3.12.3, numpy 2.5.3), motor numpy, sin
audio en vivo. **Método:** los tests de `host/tests/test_motor_render_crossfade.py`, con 3 parlantes
(uno trasero con ambiente 0,9 y Haas de 15 ms), todos los efectos prendidos (cola difusa, graves
`protect`, ecualización con curva) y ruido parcialmente correlado entre L y R. Por ventana de 10 ms, cuánto
baja el fundido bajo el nivel estable más bajo de los dos renders, y cuánto sube sobre el más alto; el
peor parlante de cada par (dB):

| par | `equal_gain` baja | `equal_power` baja | `equal_gain` sube | `equal_power` sube |
|---|---|---|---|---|
| classic ↔ front | 1,93 / 2,03 | 0,00 / 0,02 | 0 | 0,09 / 0,17 |
| front ↔ spatial | 0,00 | 0,00 | 0 | **2,02 / 2,03** |
| classic ↔ direct | 2,99 / 2,92 | 0,24 / 0,29 | 0 | 0,06 / 0,09 |
| direct ↔ spatial | 2,95 / 2,99 | 0,40 / 0,41 | 0 | 0,15 / 0,20 |

**Qué dice:** por parlante, dos renders se parecen poco (otro pan, el decorrelador, el Haas), y a igual
ganancia el medio del fundido baja hasta 3 dB; `spatial` y `front` son el mismo upmix y salen casi iguales,
y a igual potencia el medio sube 2 dB. Por eso la forma va por par (`motor.forma_render`): `equal_gain` entre
`spatial` y `front`, `equal_power` en el resto. Con esa regla, ningún par baja más de 0,41 dB ni sube más de
0,20 dB. Es una señal de prueba, no música: la escucha decide.

### 9.2 Escucha (pendiente, protocolo escrito antes de escuchar)

**Pendiente, en `HP-O16` y con permiso del usuario.** Con el monitor en modo `mix`, música elegida por el
usuario, `fade_ms` en 80 y después en 200:
1. classic → front → spatial → direct → classic, un cambio cada 15 s, anotando la hora de cada uno. Se pregunta
   si se oye un hueco, un salto de volumen o un "doble" (las dos versiones a la vez) en algún cambio.
2. El mismo recorrido con `transition = cut`, para comparar.
3. Cuánto tarda en oírse el render nuevo: el calentamiento puede llegar a 1 s.

Veredicto pendiente: hasta esta escucha, la etapa 3 queda "A medias" en el roadmap (i-7c8794-da4172).

