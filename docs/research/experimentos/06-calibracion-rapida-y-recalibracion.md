# 06 · Cómo calibrar rápido, y cómo recalibrar sin interrumpir

**Pregunta:** ¿qué estímulo y qué estimador dan los mejores parámetros en el menor tiempo,
para una calibración de 10 a 30 segundos antes de la sesión? ¿Y sirve el mismo mecanismo
para recalibrar **mientras suena la música**, sin interrupciones?

**Las marcas de este documento, que son dos.** La comparación de estrategias es
**SIMULADA**: los números salen de retardos conocidos, no de parlantes, y es a propósito —
con parlantes **nunca se conoce la respuesta correcta**, así que no se puede medir el error
de un estimador. La sección "Con parlantes de verdad" es **MEDIDA**, y ahí lo que se compara
no es el error sino la coincidencia entre mediciones independientes.

**Entorno:** `probes/calibracion/comparar.py`, 12 repeticiones por celda, cada una con una
respuesta de parlante y una sala distintas. La simulación incluye respuesta irregular del
parlante (pasabanda 150 Hz–12 kHz con ±6 dB de ondulación), reverberación de cinco ecos y
ruido de fondo. Fecha: 2026-09-28.

**Datos crudos:** [datos/06/comparacion-calibraciones.txt](datos/06/comparacion-calibraciones.txt).

## El resultado que decide

Error absoluto máximo entre parlantes, en ms. Retardos verdaderos: 0 / 7,302 / 23,094 ms,
elegidos **a media muestra** para que un estimador sin interpolación no acierte de
casualidad.

| estrategia | 2 s | 5 s | 10 s | 15 s | 30 s |
|---|---|---|---|---|---|
| **ruido rosa decorrelado + GCC-PHAT** | **0,00** | **0,00** | **0,00** | **0,00** | **0,00** |
| ruido decorrelado, correlación simple | 0,01 | 0,01 | 0,01 | 0,01 | 0,01 |
| **música decorrelada + GCC-PHAT** | **0,00** | **0,00** | **0,00** | **0,00** | **0,00** |
| ráfagas tonales + arranque de envolvente | 1,72 | 1,70 | 1,72 | 1,72 | 1,68 |

**Tres cosas se leen de esta tabla:**

1. **Dos segundos alcanzan.** Con ruido de banda ancha, alargar la medición **no mejora la
   precisión**. La pregunta "¿10 o 30 segundos?" tenía una respuesta más corta de lo
   esperado.
2. **El ruido de banda ancha es unas 170 veces más preciso que las ráfagas tonales.** No es
   un detalle de implementación: la resolución temporal de una medición de retardo va como
   **1 / ancho de banda**. Un tono filtrado a ±400 Hz da ~1,25 ms; ruido de 8 kHz de ancho
   da ~0,06 ms. Las ráfagas dan 1,70 ms, que es exactamente su límite teórico — y eso
   también sirve para confiar en que la simulación no está amañada.
3. **Con música da igual de bien que con ruido.** Es la validación del mecanismo de
   recalibración continua: **no hace falta interrumpir nada ni meter tonos de prueba**.

## Dónde se rompe cada uno

A 10 s, variando el ruido de sala relativo al pico de la señal.

| estrategia | 0,03 | 0,30 | 1,0 | 3,0 | 10 | 30 |
|---|---|---|---|---|---|---|
| ruido + GCC-PHAT | 0,00 | 0,00 | 0,00 | **0,00** | 16,3 | 169 |
| ruido, correlación simple | 0,01 | 0,01 | 0,01 | **0,01** | 214 | 99,6 |
| música + GCC-PHAT | 0,00 | 0,00 | 0,00 | **0,02** | 224 | 203 |
| ráfagas tonales | 1,57 | **25,3** | 23,1 | 23,1 | 23,1 | 23,1 |

- **Las ráfagas tonales se rompen con ruido de conversación** (0,30). Una vez rotas, el
  error se queda en ~23 ms, que es justo el retardo verdadero del tercer parlante: o sea
  que el estimador simplemente devuelve cero y deja de medir.
- **El ruido de banda ancha aguanta ruido de sala tan fuerte como la señal**, y todavía
  anda con ruido tres veces más fuerte.
- **PHAT se gana su lugar en el extremo**: con ruido 10 el blanqueado da 16 ms de error y
  la correlación simple da 214. En condiciones normales los dos empatan, así que **PHAT no
  es lo que hace funcionar esto**; es el margen extra.

## Dos defectos propios que aparecieron al probar

