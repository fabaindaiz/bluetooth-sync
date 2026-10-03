# 11 · La sonda enmascarada bajo la música, en simulación (paso 1)

**Pregunta:** ¿una señal de prueba moldeada por debajo de la música permite medir el retardo
de cada parlante mientras suena, cuando correlacionar contra la música misma falla?
¿A qué margen bajo la música y con qué largo de ventana? (i-7c8794-e3e40d, spec
`superpowers/specs/2026-10-01-music-first-and-masked-probe-design.md` §4, paso 1 del plan.)

**Respuesta (SIMULADO):**
- **Sí.** A −20 dB bajo la música, con ventanas de 4 s o más, el error queda bajo 0,012 ms en
  el 95 % de los casos, y **ninguna** medición de 240 se equivoca por más de 1 ms. Se cumple
  en las dos semillas, con decorrelador y sin él.
- **Correlacionar contra la música**, que es lo que hace hoy el lazo, se equivoca por más de
  1 ms en el **70 a 85 %** de los casos, aun con el decorrelador real. Casi siempre confunde a
  Black con Blue.

**Entorno:** simulación, sin parlantes ni micrófono. `PC-Ryzen5`, Python 3.12, motor real de
`aurasync` (extractor de ambiente y decorrelador). Fecha: 2026-10-02. Script:
`probes/13-sonda-enmascarada/simular.py`. Datos crudos: `datos/11/sonda-simulada.json`.

## Cómo se simuló

- **Música sintética estéreo:** acordes de 4 voces con 8 armónicos, bajo, bombo y hi-hat a
  120 negras por minuto, y una cola de reverberación distinta en cada canal. Es no
  estacionaria, con el espectro típico, y **llega correlacionada a los tres parlantes**, que
  es el caso que falla hoy. **No es música real**: no había archivos de música en el equipo.
- **Lo que recibe cada parlante sale del motor real**, con los roles FL, FR y RR (pan −0,7,
  0,7 y 0,7; ambiente 0,15, 0,15 y 0,55). Black y Blue comparten pan y se distinguen solo
  por el ambiente. Se probó con el decorrelador y sin él.
- **Sala:**
  - retardos de 3,13, 7,61 y 12,27 ms y ganancias de 1, 0,8 y 0,6;
  - una caída de agudos como la del Go 4 en el micrófono (−10 dB cerca de 10 kHz);
  - reverberación de 0,4 s y ruido de micrófono a −60 dB.
- **Sonda:** ruido distinto para cada parlante, entre 300 Hz y 8 kHz. Se moldea por tercio de
  octava y por cuadro (STFT de 1024 puntos, salto de 512) a `margen` dB por debajo de la
  música **de ese parlante**, y se apaga en el silencio (bajo −50 dBFS). En cada ventana la
  lleva un solo parlante, por turnos.
- **Estimadores:**
  - **sonda:** correlación entre el micrófono y la sonda enviada, con pesos PHAT solo dentro
    de la banda de la sonda y el pico interpolado;
  - **música:** `medicion.calibrar` contra lo que se mandó a cada parlante, como hace hoy el
    lazo.
- **Repeticiones:** 20 ensayos por condición, con música distinta en cada uno. Todo con dos
  semillas independientes (CLAUDE.md: un desfase verdadero se ve dos veces).

## Resultados. SIMULADO

Error absoluto del retardo del parlante medido, en ms. Cada celda muestra mediana · p95 ·
fracción por encima de 1 ms, primero con la semilla 0 y después con la semilla 1.

