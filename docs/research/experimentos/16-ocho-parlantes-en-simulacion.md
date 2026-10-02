# 16 · Ocho parlantes en simulación: qué se rompe y qué escala

**Pregunta:** si `aurasync` pasa de 3 a 8 parlantes, ¿qué se rompe y qué escala? Se miran el costo
del motor, el tope de 6 del decorrelador, la calibración con un micrófono, el lazo y la sonda
enmascarada, los roles, el transporte y el panel. Roadmap i-7c8794-1f35a1. La meta de 8 la fijó el
usuario para probar los límites del diseño (d-7c8794-3b7793).

**Respuesta corta (SIMULADO, salvo §6, que es investigación):**

- **Hoy se rompe en tres puntos con 7 parlantes o más**, y en un cuarto con 6:
  1. **El servicio no arranca.** `_default_motor` construye el motor con `decorrelar=True`, y
     `Motor` lanza `ValueError` con 7 parlantes o más. Lo comprobó el probe con el código de hoy.
  2. **La ganancia que mide la calibración falla en silencio.** Es un error del estimador de
     nivel que aparece cuando las llegadas se separan más de 15 ms del desfase grueso. Se ve más
     con más parlantes, pero **también puede pasar con 3** (§3).
  3. **El lazo con la sonda por turnos no converge** desde 6 parlantes con ventanas de 4 s. Con
     22 ppm de deriva, la confirmación nunca coincide (§4).
  4. **Los roles cubren 4 lugares y el mapa del panel tiene 6 posiciones fijas** (§5, §7).
- **Escala bien:**
  - el retardo de la calibración: ≤ 0,006 ms de error con 8 a la vez;
  - la sonda enmascarada con 8 sondas simultáneas;
  - la correlación de banda ancha del banco de filtros: con 8 sigue debajo de 0,6;
  - PipeWire y `combine-stream`: 64 o 128 canales.
- **El costo del motor crece casi lineal:** 8 parlantes cuestan 2,3 a 2,5 veces lo que cuestan 3.
- **El tope de 6** no aparece en la correlación de banda ancha. Aparece **por octava, debajo de
  ~2 kHz**: con 8 filtros, algún par queda casi coherente (0,90 a 0,99) en alguna octava. Ni filtros
  más largos ni más candidatos lo arreglan. Lo que más pesa en lo que suena es **a qué parlante va
  cada filtro** (§2.4).

**Marcas:** SIMULADO, para todo lo que sale de los probes (retardos y ganancias conocidos, sin
parlantes ni micrófono; el motor y los estimadores sí son los reales). VERIFICADO, REPORTADO o
INFERIDO, para la investigación de §6. **Nada de este documento es MEDIDO con parlantes.**

**Entorno:**
- **Equipo:** el Mac (`Fabians-MacBook-Neo`, Apple A18 Pro, arm64), Python 3.12.13 y numpy 2.5.3
  del entorno de hatch de `host/`.
- **Código:** `aurasync` del árbol de trabajo del 2026-10-02, con los cambios sin commit de esa
  fecha.
- **Carga:** **muy alta, de 9 a 31**, por otras sesiones que corrían en paralelo. Afecta a los
  tiempos absolutos de §1 (ver ahí), no a los resultados de §2 a §4. Cada JSON anota la carga con
  que terminó.
- **Fecha:** 2026-10-02.

**Qué se ejecutó** (`probes/19-ocho-parlantes/`, su README tiene el detalle). Cada script escribe
su JSON en [`datos/16/`](datos/16/):

```bash
cd host
hatch run python ../probes/19-ocho-parlantes/costo.py
hatch run python ../probes/19-ocho-parlantes/decorrelador.py
hatch run python ../probes/19-ocho-parlantes/decorrelador_bandas.py
hatch run python ../probes/19-ocho-parlantes/decorrelador_bandas.py asignacion
hatch run python ../probes/19-ocho-parlantes/calibracion.py 6
hatch run python ../probes/19-ocho-parlantes/lazo.py 8
```

**Los probes no tocan `host/`.** El tope de 6 se levanta solo dentro del proceso
(`comun.lifted_limit`). Los arreglos que se proponen están prototipados en el probe: el estimador
de nivel (`calibracion.levels_fixed`) y el seguimiento de deriva (`lazo.track(feedforward=True)`).

**La instalación de N parlantes** (`comun.installation`): un anillo con N parlantes a ángulos
iguales. El pan y el ambiente salen del ángulo con una fórmula que reproduce los roles de hoy
(§5). Con N = 4 da exactamente `quad`.

---

## 1. Costo del motor. SIMULADO

**Cómo se midió:**
- Bloques de 4096 muestras (85,3 ms), 120 bloques después de 10 de calentamiento.
- Un motor por cada N de 3 a 8, alimentados **intercalados** bloque a bloque y en orden al azar,
  para que la carga del equipo pegue igual a todos.
- Se cuenta el **tiempo de CPU del hilo** (`time.thread_time`), no el de reloj. Con carga 10–30,
  el reloj daba pendientes de 1,8 a 6,4 ms por parlante para la misma configuración en dos
  corridas. Con tiempo de CPU, el cociente entre N = 8 y N = 3 repite dentro de 3 % entre
  corridas; los valores absolutos varían hasta 25 %.
- Dos corridas con semillas distintas.
- Configuraciones:
  - "por defecto": la cadena de siempre, con EQ;
  - "todo encendido": como en `probes/18`, con difusión, `protect` + armónicos o `crossover` a un
    Charge 6, pico real y presupuesto de EQ;
  - cada una con el decorrelador (forzado para 7 y 8) y sin él.

| Configuración | N = 3 | N = 6 | N = 8 | N8 / N3 | ms por parlante (ajuste) |
|---|---|---|---|---|---|
| por defecto, con decorrelador | 5,2 / 6,6 ms | 9,1 / 11,3 | 12,0 / 15,0 (p99 16,3 / 19,0) | **2,28 / 2,28** | 1,37 / 1,64 |
| todo encendido, `protect` | 10,4 / 11,4 | 20,4 / 21,3 | 26,5 / 28,0 (p99 33,9 / 35,2) | **2,54 / 2,47** | 3,13 / 3,39 |
| todo encendido, `crossover` | 9,8 / 9,3 | 18,0 / 16,7 | 23,1 / 21,2 (p99 30,5 / 28,7) | **2,35 / 2,29** | 2,62 / 2,46 |

Mediana del tiempo de CPU por bloque, en las corridas A / B (datos completos en `costo.json`).

**Qué se lee:**

- **El cociente entre 8 y 3 se repite** (2,28 y 2,28; 2,54 y 2,47). Los números absolutos no: con
  esta carga, el hilo corre a ratos en los núcleos de eficiencia, y aun el tiempo de CPU sale entre
  2 y 2,7 veces más alto que en `probes/18` (INFERIDO como causa).
- **Con números absolutos de un equipo descargado** (INFERIDO): se toman los de `probes/18` en el
  mismo Mac (por defecto 32–38× el tiempo real; todo encendido 19–22×) y se dividen por el
  cociente medido. **Con 8 parlantes quedarían ~14–17× por defecto y ~8–9× con todo encendido.**
  - La spec 2026-10-02 §5 pide ≥ 20× con todo encendido. Con 3 el Mac ya estaba justo (19–22×);
    con el costo lineal medido, **deja de llegar desde 4 parlantes, y con 8 queda en ~8–9×**.
    PC-Ryzen5 tiene que medirse.
  - Incluso con esta carga, el peor p99 (35 ms) queda a menos de la mitad del bloque (85 ms):
    **el motor sigue en tiempo real con 8**.