**El umbral de confianza no detectaba sus propios fallos, y después resultó inservible.**
Empezó en 3; con ruido 30 el estimador daba confianza 5,8 —"confiable"— y 256 ms de error.
Se subió a **15** con el barrido en simulación, donde una medición buena daba confianza
**500**. Después se midió en una sala de verdad, donde una medición **buena** da confianza
**12**, y el umbral de 15 descartaba todo. La simulación tenía la reverberación demasiado
suave y su escala de confianza no se parece a la real.

Quedó en **8**, y sobre todo **dejó de ser el criterio de calidad**: la confianza dice si hay
un pico, no si el pico es el correcto (ver "Falla 2" más abajo).

**El estimador de nivel suponía ortogonalidad perfecta.** Proyectaba el micrófono sobre cada
referencia por separado: `g = <micro, s_i> / <s_i, s_i>`. Con ganancias verdaderas de
1,0 / 0,5 / 0,25 devolvía **1,38 en vez de 1,0**, un 38 % de error. Los filtros del banco son
*casi* ortogonales, no exactamente, y la correlación residual se colaba. Se reemplazó por
mínimos cuadrados **conjuntos** —resolver el sistema con todas las referencias a la vez—, y
el error desapareció. Lo encontró un test, no una escucha.

## La calibración que sale de esto

*(Esta receta ya incorpora las correcciones que trajo la medición con parlantes, más abajo.)*

**Estímulo:** ruido rosa **independiente** por parlante —no decorrelado, ver "Falla 2"—, con
rampas de medio segundo en los extremos. Las rampas no son cosmética: el golpe de un arranque
abrupto lo comprime el parlante, y esa compresión corre el tiempo de llegada aparente justo
al principio.

**Duración: 10 segundos, medidos como 5 ventanas de 2.** Dos segundos ya dan toda la
precisión; los otros ocho compran **poder comparar mediciones independientes**, y con eso una
**mediana** que descarta una ventana arruinada por un portazo o un bache del stream.

**Criterio de validez: el residuo de reconstrucción**, no la coincidencia entre ventanas. Se
rearma lo que tendría que haber captado el micrófono con los retardos y ganancias estimados y
se compara con lo que captó. La coincidencia entre ventanas es necesaria pero no suficiente:
pueden coincidir todas en el mismo valor equivocado.

**Qué devuelve, sin que nadie escriba un número:** el retardo y la ganancia de cada
parlante. El nivel sale de la misma grabación, por mínimos cuadrados.

**Y por qué no hace falta medir distancias con cinta:** la medición devuelve el **retardo
total** de cada parlante, que ya incluye el vuelo por el aire. Para igualar los tiempos de
llegada eso es todo lo que se necesita. Las coordenadas solo harían falta para panear con
DBAP, que es un paso posterior, y por eso en `config.py` son opcionales.

## La recalibración continua, y por qué el mecanismo es el mismo

La clave es que **el estimador no necesita un estímulo especial: necesita saber qué se le
mandó a cada parlante**. Y el sistema ya le manda a cada uno una señal **distinta**, porque
la decorrelación es lo que produce el envolvimiento
([09](../09-efecto-ambiental-y-diseno-de-la-experiencia.md) §2).

**Con una salvedad que apareció midiendo:** acá las señales son versiones *decorreladas* de
la misma obra, no independientes, así que sí están expuestas a la contaminación entre canales
de la "Falla 2". Lo que la contiene es que **la calibración inicial ya igualó los niveles**, y
la fuga aparece justamente cuando los niveles son desparejos. Los dos pasos se sostienen
mutuamente, y el residuo avisa si deja de alcanzar.

Entonces, mientras suena la música:

```
música → decorrelador por parlante → cada parlante recibe su propia señal, conocida
                                          │
micrófono ───────────────────────────────┴─► GCC-PHAT contra cada una → retardos
```

La fila "música + GCC-PHAT" de la primera tabla dice que esto funciona igual de bien que
con ruido. **La condición del efecto es la condición de la medición.**

Lo que eso permite:
- **sin interrupciones**: no hay que meter tonos ni bajar el volumen;
- **sin reiniciar los streams**: solo se ajustan retardo y ganancia, que son parámetros de
  una etapa del grafo, no de la conexión Bluetooth;
- **sin que se note**: corregir unos milisegundos de retardo es inaudible si se hace
  gradualmente.

**Lo que todavía no está resuelto:** con qué frecuencia y con qué ganancia de lazo ajustar.
Eso depende de cuánto drift haya, y el drift **sigue sin medirse** (ver abajo).

## Con parlantes de verdad: MEDIDO, y encontró dos fallas del diseño

Tres grabaciones de 10 s con los 3 Go 4 (el Tune desconectado, para no ser un cuarto stream
A2DP). **Con parlantes no se conoce la respuesta correcta**, así que lo que se compara es la
**dispersión entre las cinco ventanas**.