| Ventana | Decorrelador | Método | Semilla 0 | Semilla 1 |
|---|---|---|---|---|
| 2 s | sí | sonda −15 dB | 0,003 · 0,008 · 0 | 0,001 · 0,007 · 0 |
| 2 s | sí | sonda −20 dB | 0,004 · 0,011 · 0 | 0,002 · 0,012 · 0 |
| 2 s | sí | sonda −25 dB | 0,006 · 0,023 · 0 | 0,004 · 0,025 · 0 |
| 2 s | sí | sonda −30 dB | 0,010 · 0,043 · 0 | 0,008 · 0,038 · 0 |
| 2 s | sí | **música** | 4,64 · 4,66 · **0,85** | 4,66 · 4,67 · **0,85** |
| 2 s | no | sonda −30 dB | 0,008 · 0,034 · 0 | 0,009 · **11,83 · 0,20** |
| 2 s | no | **música** | 4,66 · 9,14 · **0,70** | 4,66 · 9,14 · **0,70** |
| 4 s | sí | sonda −20 dB | 0,002 · 0,006 · 0 | 0,002 · 0,007 · 0 |
| 4 s | sí | sonda −30 dB | 0,006 · 0,013 · 0 | 0,007 · 0,014 · 0 |
| 4 s | sí | **música** | 4,32 · 4,66 · **0,80** | 4,50 · 4,66 · **0,70** |
| 8 s | sí | sonda −20 dB | 0,002 · 0,005 · 0 | 0,002 · 0,003 · 0 |
| 8 s | sí | sonda −30 dB | 0,006 · 0,014 · 0 | 0,003 · 0,015 · 0 |
| 8 s | sí | **música** | 4,55 · 4,67 · **0,80** | 4,50 · 4,66 · **0,75** |

Las 60 condiciones completas están en el JSON. Las que no se muestran (−15 y −25 dB con
4 y 8 s, y todas sin decorrelador salvo las dos de la tabla) están todas por debajo de
0,025 ms de p95 y sin errores de más de 1 ms.

**Lo que dicen los números:**
- **El error de la música es de 4,6 ms, justo la diferencia entre Black y Blue**
  (12,27 − 7,61). El estimador confunde a los dos parlantes que comparten pan: su contenido
  es casi el mismo, y el decorrelador (que corre la fase hasta ±1,5 ms) no alcanza para
  separarlos. Es lo mismo que se vio con parlantes en el experimento 09 §5: el lazo no
  convergía y el parlante con más ambiente era el que peor se medía.
- **La sonda no depende de la música.** Es independiente de lo que suena, así que no hay
  nada que confundir. El error crece apenas al bajar el margen (de 0,003 a 0,010 ms de
  mediana entre −15 y −30 dB) y al acortar la ventana.
- **La única falla de la sonda es una advertencia útil:** a −30 dB, con 2 s y sin
  decorrelador, la semilla 1 tiene un 20 % de errores grandes y la semilla 0 ninguno. Con
  una sola semilla no se habría visto. A −30 dB hacen falta ventanas de 4 s o más.

## Los límites de simular

La sala simulada es lineal y sin codec, y el micrófono es perfecto salvo el ruido. Lo que la
simulación **no** dice:
- **El SBC:** cuantiza cada subbanda. Con música fuerte, una sonda a −20 dB puede quedar
  cerca del ruido de cuantización (INFERIDO; experimento 03 §3.2). Se mide con parlantes.
- **La deriva de reloj** durante la ventana (~22 ppm: 0,09 ms en 4 s) ensancha el pico. No
  alcanza para mover una medición de 1 ms, pero se suma.
- **Si se oye.** Esa es la pregunta del paso 4 del plan (A/B ciego), y ningún número de acá
  la responde.

## Veredicto

**El paso 1 da luz verde.** La sonda resuelve en simulación lo que la correlación contra la
música no puede: separar parlantes que tocan casi lo mismo. Para el paso 2 (construirla en
el motor) se parte de:
- **−20 dB y ventanas de 4 s**, con un parlante por turno;
- −25 dB como primer candidato para el A/B de inaudibilidad;
- −30 dB solo con 8 s.

Pasos siguientes del plan: 2, construirla en el motor; 3, con parlantes, un retraso inyectado
encontrado dos veces con semillas distintas; 4, A/B ciego de inaudibilidad.

## Paso 2: la sonda en el motor, SIMULADO

**Pregunta:** construida en el producto (el motor, el estimador, el lazo de la sesión), ¿la
sonda mide igual que en el paso 1, con 3 y con 8 parlantes? ¿El lazo que la usa mantiene a los
parlantes alineados durante una hora de deriva? Y, en el mismo cambio, dos pendientes de
medición del exp. 16: calibrar más de 6 parlantes en dos grupos con un ancla, y la coherencia
por tercio de la respuesta medida.

