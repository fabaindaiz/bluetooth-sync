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