- **El decorrelador cuesta poco en el motor** (la diferencia con y sin él se pierde en la
  variación). Aparte, por parlante y por bloque:

  | Largo | `np.convolve` (lo que hace el motor) | FFT (solapar y sumar) |
  |---|---|---|
  | 256 | 0,29 ms | 0,14–0,16 ms |
  | 512 | 0,54 ms | 0,13 ms |
  | 1024 | 0,94–0,97 ms | 0,13–0,14 ms |

  Con 8 parlantes y 1024 coeficientes en convolución directa serían ~7,6 ms por bloque. Por FFT,
  ~1,1 ms. **Si alguna vez se alargan los filtros, la convolución tiene que pasar a FFT**, o
  fundirse con la EQ como propone research/13 §3.3.
- **Lo que sí escala mal en CPU es la calibración** (§3): `medicion.calibrar` pasa de ~1 s con 3
  parlantes a 1,4–3 s con 8 sobre 10 s de grabación, y a 3–4,7 s sobre 20 s. Ya corre en un proceso
  aparte (`sincronia.MedicionEnSegundoPlano`). En la Zero 2 W (25–40× más lenta, research/13) un
  lazo cada 20 s **no alcanzaría** con 8 (INFERIDO).

## 2. El tope de 6 del decorrelador. SIMULADO

### 2.1 De dónde sale el 6