**Respuesta corta (SIMULADO; nada de esto es MEDIDO con parlantes):**
- **El estimador sobre el motor real mide como el paso 1:** p95 de 0,004–0,011 ms a −20 dB con
  ventanas de 4 s, con 3 y con 8 parlantes, con decorrelador y sin él. **Ninguna medición que el
  estimador dio por válida erró más de 1 ms** (de 646; rechazó 10). Las que fallan, las rechaza él mismo.
- **El lazo nuevo mantiene el desalineamiento bajo 0,5 ms (p95) durante una hora**, con 3 y 8
  parlantes, a 22 y 50 ppm, en las dos semillas: 0,25–0,40 ms. Sin seguir la deriva, el mismo
  lazo queda en 0,88–1,96 ms.
- **Se encontró por qué el lazo con parlantes "no convergía"** (exp. 09 §5): sumaba lo que
  medía en cada vuelta. Ver "El hallazgo" abajo.
- **Calibrar 8 parlantes en dos grupos de 10 s** con el código del producto: ganancia dentro de
  1 dB en las 24 salas (máx. 0,98 dB), con dos juegos de estímulos. **Pero con un solo
  parlante ancla, como proponía el exp. 16, no**: 2 de 12 salas pasaban 1 dB con los estímulos
  del producto. El 0 de 12 del exp. 16 no sobrevivió a cambiar la semilla del estímulo. Con
  tres parlantes compartidos, sí (§C).
- **La coherencia γ² por tercio y el error que de ella se deduce** salen en cada calibración; el
  error previsto coincide con la dispersión real entre ruidos independientes dentro de un
  factor 0,6–1,6.

**Entorno:**
- **Equipo:** el Mac (`Fabians-MacBook-Neo`, Apple A18 Pro, arm64), Python 3.12.13 y numpy
  2.5.3 del entorno de hatch de `host/`. Carga alta (12–17) por otras sesiones en paralelo; no afecta a
  los resultados, que no son de tiempo.
- **Código:** `aurasync` del árbol de trabajo del 2026-10-02, con los cambios sin commit de esa
  fecha.
- **Qué se ejecutó:** `cd host && hatch run python ../probes/13-sonda-enmascarada/paso2.py`
  (8 ensayos por condición) y `paso2.py C` (solo §C, después de pasar a tres compartidos);
  datos en [`datos/11/paso2.json`](datos/11/paso2.json). Y los
  tests `tests/test_probe.py`, `test_probe_measure.py`, `test_arrival_loop.py`,
  `test_session_probe.py`, `test_group_calibration.py` y `test_response.py`.
- **Fecha:** 2026-10-02.

### Qué se construyó

- **`dsp/probe.py`**: el ruido de cada parlante entre 300 Hz y 8 kHz, moldeado por tercio de
  octava y por cuadro (1024/512, raíz de Hann, como el paso 1) a `margin_db` bajo la señal de
  ese parlante; −20 dB por defecto, entre −40 y −10.
  - **Liberación** de 150 ms por cada 20 dB, y nada bajo −50 dBFS.
  - **Simultánea e independiente por parlante**, no por turnos: por turnos no converge desde 6
    parlantes (exp. 16 §4.2).
  - **En vez del "bloque de anticipación" de la spec, la sonda va 21 ms detrás de la música**
    que la moldeó. El fin es el mismo, que nunca suene antes de un ataque. Después de un final
    queda 21 ms más la liberación, dentro del enmascaramiento posterior de la nota que terminó
    (INFERIDO hasta el A/B ciego).
  - **Rampa de 50 ms** para prenderla y apagarla. **Apagada por defecto**
    (`SessionOptions.probe`).
- **El nivel está calibrado:** −20,0 ± 0,1 dB por tercio sobre ruido rosa estacionario sin la
  liberación, y −19,1 a −19,9 dB con ella (la liberación sostiene los cuadros más fuertes). La
  sonda del paso 1 medía −20,5 dB con la misma etiqueta: **los márgenes del paso 1 y del exp. 16
  valen para este módulo dentro de 1 dB**. Sin el factor 2 de la cuenta del módulo, la sonda
  quedaba en −23 dB: el test lo detecta.