| grabación | Black | Red | Blue | dispersión |
|---|---|---|---|---|
| 50 %, amplitud 0,4 | +0,00 | +1,56 | +0,69 | 0,00 / **2,31** / 0,78 |
| 75 %, amplitud 0,7 (1ª) | +0,00 | −3,94 | −7,45 | **0,02 / 0,07 / 0,00** |
| 75 %, amplitud 0,7 (2ª) | +0,00 | −4,42 | −2,99 | 1,05 / 0,00 / 2,11 |

**La mejor corrida dio 0,07 ms de acuerdo entre cinco ventanas independientes**, contra los
0,25–0,68 ms de MAD del método de ráfagas tonales. La receta funciona.

**Y confirma otra vez la variación entre arranques:** Blue da −7,45 ms en una corrida y
−2,99 en la siguiente, con los parlantes sin tocar y las dos corridas seguidas. Es el mismo
fenómeno de [05](05-e6-a2dp-un-canal-por-parlante.md): **cada reproducción nueva trae su
propio corrimiento**, así que la calibración vale para el stream en el que se hizo.

### Falla 1: la alineación gruesa se equivocaba en silencio
Antes de medir hay que alinear la grabación con las referencias, porque la grabación arranca
antes y el camino A2DP mete ~1 s de buffer. Ese paso, estimado con un solo canal, devolvió
**59 ms donde el valor era 985**, y todo lo que venía después quedó sin sentido.

**El arreglo usa información que ya estaba ahí:** los tres canales salen del mismo instante,
así que tienen que dar el mismo desfase grueso. Ahora se estima con todos, se toma la
mediana y **se rechaza si no concuerdan** dentro de 20 ms. Con eso, la corrida que había
fallado pasó a estimar 1041 ms, que es plausible.

### Falla 2, la grave: las referencias decorreladas se contaminan entre sí
Los tres parlantes no suenan al mismo nivel (los niveles medidos iban de 0,0022 a 0,0095, un
factor de 4). Con niveles desparejos, **la fuga del parlante fuerte dentro de la correlación
del flojo puede dominar**, y la medición devuelve el retardo equivocado.

Barrido en simulación, con referencias decorreladas contra referencias independientes:

| niveles | referencias decorreladas | referencias independientes |
|---|---|---|
| parejos (1 / 1 / 1) | 0,011 ms | 0,011 ms |
| desparejos (1 / 0,5 / 0,25) | **22,6 ms** | **0,011 ms** |
| muy desparejos (1 / 0,3 / 0,1) | **22,7 ms** | **0,011 ms** |

**Y lo peor: en todos esos casos la dispersión entre ventanas daba 0,000 ms.** O sea que el
estimador informaba una calibración perfecta mientras se equivocaba por 22 ms.

**Dos consecuencias de diseño:**

1. **El estímulo de calibración usa ruido independiente por parlante, no decorrelado.** Para
   calibrar no hay ninguna razón para que todos reciban el mismo material. La recalibración
   con música sí tiene que convivir con el problema, porque ahí las señales son versiones
   decorreladas de la misma obra — pero **la calibración inicial iguala los niveles**, que es
   justo lo que reduce la fuga, así que los dos pasos se ayudan.
2. **La coincidencia entre ventanas dejó de ser el criterio de validez.** Es necesaria pero
   no suficiente: varias ventanas pueden encontrar **el mismo** pico equivocado. El criterio
   pasó a ser el **residuo de reconstrucción** (`medicion.residuo_relativo`): se rearma lo
   que tendría que haber captado el micrófono con los retardos y ganancias estimados, y se
   compara con lo que captó. Un retardo equivocado no reconstruye la grabación, por mucho que
   las ventanas se pongan de acuerdo.

### Una inferencia mía que estaba mal
Al ver que la prueba a más volumen daba confianzas más bajas, atribuí la caída a la
**compresión interna del parlante**. Era una conjetura sin verificar y era falsa: la corrida
fuerte que "falló" no falló por compresión sino por la alineación gruesa, y la otra corrida
fuerte dio **el mejor resultado de las tres**. Queda anotado porque el error fue de método:
inferí una causa física de un único número indirecto.

### Sobre el volumen
El usuario pidió no subir el volumen. **No hace falta:** la corrida bajita ya daba bien en
dos de tres parlantes, y el que fallaba lo hacía por la contaminación entre referencias, no
por nivel. Con ruido independiente, ese problema no existe. Los volúmenes se restauraron a
como estaban (50 / 50 / 57 %).

## Segunda vuelta con parlantes: el arreglo funciona, y cayeron dos criterios más

Se repitió con **ruido independiente** y al volumen normal (50 / 50 / 57 %), dos corridas de
10 s. El criterio de comparación pasó a ser **la estabilidad del resultado al cambiar el
tamaño de la ventana de análisis**: si el número se mueve cuando se cambia un parámetro que
no debería importar, el número no vale.