Potard y Burnett (DAFx'04, research/09 §11.1, VERIFICADO en el paper): con filtros fijos de unos
**100 polos y ceros**, "solo 5 o 6" señales quedan totalmente decorrelacionadas. Más allá, el largo
finito hace que algún par termine correlacionado.

**Es un número de su diseño y de su criterio, no una ley.** El nuestro es otro filtro: FIR de 256
coeficientes con retardo de grupo aleatorio y suave, de 2,5 ± 1,5 ms. **El mecanismo del límite sí
se traslada**, y este experimento lo encuentra donde corresponde (INFERIDO de §2.2 y §2.3):
- Debajo de ~1 kHz, el retardo de grupo de cada filtro varía poco con la frecuencia, así que entre
  dos filtros la diferencia de fase dentro de una octava es casi un retardo puro.
- Con 8 filtros metidos en una variación de 3 ms, algún par queda separado por menos de ~0,4 ms.
  En las octavas de 250 Hz a 1 kHz, eso deja la fase casi alineada (o en contrafase).
- En la correlación de banda ancha eso se diluye; **por octava, no**.

### 2.2 Qué pasa con 7 y 8: bancos y métricas

**Cómo se calculó:** correlación a retardo cero de ruido rosa (el mismo ruido, totalmente
correlacionado, a la entrada de todos los filtros), con la ponderación de
`decorrelate.correlacion_rosa`. Se informa el **peor par**:
- de banda ancha;
- ≥ 500 Hz (el criterio orientativo: < 0,6);
- por octava;
- con desfase de hasta ±1 ms (≥ 500 Hz).

También la **planitud**: el apartamiento máximo por tercio de octava entre 50 Hz y 16 kHz (criterio
±0,5 dB). Dos semillas de selección por banco. **Comprobado con señales:** el banco actual con 8, y
los de 1024 coeficientes, pasados por ruido rosa de 10 s con dos semillas, dan las mismas cifras
que el cálculo analítico (±0,01).

| Banco, N = 8 | peor par, banda ancha | ≥ 500 Hz | por octava 250 / 500 / 1k / 2k / 4k | ±1 ms | planitud |
|---|---|---|---|---|---|
| **actual con N = 3** (referencia) | 0,20 / 0,23 | 0,32 / 0,27 | 0,79 / 0,89 / 0,58 / 0,53 / 0,53 | 0,46 | 0,17 / 0,06 dB |
| actual (256, 64 candidatos) forzado a 8 | 0,38 / 0,41 | 0,34 / 0,33 | 0,997 / 0,90 / 0,985 / 0,76 / 0,53 | 0,52 / 0,47 | 0,17 / 0,25 dB |
| 512 coeficientes | 0,42 / 0,42 | 0,34 / 0,29 | 0,94 / 0,97 / 0,99 / 0,72 / 0,74 | 0,44 / 0,52 | 0,43 dB |
| 1024 coeficientes | 0,47 / 0,43 | 0,32 / 0,37 | 0,996 / 0,98 / 0,89 / 0,90 / 0,65 | 0,64 / 0,52 | **0,92** / 0,68 dB |
| 1024, variación 2 ms | 0,40 / 0,41 | 0,29 / 0,31 | 0,95 / 0,93 / 0,88 / 0,81 / 0,63 | 0,44 / 0,51 | **2,0** / 0,75 dB |
| 1024 candidatos | 0,36 / 0,35 | 0,32 / 0,38 | 0,96 / 0,97 / 0,93 / 0,84 / 0,86 | 0,54 / 0,47 | 0,18 / 0,25 dB |
| minimax del peor par (256, pool 512) | 0,32 / 0,34 | 0,31 / 0,27 | 0,995 / 0,91 / 0,95 / 0,98 / 0,62 | 0,53 / 0,50 | 0,18 / 0,23 dB |
| **minimax por octava** (256, pool 512) | 0,49 / 0,50 | 0,24 / 0,31 | **0,83 / 0,78 / 0,79 / 0,78 / 0,66** | 0,52 / 0,48 | 0,29 / 0,28 dB |
| velvet noise 30 ms, 1000/s | **0,14 / 0,12** | 0,12 / 0,13 | 0,74 / 0,62 / 0,37 / 0,29 / 0,28 | 0,22 / 0,28 | **5,7 / 5,5 dB** |
| velvet noise 20 ms, 2000/s | 0,14 / 0,15 | 0,14 / 0,15 | 0,83 / 0,80 / 0,44 / 0,41 / 0,31 | 0,30 / 0,31 | **6,2 / 6,1 dB** |

Cada celda da la semilla 0 / la semilla 1; las columnas por octava son de la semilla 0. Todas las
filas, con N = 3 a 8, están en `decorrelador.json` y `decorrelador_bandas.json`.

**El banco actual con 10 semillas** (mediana y máximo del peor par):

| N | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|
| banda ancha | 0,20 · 0,24 | 0,26 · 0,33 | 0,33 · 0,36 | 0,35 · 0,39 | 0,39 · 0,42 | 0,41 · 0,43 |
| ≥ 500 Hz | 0,29 · 0,44 | 0,30 · 0,44 | 0,31 · 0,44 | 0,32 · 0,44 | 0,32 · 0,44 | 0,33 · 0,44 |

**Qué se lee:**

- **Con el criterio de banda ancha (< 0,6 sobre 500 Hz), el banco actual ya alcanza con 8:** su
  peor par es 0,33–0,44. Ese criterio se vuelve un poco peor con cada parlante, sin ningún escalón
  en 6.
- **Por octava, el 6 sí aparece.** El peor par de cada octava ya es alto con 3 (0,79–0,89 debajo
  de 1 kHz). Con 7–8, algún par llega a **0,95–0,997 en 250 Hz–1 kHz**: dos parlantes que en esa
  octava reciben casi lo mismo. Esa coherencia de un par en una banda es la que el oído usa para
  fundir dos fuentes en una (IACC por banda crítica; INFERIDO para parlantes en una pieza, donde la
  sala y la distancia decorrelacionan más).
- **Alargar los filtros no ayuda, y además colorea.** Con 1024 coeficientes, la planitud pasa de
  ±0,5 dB (0,92 dB en una semilla; 2,0 dB con 2 ms de variación). Más candidatos tampoco ayuda.
- **Elegir el banco por el peor par por octava** baja el peor par a ~0,8 en todas las octavas de
  250 Hz a 2 kHz, sin perder planitud. A cambio, el de banda ancha sube a ~0,5, todavía debajo de
  0,6. Es la mejor opción de fase pura que se probó.
- **Velvet noise decorrelaciona mucho mejor** (0,12–0,15 de banda ancha con 8), pero **colorea
  ±5,5–6,2 dB por tercio**, aun eligiendo el cuarto más plano de 512 candidatos. Es el mismo
  defecto que el oído del usuario ya rechazó en el decorrelador anterior (±9,5 dB, experimentos/10
  §6). **Se descarta** salvo que se compense con la EQ de cada parlante, y eso no se probó.

### 2.3 Lo que reciben los parlantes con el motor real

`decorrelador_bandas.py` pasa música sintética (la de `probes/13`, dos materiales) por el motor real
con los roles del anillo. Peor par entre los 8, ≥ 500 Hz y de banda ancha:

| | N = 3 | N = 4 (`quad`) | N = 8, decorrelador forzado | N = 8, sin decorrelador |
|---|---|---|---|---|
| banda ancha | 0,26 / 0,27 | 0,39 / 0,42 | **0,61–0,66 / 0,62** | 0,94 / 0,87 |
| ≥ 500 Hz | 0,32 / 0,16 | 0,29 / 0,33 | **0,65 / 0,45** | 0,91 / 0,83 |
| octava de 500 Hz | 0,57 / 0,46 | 0,44 / 0,61 | 0,73 / 0,69 | 0,93 / 0,87 |

**Con 8 parlantes, lo que suena está más correlacionado que lo que dice el banco.** Los vecinos del
anillo reciben mezclas parecidas: en el octágono, −67,5° y −112,5° tienen el mismo pan (−0,92) y
ambientes de 0,24 y 0,46, y el par de adelante (±22,5°) tiene pan ±0,38 con el mismo ambiente. Sobre
500 Hz, el peor par es de vecinos en los dos materiales. Con uno de los dos materiales, **el criterio
de < 0,6 sobre 500 Hz no se cumple (0,65)**; con el otro sí (0,45).

### 2.4 A qué parlante va cada filtro

Hoy el filtro k del banco va al parlante k de la instalación. Se probaron 24 asignaciones del mismo
banco de 8 filtros. Con el material 0, el peor par ≥ 500 Hz va de **0,34 a 0,69** según la
asignación. **Pesa tanto como el banco.**

La mejor asignación con el material 0 (0,343, contra 0,646 la de hoy) se comprobó con el material 1:
da 0,429, contra 0,445 la de hoy. **La mejora se repite pero chica**, así que una asignación elegida
con un solo material está en parte ajustada a ese material. La propuesta (§8) la elige con
**ruido rosa a través de las mezclas de los roles**, no con una música.

## 3. Calibración con N fuentes y un micrófono. SIMULADO

**Cómo se midió:**
- **Estímulo y estimador reales:** `estimulos.calibracion` (ruido rosa independiente por
  parlante, amplitud 0,1, como el servicio) y `medicion.calibrar`.
- **Sala simulada:**
  - retardos al azar de 0 a 30 ms (experimentos/10 midió diferencias de 5–40 ms entre parlantes)
    y ganancias de −8 a 0 dB;
  - la coloración del Go 4 (`simulated.ROOM_COLOUR_DB`);
  - una cola reverberante distinta por parlante (0,4 s, como `probes/13`);
  - ruido de micrófono de 0,001 (`simulated.ROOM_NOISE`) y de 0,004.
- **Repeticiones:** 6 salas por condición; 10 s y 20 s; dos semillas del estímulo (la 0 es la del
  producto), cada una con sus propias salas.
- **El error de ganancia se mide contra lo que el mismo estimador da con cada parlante sonando
  solo**, sin ruido y sin los demás. La razón: con la cola reverberante, las primeras reflexiones de
  cada parlante interfieren con su sonido directo, y el "nivel" de cada parlante en el micrófono
  deja de ser su ganancia desnuda. Contra la ganancia desnuda, el estimador se aparta 1–3 dB ya con
  3 parlantes (`gain_err_vs_bare_gain`), y eso es de la sala, no de N. Lo que se busca aquí es
  **lo que agrega N**.
- **Criterio:** error de retardo > 0,5 ms o error de ganancia > 1 dB.

### 3.1 El error que encontró: el estimador de nivel busca en el lugar equivocado

**Fallas con ganancias de 2,7 a 6,6 dB de error**, informadas como confiables
(`Calibracion.confiable` verdadero). No mejoraban con 20 s ni con menos ruido de micrófono: las dos
condiciones de ruido dieron cifras idénticas. Eso apuntaba a algo sistemático y no a la relación
señal-ruido. **La causa, comprobada:**

- `medicion.calibrar` le pasa a `niveles` el retardo de cada parlante **relativo al que llega
  primero** (`para_nivel = v − origen`).
- Pero `niveles` busca el pico en las referencias **corridas por el desfase grueso**, que es la
  **mediana** de las llegadas.
- Entonces cada pista se equivoca en (primera llegada − mediana).
- Cuando eso pasa los ±15 ms de búsqueda (`BUSQUEDA_MS`), la ventana no encuentra el pico del
  parlante y su nivel sale de la diafonía.

Con las pistas corregidas, el error de esas mismas salas baja de 2,4–6,6 dB a 0,3–0,8 dB:

| N, sala | error con `calibrar` | error con la pista corregida | primera llegada − desfase grueso |
|---|---|---|---|
| 7, sala 0 | 4,91 dB | 0,79 dB | −18,6 ms |
| 7, sala 3 | 6,63 dB | 0,61 dB | −15,4 ms |
| 8, sala 2 | 3,79 dB | 0,33 dB | −18,4 ms |
| 8, sala 3 | 2,38 dB | 0,67 dB | −16,3 ms |
| 4, sala 0 | 2,75 dB | 0,63 dB | −19,4 ms |
| 4, salas 1–3 | 0,08–0,29 dB | igual | −6 a −11 ms |

**No es un problema de 8 parlantes.** Con más parlantes la mediana queda más lejos del primero, y
por eso pasa más seguido. Pero con 3 parlantes alcanza con que el del medio llegue más de 15 ms
después del primero. Diferencias de 5–40 ms entre parlantes ya se midieron (experimentos/10 §5.3).
**Las ganancias de las calibraciones reales con diferencias así quedan en duda** hasta revisarlas
(INFERIDO: no se repasaron los JSON de experimentos/10).

**El arreglo prototipado** (`calibracion.levels_fixed`): medir el residuo del primer parlante
contra la alineación gruesa con un GCC-PHAT sobre toda la grabación, y sumarlo a todas las pistas.
Alternativa más simple: que `BUSQUEDA_MS` cubra la dispersión de las llegadas. Es un cambio de
`medicion.py` (§8, prioridad 1).

**Corregido en el producto el mismo día (2026-10-02).** `medicion.calibrar` mide ahora una vez,
con GCC-PHAT sobre toda la grabación, el residuo absoluto del parlante más temprano contra las
referencias alineadas, y se lo suma a las pistas relativas (la misma idea que `levels_fixed`).
`tests/test_medicion.py::test_calibrar_iguala_bien_aunque_los_parlantes_lleguen_a_destiempo` tiene
tres casos con el primero a 20–30 ms de la mediana: fallaban con 3,0 a 4,4 dB de error y ahora
pasan (< 0,5 dB), y vuelven a fallar si se quita la corrección. **Las calibraciones reales del
experimento 10** tenían el más temprano a ~11,6 ms de la mediana (retardos de 13,56, 11,59 y 0 ms),
por debajo de los 15 ms del umbral: probablemente no se vieron afectadas (INFERIDO), aunque
estaban cerca del borde.

### 3.2 Exactitud frente a N

Peor error sobre los N parlantes, en las 6 salas de cada condición, semilla 0 / semilla 1, con ruido
de micrófono 0,001. Con 0,004 (12 dB más) las cifras son las mismas dentro de ±0,03 dB y ±0,001 ms:
**el ruido del micrófono no es lo que limita**, la diafonía entre parlantes sí. "Fallas" cuenta las
salas que pasan 0,5 ms o 1 dB; todas las fallas se informaron como confiables.

| N | retardo, 10 s | ganancia hoy, 10 s: mediana · máx · fallas | ganancia con la pista corregida, 10 s | con la pista corregida, 20 s | CPU de `calibrar`, 10 s |
|---|---|---|---|---|---|
| 3 | 0,003 / 0,005 ms | 0,19 / 0,22 · 0,28 / 0,37 · 0 / 0 | igual (0 fallas) | 0,14 / 0,26 · 0,21 / 0,36 · 0 / 0 | 0,6–1,1 s |
| 4 | 0,003 / 0,004 | 0,19 / 0,58 · 2,75 / 3,67 · **1 / 2** | 0,19 / 0,24 · 0,63 / 0,69 · 0 / 0 | 0,19 / 0,20 · 0,40 / 0,71 · 0 / 0 | 0,7–1,3 s |
| 5 | 0,004 / 0,005 | 0,45 / 0,58 · 4,66 / 0,95 · **1 / 0** | 0,33 / 0,58 · 0,67 / 0,95 · 0 / 0 | 0,16 / 0,32 · 0,39 / 0,57 · 0 / 0 | 1,0–3,3 s |
| 6 | 0,005 / 0,004 | 1,96 / 0,55 · 4,99 / 0,90 · **3 / 0** | 0,41 / 0,55 · 0,81 / 0,90 · 0 / 0 | 0,42 / 0,25 · 0,45 / 0,81 · 0 / 0 | 1,1–2,5 s |
| 7 | 0,004 / 0,003 | 1,08 / 0,63 · 6,63 / 4,76 · **3 / 2** | 0,82 / 0,40 · 1,27 / 0,70 · **1 / 0** | 0,48 / 0,36 · 0,76 / 0,46 · 0 / 0 | 1,2–2,6 s |
| 8 | 0,004 / 0,005 | 1,56 / 2,61 · 3,79 / 6,25 · **3 / 5** | 0,60 / 1,29 · 0,74 / 1,50 · **0 / 4** | 0,40 / 0,75 · 0,61 / 1,14 · **0 / 2** | 1,4–3,0 s |

**Qué se lee:**

- **El retardo escala sin problema:** ≤ 0,006 ms con 8 fuentes a la vez, con 10 o 20 s, en las dos
  semillas y los dos ruidos. Ni una falla de retardo en 288 calibraciones. La ganancia de proceso
  de 8 kHz × 0,25–2 s alcanza de sobra para la interferencia de 7 fuentes más.
- **La ganancia es lo que se degrada con N**, aun con la pista corregida. El peor error pasa de
  0,3–0,4 dB con 3 a 0,7–1,5 dB con 8 en 10 s. **Desde N = 7 empieza a pasar 1 dB** (1 de 12 salas
  con 7; 4 de 12 con 8). Es diafonía: la normalización por la autocorrelación de cada ruido se
  ensucia con las otras N − 1 referencias, que en graves y en 20 ms todavía se parecen.
- **20 s ayudan pero no alcanzan con 8:** 2 de 12 salas siguen pasando 1 dB (máx. 1,14).
- **Las fallas de hoy dependen de la sala, no de N:** la semilla 1 con 6 parlantes no tiene
  ninguna, y la 0 tiene 3. Es el error de la pista (§3.1), que aparece cuando la primera llegada
  queda a más de 15 ms de la mediana.
- **El costo de CPU crece lineal** (1 s con 3 → 1,4–3 s con 8 sobre 10 s; 3–4,7 s sobre 20 s).

### 3.3 ¿Calibrar de a grupos?

Para N = 8: dos grupos que comparten el parlante 0 (0–4 y 0 + 5–7), cada uno con su propia
grabación, juntados por el parlante ancla. Mismas 6 salas que la fila N = 8 de §3.2; el nivel con la
pista corregida en las dos variantes.

| N = 8 | tiempo total | retardo, peor | ganancia: mediana · máx · fallas |
|---|---|---|---|
| los 8 a la vez | 10 s | 0,004 / 0,005 ms | 0,60 / 1,29 · 0,74 / 1,50 · 0 / 4 |
| los 8 a la vez | 20 s | 0,004 / 0,004 ms | 0,40 / 0,75 · 0,61 / 1,14 · 0 / 2 |
| 2 grupos × 5 s | 10 s | 0,005 / 0,005 ms | 0,85 / 0,64 · 1,22 / 1,03 · 1 / 2 |
| **2 grupos × 10 s** | 20 s | 0,004 / 0,005 ms | **0,63 / 0,47 · 0,72 / 0,59 · 0 / 0** |

**Con el mismo tiempo total, dos grupos de 4–5 son mejores que los 8 juntos** (20 s: 0 fallas contra
2), y repite en las dos semillas. A igual tiempo corto (10 s) no: cada grupo tiene la mitad del
tiempo y se pierde lo ganado. **Propuesta:** con más de 6 parlantes, calibrar en dos grupos de
10 s con un parlante ancla. El retardo no lo necesita: es solo por la ganancia.

## 4. El lazo y la sonda enmascarada con 8. SIMULADO

### 4.1 La sonda con 8 parlantes

Es la sonda de experimentos/11 (`probes/13`) sobre el motor real con N = 3 y 8: música sintética,
la sonda moldeada por tercio bajo la música de cada parlante, y la sala de `probes/13` con retardos
de 2–30 ms. Ocho ensayos por condición y dos semillas. Error del retardo medido: mediana · p95 ·
máximo · fracción > 1 ms (n = mediciones).

| N | ventana | margen | por turnos (1 sonda por ventana) | simultánea (N sondas por ventana) |
|---|---|---|---|---|
| 3 | 4 s | −20 dB | 0,002 · 0,004–0,005 · 0,005 · 0 | 0,002 · 0,005–0,007 · 0,008 · 0 (n = 48) |
| 8 | 2 s | −20 dB | 0,003–0,004 · 0,009–0,015 · 0,016 · 0 | 0,006 · 0,013–0,016 · 0,023 · 0 (n = 128) |
| 8 | 4 s | −20 dB | 0,001–0,003 · 0,005–0,009 · 0,010 · 0 | **0,003 · 0,009–0,011 · 0,018 · 0** (n = 128) |
| 8 | 4 s | −25 dB | 0,003–0,005 · 0,008–0,013 · 0,014 · 0 | 0,005 · 0,013–0,015 · 0,023 · 0 (n = 128) |
| 8 | 2 s | −25 dB | 0,006–0,009 · 0,013–0,022 · 0,022 · 0 | 0,009 · 0,025–0,026 · **10,6 / 19,1 · 0,031 / 0,016** |

**Ocho sondas simultáneas miden igual que una.** Cada sonda ya compite con la música, que está
20 dB más fuerte. Las otras 7 sondas suman 7 veces la potencia de una, que frente a la música de
8 parlantes agrega menos de 0,1 dB de interferencia (INFERIDO, y es lo que muestra la tabla).

**El único caso que falla es 2 s a −25 dB, y falla en las dos semillas** (3,1 % y 1,6 % de
mediciones con más de 1 ms de error). Con 8 la música es más densa en el micrófono que con 3
(+4,3 dB de interferencia por sonda), y eso se come el margen. **Con 8 parlantes: −20 dB, o
ventanas de 4 s.**

### 4.2 Seguir una deriva de 22 ppm

`lazo.track` simula una hora:
- **Deriva:** offsets que derivan linealmente, cada parlante con una pendiente al azar dentro de
  ±22 ppm (experimentos/10 §5.3) o de ±50 ppm. El primero es el driver del sink combinado, con
  deriva 0.
- **Medición:** el error de la sonda (σ = 0,005 ms) y, en una variante, un 2 % de mediciones
  aberrantes de ±5 ms.
- **Corrección:** las reglas del lazo tal como están en `sincronia`, aplicadas por parlante: zona
  muerta de 0,5 ms, todo cambio confirmado por una segunda medición que coincida dentro de 0,5 ms,
  factor 0,5.
- **Resultado:** el desalineamiento es la dispersión entre parlantes, p95 después de los primeros
  10 minutos, semilla 0 / semilla 1, en ms.

| Esquema | entre dos mediciones del mismo parlante | N = 3 | N = 6 | N = 8 | N = 8 a 50 ppm |
|---|---|---|---|---|---|
| cadencia de hoy (todos cada 20 s, ventana de 10 s) | 20 s | 1,85 / 1,38 | 2,47 / 3,38 | 2,49 / 3,00 | **169 / 282** |
| sonda por turnos, ventana de 4 s | N × 4 s | 1,39 / 1,00 | **24,3** / 3,77 | **74,7 / 124** | **231 / 282** |
| por turnos, ventana de 2 s | N × 2 s | 1,17 / 0,87 | 1,60 / 1,83 | 1,98 / 2,06 | **169 / 282** |
| por turnos, 4 s, tolerancia de confirmación ensanchada por la deriva | N × 4 s | 1,39 / 1,00 | 2,69 / 3,65 | 3,45 / 4,13 | 8,4 / 10,1 |
| **simultánea, ventana de 4 s** | 4 s | 1,11 / 0,82 | 1,19 / 1,30 | **1,20 / 1,23** | **1,46 / 1,60** |
| cadencia de hoy **+ seguimiento de deriva** | 20 s | 0,65 / 0,50 | 0,57 / 0,72 | 0,88 / 0,88 | 3,4 / 4,9 |
| por turnos, 4 s, tolerancia ensanchada **+ seguimiento** | N × 4 s | 0,77 / 0,60 | 0,60 / 0,83 | 0,80 / 0,97 | 0,85 / 0,69 |
| **simultánea, 4 s + seguimiento** | 4 s | 0,55 / 0,63 | 0,56 / 0,77 | **0,50 / 0,73** | **0,66 / 0,78** |

Con 2 % de mediciones aberrantes, las filas que convergen cambian menos de 0,8 ms (`lazo.json`).

**Qué se lee:**

- **Por turnos, la sonda no alcanza desde 6 parlantes.** La causa no es la precisión: es que la
  confirmación nunca coincide.
  - Entre dos mediciones del mismo parlante pasan N × 4 s. En ese tiempo, un parlante a 22 ppm se
    corre 22 ppm × N × 4 s. Con N = 6 son 0,53 ms; con N = 8, 0,70 ms. Las dos cifras pasan los
    0,5 ms de tolerancia de la confirmación.
  - Entonces cada medición nueva difiere de la pendiente y la reemplaza, el lazo no aplica nada y
    el desalineamiento crece 1,3 ms por minuto.
  - **La condición es N × ventana × deriva ≤ 0,5 ms:** a 22 ppm, N × ventana ≤ 23 s; a 50 ppm,
    ≤ 10 s. **La cadencia de hoy (20 s) tampoco sobrevive 50 ppm**, con 3 parlantes o con 8.
- **La sonda simultánea escala.** Con 8 parlantes se mantiene en 1,2 ms a 22 ppm y en 1,5–1,6 ms
  a 50 ppm. Y §4.1 muestra que medir los 8 a la vez no cuesta precisión.
- **El piso de ~1 ms de todas las filas lo pone el propio lazo,** no la sonda. Entre medir,
  confirmar y aplicar la mitad, el parlante sigue derivando (el residuo de equilibrio es ~4 ×
  deriva × intervalo, INFERIDO).
  - Con un **seguimiento de deriva** (estimar la pendiente de cada parlante con sus últimas 8
    mediciones y mover su retardo a ese ritmo entre mediciones; `Controlador.deriva_ms_h` ya la
    estima), el desalineamiento baja a 0,5–0,9 ms con 8. A 50 ppm se sostiene en 0,7–0,9 ms si se
    mide seguido (la sonda simultánea, o por turnos con la tolerancia ensanchada). Con la cadencia
    de hoy queda en 3–5 ms. Ese piso de 0,5–0,9 ms ya es la zona muerta.
  - Es lo que haría un remuestreo por parlante.
- **Lo que no modela este probe** (INFERIDO):
  - El `Controlador` de hoy descarta la medición **entera** si un solo parlante no es estable. Si
    cada uno pasa con probabilidad p, los 8 pasan con p⁸: con p = 0,9 son 43 %, contra 73 % con 3.
    Con 8 hace falta aceptar mediciones por parlante.
  - El lazo de hoy correlaciona contra la música, no contra la sonda, y eso con roles parecidos ya
    falla con 3 (experimentos/11). Con 8 y vecinos de mezcla parecida (§2.3) falla más.

## 5. Layouts y roles para 5 a 8 parlantes. Propuesta, INFERIDO

Hoy `control.ROLES` tiene `quad` (FL, FR, RL, RR) y `lcrs` (FL, FC, FR, RC). `assign` acepta 6
roles, y dos parlantes no pueden compartir uno. Con A2DP cada parlante recibe una **mezcla**, no un
canal, así que un rol es un par (pan, ambiente).

**Una fórmula por ángulo** (θ: 0 = frente, positivo a la derecha) reproduce los roles de hoy, y con
eso los nuevos quedan coherentes con los viejos:

> pan = sen θ · ambiente = 0,35 − 0,2 · cos θ / cos 45°, acotado a [0,1; 0,6]

FL (−45°) da (−0,7; 0,15); RL (−135°) da (−0,7; 0,55); FC da (0; 0,1); RC da (0; 0,6) (hoy 0,55).
El retardo de Haas (`retardo_traseros_ms × ambiente`) sigue saliendo solo.

| Layout | Roles (ángulo → pan, ambiente) | Para qué |
|---|---|---|
| **5.0** (ITU) | FL −30° (−0,5; 0,11) · FC 0° (0; 0,1) · FR 30° (0,5; 0,11) · SL −110° (−0,94; 0,45) · SR 110° (0,94; 0,45) | 5 parlantes, con el frente que el material estéreo espera |
| **hex** | FL −30° · FR 30° · SL −90° (−1; 0,35) · SR 90° (1; 0,35) · RL −150° (−0,5; 0,59) · RR 150° (0,5; 0,59) | una pieza rectangular con 6, sin centro |
| **7.0** (7.1 sin LFE) | FL −30° · FC 0° · FR 30° · SL −90° · SR 90° · RL −150° · RR 150° | 7 parlantes; el Charge 6 puede ir de FC |
| **octágono** | ±22,5° (±0,38; 0,1) · ±67,5° (±0,92; 0,24) · ±112,5° (±0,92; 0,46) · ±157,5° (±0,38; 0,6) | 8 en los bordes, sin frente privilegiado: el envolvimiento puro |
| **dos anillos** | un `quad` en los bordes (los roles de hoy) + un `quad` interior a −0,5 de ambiente o al revés: el anillo de afuera con más ambiente (0,55–0,6) y el de adentro con más directo (0,1–0,15) | 8 en una pieza grande donde el oyente camina entre los dos anillos |

**Tres cuidados, que salen de este experimento y de research/09:**

1. **Vecinos con mezclas parecidas se correlacionan** (§2.3). En el octágono, −67,5°/−112,5° (y
   su espejo) tienen el mismo pan, y el par de adelante (±22,5°) comparte casi todo el centro. Ahí la asignación de filtros (§2.4) tiene que darles los
   más ortogonales. Una alternativa es alternar el ambiente entre vecinos: 0,1 / 0,3 / 0,1 / 0,3…
   en vez de una rampa suave.
2. **"Una densidad de fuentes demasiado alta produce una extensión percibida más angosta"**
   (Potard y Burnett, research/09 §11.1, REPORTADO por los autores). Con 8 parlantes conviene
   **separarlos hacia los bordes**, no apiñarlos. Y si no se logran 8 señales distintas, es mejor
   pocas señales bien decorrelacionadas en parlantes lejanos que 8 casi iguales.
3. **DBAP** (research/09 §4): el modelo de datos ya tiene `x`, `y` opcionales. Con 8 parlantes,
   los roles por ángulo son el paso intermedio. El paso siguiente es calcular pan y ambiente por la
   distancia a fuentes virtuales: L y R adelante, el ambiente difuso en todos. Cuantos más
   parlantes, más gana DBAP sobre "un rol por parlante", porque las posiciones reales de una pieza
   no son un anillo.

**Lo que habría que cambiar** (sin hacerlo aquí):
- `control.ROLES` y `assign.choices`: roles nuevos (SL, SR y los del octágono);
- `ROLE_POSITIONS` del panel (§7);
- `simulated.ROOM_DELAYS_MS`, que tiene 6 valores: con 8 parlantes, el 7.º y el 8.º repiten el
  retardo y la ganancia del 1.º y el 2.º.

## 6. Transporte para 8. Investigación (VERIFICADO / REPORTADO / INFERIDO)

Lo investigó un subagente el 2026-10-02, con fuentes primarias donde había. El detalle está en las
URLs.

### 6.1 A2DP con varios adaptadores

- **El techo por adaptador es de 2–3 parlantes.**
  - sendspin-bt-bridge recomienda 1–3 parlantes por adaptador, 2 adaptadores para 4–5 y "3+
    adapters, one per 2–3 speakers" para 6 o más. Su configuración de referencia es **2× CSR8510
    con 6 parlantes** (REPORTADO,
    [sendspin-bt-bridge](https://trudenboy.github.io/sendspin-bt-bridge/bluetooth-adapters/)).
  - El RTL8761B (UB500) "handles 2–3 speakers reliably" (REPORTADO, mismo sitio y
    [hilo de Home Assistant](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762?page=4)).
  - **Nadie reporta 8.**
- **Aire, por stream** (INFERIDO): SBC bitpool 53 (~328 kbps) ocupa ~26 % del aire con 2-DH5 y
  ~16 % con 3-DH5. Con retransmisiones y coexistencia, quedan 2–3 por adaptador; con bitpool 40
  (el de los Go 4), algo más de holgura.
- **Para 8: 3–4 adaptadores** (más el AX210, que comparte radio con el Wi-Fi).
  - Conviene alejar los dongles de los puertos USB 3 con alargadores USB 2: el ruido de USB 3 cae
    en 2,4–2,5 GHz (VERIFICADO,
    [Intel/USB-IF, *USB 3.0 Radio Frequency Interference*](https://www.usb.org/sites/default/files/327216.pdf)).
  - BlueZ y PipeWire no tienen un límite documentado de adaptadores (INFERIDO).
  - Antes de su v2.58, sendspin corría "silently against the default controller only" con varios
    controladores (REPORTADO, [music-assistant #5061](https://github.com/orgs/music-assistant/discussions/5061)).
    **Hay que verificar que cada stream salió por el adaptador pedido**, como pide CLAUDE.md para
    PipeWire.
- **El riesgo principal** son 8 enlaces en 2,4 GHz desde 3–4 piconets. Las retransmisiones y
  los cortes tienen que medirse con la vara de experimentos/12.

### 6.2 `combine-stream` con 8 salidas

- **Canales:**
  - `SPA_AUDIO_MAX_CHANNELS` es 64, y las posiciones van de `AUX0` a `AUX63` (VERIFICADO,
    [raw.h](https://raw.githubusercontent.com/PipeWire/pipewire/master/spa/include/spa/param/audio/raw.h)).
  - PipeWire 1.6.0 lo subió a 128 (VERIFICADO,
    [NEWS](https://raw.githubusercontent.com/PipeWire/pipewire/master/NEWS)).
  - Ocho canales están lejos de cualquier techo.
- **Salidas:**
  - El módulo no fija un máximo (VERIFICADO,
    [documentación](https://docs.pipewire.org/page_module_combine_stream.html)).
  - `combine-stream` pone `resample.disable=true` en sus streams. La deriva de cada parlante la
    corrige el propio sink bluez5 cuando sigue al driver (`rate_match`, VERIFICADO en
    [`media-sink.c`](https://raw.githubusercontent.com/PipeWire/pipewire/master/spa/plugins/bluez5/media-sink.c)).
  - El costo es un remuestreador por parlante, lineal y chico (INFERIDO).
- **Un matiz para §4:** lo que queda es la deriva residual entre sinks (los ~22 ppm de
  experimentos/10). **No se sabe si crece con 8 sinks en 3–4 adaptadores**; hay que medirlo.

### 6.3 Una Pico 2 W por parlante

- **El USB del RP2350 es full-speed** (12 Mbps, VERIFICADO,
  [datasheet](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf)).
  - Detrás de un hub USB 2.0 **single-TT**, todos los dispositivos FS comparten 1157 bytes
    periódicos por milisegundo (USB 2.0 §11.18.1, VERIFICADO).
  - Estéreo 48 kHz/16 bit con retorno asíncrono son ~217 B/ms: **caben 5, no 8**. Mono, ~119 B/ms:
    caben 8 (952 B, 82 %) (INFERIDO).
  - Con un hub **multi-TT**, cada puerto tiene su presupuesto y entran los 8 en estéreo (VERIFICADO,
    [Infineon AN1071](https://www.infineon.com/dgdl/Infineon-AN1071_Single_Versus_Multiple_Transaction_Translator-ApplicationNotes-v05_00-EN.pdf?fileId=8ac78c8c7cdc391c017d0d4c6b726c40)).
- **Hay reportes** de "Not enough bandwidth" con varias tarjetas de audio USB detrás de un hub en
  xHCI, y de 3–4 estables en un hub de 7 puertos (REPORTADO,
  [Red Hat #1411604](https://bugzilla.redhat.com/show_bug.cgi?id=1411604),
  [Launchpad #889953](https://bugs.launchpad.net/ubuntu/+source/linux/+bug/889953)).
- **Para 8:** 8 Picos (~US$7 cada una) y un hub multi-TT, o **un canal mono por Pico**, que es lo
  natural (cada parlante recibe una mezcla). El riesgo de research/13 no cambia: firmware propio y
  sin reportes de varios streams A2DP por CYW43439. Y siguen siendo 8 radios A2DP en el aire.

### 6.4 Auracast con un BIG de 8 BIS

- **La especificación permite hasta 31 BIS por BIG** (VERIFICADO,
  [Core 5.4, Link Layer](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-54/out/en/low-energy-controller/link-layer-specification.html)).
- **El aire** (INFERIDO, cálculo a la vista en el informe del subagente):
  - Cada transmisión de un PDU BIS en LE 2M ocupa (11 + carga) × 4 µs + 150 µs de T_MSS.
  - Con un intervalo ISO de 10 ms y 8 BIS:

  | SDU (preset BAP) | 1 transmisión | 2 | 3 | 5 (RTN 4, lo que pide BAP) |
  |---|---|---|---|---|
  | 60 B (24_2) | 3,5 ms | 6,9 ms | 10,4 ms | 17,4 ms |
  | 100 B (48_2) | 4,8 ms | **9,5 ms** | 14,3 ms | 23,8 ms |
  | 120 B (48_4) | 5,4 ms | 10,8 ms | 16,2 ms | 27,0 ms |

  - Los presets de BAP para broadcast llevan RTN 4 (VERIFICADO,
    [BAP](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/16212-BAP-html5/out/en/index-en.html),
    y [`bap_lc3_preset.h`](https://raw.githubusercontent.com/zephyrproject-rtos/zephyr/main/include/zephyr/bluetooth/audio/bap_lc3_preset.h)
    de Zephyr).
  - El controlador de Nordic reserva además ~2,5 ms por evento para el periodic advertising
    (VERIFICADO, `BT_CTLR_SDC_BIG_RESERVED_TIME_US`).
  - **Ocho BIS caben solo con 2 transmisiones por PDU a 24 kHz (6,9 ms)**, o con 48_2 sin margen
    (9,5 ms). Con la robustez de BAP (RTN 4) caben 2–3 BIS.
- **El controlador de Zephyr en el nRF52840** (`BT_LL_SW_SPLIT`):
  - `BT_CTLR_ADV_ISO_STREAM_MAX` va de 1 a 31, sin default declarado. Los samples lo fijan en
    **2**, y los bsim de audio en 4. `BT_BAP_BROADCAST_SRC_STREAM_COUNT` tiene default 1 y rango
    1–31 (VERIFICADO,
    [Kconfig del controlador](https://raw.githubusercontent.com/zephyrproject-rtos/zephyr/main/subsys/bluetooth/controller/Kconfig),
    [Kconfig.bap](https://raw.githubusercontent.com/zephyrproject-rtos/zephyr/main/subsys/bluetooth/audio/Kconfig.bap)).
  - El ISO de difusión de ese controlador está marcado **[EXPERIMENTAL]**. Lo probado
    públicamente es 2 BIS en un nRF52840. Con 4 BIS en un nRF5340 se perdían eventos (REPORTADO,
    [zephyr #45039](https://github.com/zephyrproject-rtos/zephyr/issues/45039),
    [#52055](https://github.com/zephyrproject-rtos/zephyr/issues/52055)).
  - **No hay reportes de un BIG de 8 BIS desde un nRF52840.**
  - En el nRF5340 Audio, un ingeniero de Nordic habla de un máximo de 4 BIS del controlador, y 6
    dieron "sin recursos" (REPORTADO,
    [DevZone 94823](https://devzone.nordicsemi.com/f/nordic-q-a/94823/nrf5340-audio---6-streams-broadcasting)).
- **Los Go 4 frente a un BIG de 8: no se sabe.**
  - Del lado del receptor, la especificación permite sincronizarse a un subconjunto de BIS: "The
    number of BISes requested may be less than the number of BISes in the BIG" (VERIFICADO,
    `HCI_LE_BIG_Create_Sync`, Core 6.1).
  - La BASE puede asignar un `Audio_Channel_Allocation` por BIS (VERIFICADO, BAP §3.7.2.2).
  - Los JBL solo reconocen un broadcast con su manufacturer data (REPORTADO, Bumble lo documenta
    "tested on the JBL GO 4", [auracast](https://google.github.io/bumble/apps_and_tools/auracast.html)).
  - **Nadie mostró que un JBL elija 1 BIS de un BIG multicanal de un tercero.** Es E4, y después E5.
- **La sincronía entre los BIS de un BIG** sale de un único `Presentation_Delay` en la BASE, desde
  un punto de referencia común (VERIFICADO, BAP §7.1 y Core Vol 6 Part G §3.2.2). La especificación
  no fija una tolerancia en µs: lo que queda es el firmware de cada receptor, y hay que medirlo.

### 6.5 Los tres caminos, con 8

| | Compra | Lo que se sabe que funciona | Riesgo que decide |
|---|---|---|---|
| A2DP, 3–4 adaptadores | 2–3 UB500 (~US$12–15 c/u) | 6 parlantes con 2 adaptadores (REPORTADO) | 8 enlaces en el aire; cortes y deriva por sink |
| 8 Picos | 8 × ~US$7 + hub multi-TT | 1 Pico → 1 parlante (pico-examples) | firmware propio; 8 radios A2DP igual |
| Auracast, 1 BIG de 8 BIS | ya están las SuperMini | 2 BIS desde un nRF52840 (REPORTADO) | **E4**: que el JBL elija su BIS; aire justo con 8 |

## 7. El panel con 8: lo que no escala

Leído en `panel/index.html` y `panel/app.js` del 2026-10-02. Es para quien lo construya; no se
cambió nada.

- **El mapa de la sala tiene 6 posiciones fijas** (`ROLE_POSITIONS`: FL, FR, RL, RR, FC, RC), y
  cada layout dibuja 4. Con 8 parlantes, 4 quedan en "personalizados: …" como texto. Hace falta
  que las posiciones vengan del layout (o de `x`, `y` del parlante, para DBAP).
- **La tabla de parlantes tiene 9 columnas** (parlante, estado, tipo, rol, pan, ambiente,
  volumen, retardo, acciones). En un teléfono no entran ni con 3. Con 8 filas hace falta:
  - una tarjeta por parlante plegable, o una vista "una perilla, todos los parlantes" (p. ej.
    ambiente de los 8 como 8 deslizadores cortos);
  - **acciones por grupo** (anillo de adelante y de atrás, izquierda y derecha): mover de a uno
    ocho parlantes no es usable.
- **Los controles rápidos** dan una tarjeta por parlante con ambiente y volumen: 8 tarjetas son
  varias pantallas de scroll en el teléfono.
- **Los medidores** son uno por parlante más el micrófono y la entrada: 10 barras. Conviene una
  grilla compacta con nombres cortos.
  - El SSE de medidores a 20 Hz crece lineal con N y no preocupa (INFERIDO).
  - Lo que sí: en la Zero 2 W, el costo de calcularlos se suma al del motor.
- **La línea de tiempo de cortes y las pistas de radio** son una pista por parlante. Con 8:
  - las pistas son 8 filas;
  - **colorear por parlante choca con el límite práctico de ~8 colores categóricos distinguibles**.
    Hace falta etiquetar por nombre o por grupo, no solo por color.
- **La tabla de calibración** (5 columnas) y la respuesta por parlante: 8 curvas en un mismo
  gráfico ya no se leen. Hace falta elegir 1–2, o pequeños múltiplos.
- **El A/B y los presets** no dependen de N.

## 8. Veredicto

### Qué se rompe con 8 (y desde cuántos)

| | Desde | Gravedad |
|---|---|---|
| el motor no se construye (`ValueError` en `Motor`, que el servicio llama con `decorrelar=True`) | 7 | bloquea: el servicio no arranca |
| la ganancia de la calibración (pista del estimador de nivel) | **cualquier N** con llegadas separadas > 15 ms de la mediana; más frecuente con N | falla **en silencio**, informada como confiable |
| el lazo con la sonda por turnos (ventana de 4 s) | 6 a 22 ppm; 3 a 50 ppm con la cadencia de hoy | el desalineamiento crece sin límite |
| la correlación de lo que suena entre vecinos (≥ 500 Hz) | 8 en el octágono | 0,45–0,65; el criterio de 0,6 se cumple con un material y no con otro |
| la ganancia de la calibración con la pista corregida (diafonía) | 7 (> 1 dB en 1 de 12 salas con 7, 4 de 12 con 8, a 10 s) | se arregla con 2 grupos de 10 s |
| la coherencia por octava debajo de 2 kHz | 7 | algún par a 0,95–0,99 (con 3: 0,79–0,89) |
| roles y mapa del panel | 5 | 4 roles por layout, 6 posiciones fijas |
| el presupuesto de 20× con todo encendido (spec 2026-10-02 §5) | 4 en el Mac (INFERIDO; con 3 ya estaba justo) | margen, no tiempo real |

### Qué escala

- **El retardo de la calibración:** error ≤ 0,006 ms con 8 fuentes a la vez, con 10 o 20 s y las
  dos semillas (§3.2).
- **La sonda enmascarada simultánea:** 8 sondas a −20 dB miden igual que una (p95 ≤ 0,011 ms).
- **El costo del motor**, lineal: 8 cuestan 2,3–2,5 veces lo que cuestan 3. Sigue en tiempo real.
- **PipeWire y `combine-stream`:** 64 o 128 canales, sin tope de salidas.

### Qué se propone al producto, por prioridad

1. **Corregir la pista del estimador de nivel** en `medicion.calibrar`. Que `niveles` reciba la
   posición de cada parlante relativa al desfase grueso, no al primero: es el arreglo prototipado
   en `calibracion.levels_fixed`. O que `BUSQUEDA_MS` cubra la dispersión de las llegadas. Un test
   con llegadas a 0, 20 y 35 ms lo destapa. **No es de 8: afecta ya a 3 parlantes.** Hay que
   revisar las ganancias de las calibraciones reales con diferencias de más de 15 ms.
2. **Con más de 6 parlantes, calibrar en dos grupos de 10 s con un parlante ancla** (§3.3): con 8,
   0 de 12 salas pasan 1 dB, contra 2 de 12 con los 8 juntos en 20 s. Solo hace falta por la
   ganancia; el retardo escala solo.
3. **Que 7 u 8 parlantes no tumben el servicio.** `MAXIMO_FIJOS` pasa de error a aviso: con 7–8
   el banco se arma igual y el panel avisa "decorrelación parcial con N parlantes". §2.2 muestra
   que el banco de hoy forzado a 8 sigue debajo de 0,6 de banda ancha; lo que se degrada es la
   coherencia por octava.
4. **Asignar los filtros por la mezcla, no por el orden.** Elegir qué filtro va a cada parlante
   minimizando el peor par de las salidas con ruido rosa a través de las mezclas de los roles (pan,
   ambiente). Es barato, se calcula al cambiar la instalación y no cambia ningún filtro. §2.4 mostró
   que la asignación mueve el peor par de 0,34 a 0,69.
5. **Elegir el banco por el peor par por octava** (250 Hz–2 kHz), además del de banda ancha. Con 8
   baja la coherencia por octava de 0,90–0,997 a ~0,8, sin perder planitud. **No alargar los
   filtros** (no ayuda y colorea). Si algún día se alargan, pasar la convolución a FFT.
6. **La sonda enmascarada con sondas simultáneas, no por turnos,** a −20 dB y ventanas de 4 s.
   Con turnos, el esquema tiene que cumplir N × ventana × deriva ≤ zona muerta. Y si la deriva
   resulta de 50 ppm, la cadencia de hoy (20 s) tampoco alcanza con 3.
7. **Seguimiento de deriva en el lazo:** mover el retardo de cada parlante al ritmo estimado entre
   mediciones (prototipo en `lazo.track(feedforward=True)`). Baja el desalineamiento de ~1,2 a
   ~0,5–0,9 ms con 8 y lo mantiene a 50 ppm. Y **aceptar mediciones por parlante**: hoy un solo
   parlante inestable descarta la medición de todos.
8. **Roles por ángulo** (`5.0`, `hex`, `7.0`, `octágono`, `dos anillos`, §5), con la fórmula que
   reproduce los de hoy, y el mapa del panel que dibuje las posiciones del layout. Después, DBAP con
   `x`, `y`.
9. **Panel:** acciones por grupo, una tarjeta por parlante plegable en el teléfono, medidores en
   grilla y pistas etiquetadas por nombre (§7).
10. **Transporte:** para pasar de 3, el primer paso barato sigue siendo research/13 §2.3 (a) con 4
   Go 4 en dos adaptadores (2 + 2), midiendo los cortes con la vara de experimentos/12. Para 8:
   3–4 adaptadores. Auracast con 8 BIS no se considera hasta que E4 diga que el JBL elige su BIS.

### Lo que este experimento no dice

- **Todo es simulado.** Las salas son lineales, sin códec, con una cola reverberante de ruido.
  Faltan las cosas que con 3 parlantes ya dieron sorpresas:
  - las reflexiones reales, que hacen que el nivel de cada parlante dependa de dónde está el
    micrófono;
  - el SBC con 8 enlaces;
  - la deriva entre 8 sinks en varios adaptadores.
- **El costo absoluto se midió con el Mac muy cargado.** Vale el cociente con N; los ×-tiempo-real
  para 8 son INFERIDOS a partir de `probes/18`.
- **Si la coherencia por octava se oye**, y si la asignación de filtros cambia lo que se oye, solo
  lo dice una escucha (el A/B ciego que ya existe).
- La música es sintética, como en experimentos/11.