- **En el motor** (`motor.sonda`), la sonda se suma a la entrada del limitador. Va después del
  retardo, de la ecualización, de la ganancia y del volumen, así que sigue el nivel de lo que
  suena. Se multiplica otra vez por la envolvente del corte, y el fondo de un corte sigue siendo
  silencio exacto. **Con la sonda en `None`, o apagada y quieta, el motor no la llama:** la
  salida es bit a bit la de siempre (`test_chain_golden.py`, más un test con la sonda puesta y
  nunca encendida).
- **`probe_measure.py`**, el estimador: GCC-PHAT solo dentro de la banda de la sonda, contra la
  sonda que salió, con el pico interpolado. Cada parlante se juzga solo, con tres condiciones:
  - las dos mitades de la ventana, que son dos mediciones independientes (otro tramo de sonda
    y de música), coinciden entre sí y con la ventana entera dentro de 0,25 ms: es la
    repetición, y además un cambio de largo de ventana;
  - la llegada queda a menos de 100 ms de la mediana de los demás (consenso, como
    `alineacion_gruesa`);
  - hubo sonda en las dos mitades.

  **El cociente pico/lóbulo que nombra la spec no se usa**, por lo mismo que se descartó la
  confianza (`medicion.CONFIANZA_MINIMA`).
- **`arrival_loop.py`**, el lazo nuevo:
  - acepta mediciones por parlante;
  - lleva cada medición a un marco común, por la mediana de lo que difiere de lo previsto;
  - cree un valor nuevo de un parlante si coincide con lo que predice su historia (0,5 ms), y
    corrige a un parlante solo después de dos mediciones que coinciden;
  - sigue un salto real si se repite, como un stream que se resincroniza;
  - estima la pendiente de cada parlante (Theil-Sen sobre las últimas 8) y mueve su retardo a
    ese ritmo entre mediciones;
  - pone la zona muerta sobre la **dispersión** de las llegadas y no sobre cada parlante: dos
    parlantes 0,4 ms corridos en sentidos opuestos están a 0,8 ms y una zona muerta por parlante
    no los tocaba;
  - **no corrige niveles** (ver el hallazgo).
- **`session.py`**:
  - con la sonda encendida y una ventana entera de ella ya oída, el lazo mide contra la sonda
    cada 4 s con ventanas de 4 s. Si no, mide contra la música como antes (10 s cada 20 s);
  - `set_probe(active, margin_db)` la cambia mientras suena;
  - el residuo que muestra "Sincronía" suma ahora el retardo con que sonó cada parlante.
- **La calibración**:
  - con más de 6 parlantes va en dos grupos que comparten los tres primeros
    (`group_calibration.py`), cada uno con la duración entera. Con 8 son 0–5 y 0–2 + 6–7; con
    7, 0–4 y 0–2 + 5–6; con 6 o menos, el estímulo de siempre, bit a bit. Los grupos se unen
    por la media, sobre los compartidos, de la diferencia de llegada y del cociente de nivel;
  - cada resultado trae `coherence` y `response_error_db` por tercio (`dsp/response.py`).

### El hallazgo: el lazo sumaba lo que medía. SIMULADO

Hasta ahora el lazo correlacionaba el micrófono contra lo que se le mandó a cada parlante.
Esa referencia la guarda `VentanaDeEmision`, y la guarda **después de la línea de retardo**.
Entonces lo que mide es la latencia propia de cada parlante, y **la corrección que el lazo
aplicó no aparece en la medición**.

`sincronia.Controlador.proponer` la trataba como el residuo que queda después de las
correcciones, y en cada vuelta aceptada la sumaba a lo aplicado.

**Comprobado con el motor real y `medicion.calibrar`:** con retardos aplicados de 0/0/0,
0/5/0 y 4/0/2 ms, las tres mediciones dieron las mismas correcciones, 9,00 / 4,50 / 0,00 ms
(±0,01). Alimentado diez veces con la misma medición, el controlador viejo llevó los retardos
a más del doble de lo que la sala necesitaba; con la mutación que reproduce ese error, el lazo
nuevo llegó a 88,5 ms para una sala de 9 ms (`test_arrival_loop.py`).