| grabación | Black | Red | Blue |
|---|---|---|---|
| decorrelado (primera vuelta) | 0,00 | **5,45 ms** | 0,19 |
| independiente, corrida 1 | 0,00 | 1,42 | 0,66 |
| independiente, corrida 2 | 0,00 | **0,14 ms** | 0,42 |

**Red pasó de moverse 5,45 ms a moverse 0,14 ms.** El arreglo del estímulo era el correcto.

Y los retardos quedan consistentes entre corridas distintas: **Red +0,80 y +0,79 ms**.

### Cayó el criterio de la confianza, definitivamente
Se probaron umbrales de 3, 15 y 8, y ninguno servía. Lo que lo liquidó fue medir el otro
extremo: correlacionando **ruido puro** contra una referencia ausente, la confianza da
**5,8 a 7,4**; las mediciones reales **buenas** daban **6,4 a 16**. **Los rangos se
superponen**, así que no existe un umbral que separe "no hay señal" de "hay señal débil".

Quedó desactivada y documentada como diagnóstico. Lo que sí detecta un parlante que no suena
es el **nivel**, comparado con el de los demás.

### Cayó también el criterio del residuo
El residuo de reconstrucción daba **0,999** sobre una estimación que después resultó
correcta. No es un error del criterio sino de la física: en una sala, el micrófono capta
mucho más campo reverberante que sonido directo, y el modelo solo tiene el directo. **El
residuo no sirve en una sala**, aunque en la simulación —con reverberación suave— parecía
razonable.

### El estimador de nivel también hubo que cambiarlo
Por mínimos cuadrados en el tiempo daba resultados **contradictorios entre corridas de la
misma sala sin mover nada**: Red salía −16,44 dB en una y 0,00 en la otra. Exige alineación
con precisión de muestra —medio milisegundo son 24 muestras— y además queda mal condicionado
cuando la reverberación domina.

Se reemplazó por la **energía del filtro adaptado en una ventana alrededor del pico, con
resta de línea de base**. Entre las mismas dos corridas: **0,3 dB de diferencia** en vez de
16. Y la resta de base mejora también el sesgo hacia los canales flojos: con ganancias reales
de 0,5 y 0,25, el estimador pasó de devolver 0,568 y 0,310 a devolver 0,538 y 0,242.

### Y la regla de consenso hubo que aflojarla
La alineación gruesa exigía que **todos** los canales coincidieran. En una medición real, dos
daban 960,4 y 958,0 ms —el valor correcto— y el tercero, el más flojo, daba 116,2: la
unanimidad descartaba la medición entera por culpa del peor canal. Ahora se toma el **grupo
más grande que concuerda**, con al menos dos canales.

### Los tres criterios, y cuál sobrevivió

| criterio | qué detecta | veredicto |
|---|---|---|
| confianza del pico | que hay *un* pico | **descartado**: se superpone con el ruido |
| coincidencia entre ventanas iguales | ruido aleatorio | **insuficiente**: coinciden en el mismo error |
| residuo de reconstrucción | que el modelo explica la grabación | **inútil en sala**: la reverb domina |
| **estabilidad entre tamaños de ventana** | que el resultado no depende de un parámetro arbitrario | **el que quedó** |
| **nivel relativo entre parlantes** | que un parlante no está sonando | **el que quedó** |

## Lo que falta

1. **Medir cuánto varía la calibración entre arranques de reproducción.** Con dos corridas,
   Red coincidió (+0,80 y +0,79) pero Blue no (+0,48 y +0,02). Hacen falta más corridas para
   saber si una calibración sirve para la sesión entera o hay que rehacerla en cada tema.
2. **Repetir la medición de drift con este estímulo.** La corrida de 30 minutos que se hizo
   con ráfagas tonales quedó inservible, justamente por el problema que este documento
   explica ([05](05-e6-a2dp-un-canal-por-parlante.md)).
3. **Revisar el umbral de confianza** con datos reales.
4. Decidir la frecuencia y la ganancia del lazo de recalibración, que dependen del drift.

---

**Nota del 2026-10-01 (después de este experimento).** Lo que aquí se dice de que "la
calibración inicial iguala los niveles" descansaba en `medicion.niveles`, y ese estimador
tenía dos errores que destapó la calibración del panel simulado: dividía por la energía de
la referencia (con ruido rosa, hasta **11,4 dB** de error según qué realización le tocaba a
cada parlante) y perdía al parlante que llega antes que la mediana. Sus 0,3 dB entre
corridas eran repetibilidad con la misma semilla, no exactitud. Las ganancias que escribió
`calibrate` antes de esa fecha no se deben tomar como medidas. Detalle y corrección en
[10](10-servicio-de-control-con-3-go-4.md) §3.