**Es la firma del exp. 09 §5:** *"las propuestas siguientes para Blue crecieron (+11,3 ms),
cuando con ganancia de lazo 0,5 y un objetivo estable tendrían que encogerse"*. Que eso
explique aquella sesión es INFERIDO: el registro no guardó las referencias.

**Con el nivel pasaba lo mismo:** la referencia va después de la ganancia, y la corrección de
nivel se volvía a sumar en cada vuelta. El lazo nuevo **no corrige niveles**. Hacerlo contra la
sala pisaría las ganancias que eligió el oyente o la calibración. El nivel queda para la
calibración, cuyo estímulo sale por la ganancia aplicada y sí mide un residuo.

### A. El estimador sobre el motor real

Las condiciones:
- música sintética del paso 1;
- los roles del paso 1, con Black y Blue con el mismo pan;
- la sala del paso 1 con retardos al azar de 2–30 ms y ganancias de −6 a 0 dB;
- ventanas de 4 s y sondas simultáneas.

Cada celda da el error de la llegada en ms: mediana · p95 · máximo de las válidas · rechazadas.
Son 8 ensayos por condición (n = 24 con 3 parlantes, 64 con 8), con la semilla 0 / la semilla 1.

| N | decorrelador | margen | semilla 0 | semilla 1 |
|---|---|---|---|---|
| 3 | sí | −20 dB | 0,002 · 0,004 · 0,007 · 0 | 0,002 · 0,005 · 0,006 · 0 |
| 3 | sí | −25 dB | 0,002 · 0,006 · 0,009 · 0 | 0,003 · 0,007 · 0,009 · 0 |
| 8 | sí | −20 dB | 0,003 · 0,009 · 0,011 · 0 | 0,003 · 0,011 · 0,011 · 0 |
| 8 | sí | −25 dB | 0,004 · 0,014 · 0,016 · 2 | 0,003 · 0,014 · 0,018 · 4 |
| 8 | no | −20 dB | 0,002 · 0,010 · 0,017 · 0 | 0,003 · 0,007 · 0,016 · 1 |
| 8 | no | −25 dB | 0,003 · 0,013 · 0,026 · 0 | 0,004 · 0,009 · 0,023 · 3 |

Y con la sala del paso 1 (Red, Black, Blue a 3,13 / 7,61 / 12,27 ms), sobre las mismas
grabaciones:
- **la sonda** erró a lo sumo 0,010 ms (24 mediciones por semilla, ninguna rechazada);
- **la música erró por más de 1 ms en el 50 % y el 33 %** de las mediciones, con p95 de
  4,50 ms: la distancia entre Black y Blue.

**Qué se lee:**
- **El producto reproduce el paso 1.** Las mediciones que fallan son pocas: a −25 dB con 8
  parlantes, 2 y 4 de 64; a −20 dB sin decorrelador, 1 de 64. **El estimador las rechazó
  todas, y ninguna medición aceptada erró.** Es lo que tiene que pasar: el lazo acepta por
  parlante y sigue con los demás.
- **Con 8 parlantes, −20 dB.** A −25 dB empiezan los rechazos, como en el exp. 16 §4.1.

### B. Una hora de deriva

Modelo de `tests/drift_room.py`, el del exp. 16 §4.2, pero con el lazo del producto:
- cada parlante con una latencia que deriva lineal, a un ritmo al azar dentro de ±22 o ±50 ppm;
- una medición cada 4 s de la ventana anterior, con un corrimiento común al azar en cada una;
- error de 0,005 ms, y la rampa de 0,5 ms/s de la línea de retardo.

La tabla da la dispersión (máx − mín) de retardo + latencia después de los primeros 10 minutos:
p95 · máximo, en ms, con la semilla 0 / la semilla 1.

| N | deriva | lazo nuevo | con 2 % de picos falsos dados por válidos | sin seguir la deriva |
|---|---|---|---|---|
| 3 | 22 ppm | 0,37 · 0,38 / 0,25 · 0,26 | 0,46 · 0,52 / 0,48 · 0,58 | 0,88 · 0,94 / 1,16 · 1,18 |
| 3 | 50 ppm | 0,40 · 0,41 / 0,32 · 0,33 | 0,46 · 0,57 / 0,48 · 0,68 | 1,41 · 1,47 / 1,96 · 2,17 |
| 8 | 22 ppm | 0,36 · 0,38 / 0,30 · 0,31 | 0,47 · 0,67 / 0,47 · 0,58 | 1,29 · 1,31 / 1,16 · 1,18 |
| 8 | 50 ppm | 0,32 · 0,33 / 0,31 · 0,31 | 0,47 · 0,79 / 0,48 · 0,77 | 1,81 · 2,01 / 1,78 · 2,15 |

**Qué se lee:**
- **El criterio de < 0,5 ms se cumple en todas las condiciones limpias**: p95 0,25–0,40 ms,
  máximo 0,41 ms. El exp. 16 llegaba a 0,50–0,78 ms con su prototipo, por la zona muerta por
  parlante.
- **Con picos falsos que el estimador no detectó** (el peor caso: en A no hubo ninguno), el p95
  sigue bajo 0,5 ms y el máximo llega a 0,79 ms. Los retiene la repetición.
- **Seguir la deriva es lo que lo sostiene:** sin ella, 0,88–2,17 ms.
- **Un límite que esto destapa (INFERIDO):** si la deriva fuera lineal y sin fin, en una hora
  a 50 ppm los retardos se separan **280–320 ms**. La línea del motor llega a 250 ms (o al
  doble del mayor retardo al arrancar), y más allá recorta. Con 22 ppm quedan 45–150 ms. Falta
  saber si con parlantes la latencia se resincroniza antes, con saltos que el lazo ya sigue, o
  si hay que agrandar la línea.

**En la sesión** (`test_session_probe.py`, sala de `simulated.py` sin deriva):
- con la sonda, 3 parlantes con decorrelador y 8 sin él pasan de 3–12 ms sin calibrar a
  ≤ 0,5 ms, y ahí se quedan los últimos 30 s de 90;
- se conserva el retardo de Haas de los traseros;
- contra la música, con contenido que se puede medir (2 parlantes, ruidos independientes), el
  lazo converge en vez de crecer.

### C. Calibrar 8 parlantes en dos grupos

`session.Calibration` del producto, con su línea de tiempo, sus tramos y la unión de los
grupos, sobre las salas del exp. 16 §3:
- retardos de 0 a 30 ms y ganancias de −8 a 0 dB;
- la coloración del Go 4, una cola de 0,4 s por parlante y ruido de micrófono de 0,001.

La verdad es el mismo estimador de nivel sobre cada parlante solo. **Repetido con dos juegos de
estímulos**: los del producto (semillas 0 y 1 de los grupos) y los mismos corridos en 10. La
semilla del estímulo es un parámetro que no debería importar.

Error de ganancia en dB: mediana · máximo · salas sobre 1 dB. Son 6 salas por celda, con la
semilla de sala 0 / la 1.

| | estímulo del producto | estímulo + 10 |
|---|---|---|
| **2 grupos × 10 s, 3 compartidos** (lo que hace ahora con más de 6) | 0,48 · 0,64 · 0 / 0,57 · 0,66 · 0 | 0,49 · 0,91 · 0 / 0,58 · 0,98 · 0 |
| 2 grupos × 10 s, 1 ancla (la propuesta del exp. 16) | 0,59 · 0,72 · 0 / 0,73 · 1,19 · **2** | 0 de 12 sobre 1 dB, máx. 0,88 |
| los 8 a la vez, 10 s | 0,60 · 0,74 · 0 / 1,28 · 1,50 · **4** | 0,82 · 1,53 · **2** / 0,74 · 1,67 · **1** |
| los 8 a la vez, 20 s | 0,40 · 0,61 · 0 / 0,76 · 1,13 · **2** | 0,53 · 0,95 · 0 / 0,59 · 0,71 · 0 |

El error de retardo, en todas: ≤ 0,007 ms. La fila de un ancla sale de la primera corrida
(`group_calibration.py` con un ancla) y de un script de comparación sobre las mismas salas.
Quedó fuera del JSON porque el código ya cambió.

**Qué se lee:**
- **Con un ancla, el segundo grupo se mueve entero**, por el error de nivel del propio ancla:
  en la sala que más falló, los cinco parlantes del grupo A tenían −0,8 a −1,2 dB y los del B,
  0 a −0,7. El 0 de 12 del exp. 16 venía con otros estímulos, y con los del producto no se
  repitió.
- **Con tres compartidos, el error de la unión se promedia: 0 de 24 sobre 1 dB**, con los dos
  juegos de estímulos. Cuesta lo mismo (2 × 10 s), y el grupo A queda de 6, que es el tope sin
  grupos.
- **Los 8 a la vez en 20 s** quedan en 2 de 24 sobre 1 dB, con el mismo tiempo. En 10 s, 7 de 24.

**Un error del propio probe, que conviene que quede escrito.** La primera corrida ubicaba la
referencia "exacta" de la verdad con la cuenta de los retardos de la sala. No contaba los 21 ms
de latencia del FIR de fase lineal que el estímulo atraviesa en la calibración. Con eso la
"verdad" caía fuera de la búsqueda de ±15 ms y daba errores de 2–7 dB que no eran del
producto. La verdad ahora ubica a cada parlante con GCC-PHAT sobre él solo.

### D. La coherencia y el error por tercio

`response_with_coherence` da por tercio la γ² y el error aleatorio (1σ) del nivel del tercio,
según Bendat y Piersol, con n = segmentos × bins × 0,5. Contra la dispersión real de 12 ruidos
independientes, entre 100 Hz y 8 kHz:

| | γ² mediana | error previsto a 100 Hz / 1 kHz / 8 kHz | dispersión real / prevista |
|---|---|---|---|
| 3 parlantes, 10 s | 0,51 | 0,37 / 0,11 / 0,05 dB | 0,61–1,49 |
| 3 parlantes, 5 s | 0,51 | 0,55 / 0,16 / 0,07 dB | 0,58–1,60 |
| 8 parlantes, 10 s | 0,20 | 0,79 / 0,23 / 0,09 dB | 0,63–1,50 |

**Qué se lee:**
- **La γ² sola no sirve de umbral** con varios parlantes. Para cada uno, los demás son ruido, y
  la γ² cae a ~1/N aunque la curva esté bien medida.
- **El campo para atenuar es el error** (`response_error_db`), que ya tiene en cuenta N y la
  duración. Un umbral de ~1 dB deja afuera, por ejemplo, los graves de una calibración corta
  con 8 parlantes.

### Lo que queda para los parlantes

1. **Paso 3, el retraso inyectado:** +5 ms en un parlante con la sonda encendida, encontrado y
   corregido dos veces con semillas distintas (`MaskedProbe(seed=…)`).
2. **Paso 4, la inaudibilidad:** el A/B ciego, con la sonda encendida y apagada en cada margen,
   con material crítico. Hasta entonces queda apagada por defecto. Con 8 parlantes, −25 dB ya
   da rechazos.
3. Lo que la simulación no tiene:
   - el SBC;
   - el piso de ruido del micrófono fifine;
   - cómo deriva de verdad cada Go 4: ¿lineal sin fin, o con saltos?;
   - la liberación sobre música real;
   - si la sonda se oye después de los finales.
4. **Revisar el registro del lazo con parlantes:** con el lazo nuevo, las propuestas no
   tendrían que crecer. Es la prueba de que el hallazgo explica el exp. 09 §5.

**Veredicto del paso 2:** hecho en simulación. La sonda, el lazo por parlante con seguimiento de
deriva, la calibración en grupos y la coherencia están en el producto, con tests que fallan si
se rompe cada pieza (mutaciones en `docs/roadmap.md`, i-7c8794-e3e40d). Siguen los pasos 3 y 4,
con parlantes.
